"""The local analysis service behind the desktop window.

This is not a web server in the usual sense. It listens on the loopback
interface only, on an ephemeral port chosen at launch, behind a random
per-session token. Nothing outside this process -- not another application, not
a browser someone opens by hand -- can reach it without that token, which is
never written to disk and dies with the window.

It exists because the globe is WebGL, and the cleanest way to hand a WebView
its data is HTTP. It is an implementation detail of the application, not a
service anyone is meant to connect to.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import __version__, firstrun
from .config import Config, load_config
from .paths import apply_model_cache_env, read_root, state_dir
from .pipeline import AnalysisEngine, ImageRejected

log = logging.getLogger("mapify.server")

WEB_DIR = Path(__file__).resolve().parent / "web"
ROOT = read_root()

COOKIE_NAME = "mapify_session"

# Everything the window needs comes from this origin. The single exception the
# operator can switch on is raster basemap tiles, and the policy widens only
# when they do.
_CSP_BASE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


def _csp(cfg: Config, nonce: str) -> str:
    """Build the policy for one response.

    The page carries exactly one inline script -- the ES module import map,
    which by definition cannot live in a file. Chromium does not honour CSP
    hashes for import maps, only nonces, so each response gets a fresh nonce
    rather than the policy being opened up with 'unsafe-inline'.
    """
    if cfg.network.basemap_tiles_enabled:
        img = "img-src 'self' data: blob: https:; "
        connect = "connect-src 'self' https:; "
    else:
        img = "img-src 'self' data: blob:; "
        connect = "connect-src 'self'; "
    policy = _CSP_BASE.replace("default-src 'self'; ", "default-src 'self'; " + img)
    policy = policy.replace("connect-src 'self'; ", connect)
    return policy.replace("script-src 'self'", f"script-src 'self' 'nonce-{nonce}'")


def create_app(cfg: Config | None = None, token: str | None = None,
               bridge: Any = None) -> FastAPI:
    """Build the service.

    :param token: when set, every request must present it -- as ``?t=`` on the
        first page load, then as a session cookie, or as an X-Mapify-Token
        header. The desktop shell always sets one.
    :param bridge: the desktop bridge, when running as an application. It owns
        the set of file paths the user chose in a native dialog, which is the
        only way a local path may be read.
    """
    cfg = cfg or load_config()
    engine = AnalysisEngine(cfg)

    app = FastAPI(
        title="Mapify",
        version=__version__,
        description="Image geolocation reconnaissance (local analysis service).",
        docs_url=None if token else "/api/docs",
        redoc_url=None,
        openapi_url=None if token else "/api/openapi.json",
    )
    app.state.cfg = cfg
    app.state.engine = engine
    app.state.token = token
    app.state.bridge = bridge

    @app.middleware("http")
    async def gate(request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        authorised = token is None
        set_cookie = False
        if not authorised:
            supplied = (
                request.headers.get("X-Mapify-Token")
                or request.cookies.get(COOKIE_NAME)
                or request.query_params.get("t")
            )
            if supplied and secrets.compare_digest(supplied, token):
                authorised = True
                set_cookie = request.cookies.get(COOKIE_NAME) != token

        if not authorised:
            return PlainTextResponse(
                "Mapify's analysis service is private to the running "
                "application. There is nothing to see here.",
                status_code=403,
            )

        response = await call_next(request)
        if set_cookie:
            response.set_cookie(
                COOKIE_NAME, token,
                httponly=True, samesite="strict", path="/",
            )
        response.headers["Content-Security-Policy"] = _csp(cfg, nonce)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=(), interest-cohort=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        # The window ships with its own assets and the port changes every
        # launch, so there is nothing to gain from caching and a stale
        # stylesheet to lose.
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    # ------------------------------------------------------------- window

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        # Rendered here rather than served by StaticFiles so the CSP nonce is
        # never handed out unsubstituted.
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        nonce = getattr(request.state, "csp_nonce", "")
        return HTMLResponse(html.replace("__CSP_NONCE__", nonce))

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    # --------------------------------------------------------------- api

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        """What is loaded, what is reachable, and what is switched off."""
        return {
            "version": __version__,
            "engine": engine.engine_status(),
            "client": {
                "offline_only": cfg.network.offline_only,
                "basemap_tiles_enabled": cfg.network.basemap_tiles_enabled,
                "basemap_tile_url": (
                    cfg.network.basemap_tile_url
                    if cfg.network.basemap_tiles_enabled else None
                ),
                "basemap_attribution": cfg.network.basemap_attribution,
                "max_upload_mb": round(cfg.server.max_upload_bytes / 1e6, 1),
                "textures_present": (WEB_DIR / "textures" / "earth_day.jpg").exists(),
                "candidates_returned": cfg.fusion.candidates_returned,
                "native": bridge is not None,
            },
        }

    @app.post("/api/warmup")
    async def warmup() -> dict[str, Any]:
        """Load the models now rather than on the first analysis."""
        return await asyncio.to_thread(engine.warmup)

    @app.post("/api/home")
    async def home_view(payload: dict[str, Any]) -> dict[str, Any]:
        """Roughly where the user is, from their clock. Nothing else.

        The globe opens somewhere, and opening it over the user's own part
        of the world -- correctly lit for the actual time -- is better than
        opening it over the mid-Atlantic. The question is how to know where
        that is without asking for something we have no business having.

        Not IP geolocation: that sends the user's address to a third party
        to learn something we only want for a camera angle.
        Not the browser's geolocation API: that is a permission prompt for
        a decoration, and a precise fix for a use that needs none.

        The system timezone is already on the machine, needs no permission
        and leaves no network trace. Its name carries a city -- Kolkata,
        Nairobi, Sao_Paulo -- and the bundled gazetteer knows where that
        city is. When the name is not a place, or is not in the gazetteer,
        the UTC offset still gives a longitude to a few degrees.

        This is the timezone's namesake city, not the user. Anyone in
        Chennai gets Kolkata, and that is the correct amount of precision
        for pointing a camera at a planet.
        """
        tz_name = str(payload.get("timezone") or "").strip()
        try:
            offset_minutes = float(payload.get("utc_offset_minutes") or 0.0)
        except (TypeError, ValueError):
            offset_minutes = 0.0

        # Longitude from the offset: 15 degrees an hour, always available.
        lon = max(-180.0, min(180.0, offset_minutes / 60.0 * 15.0))
        lat = 22.0                      # a mid-northern default
        source = "utc-offset"
        label = None

        city = tz_name.rsplit("/", 1)[-1].replace("_", " ").strip()
        if city and city.lower() not in {"utc", "gmt", "unknown"}:
            try:
                matches = engine.geocoder.find_place(city, limit=1)
            except Exception:                       # pragma: no cover
                matches = []
            if matches:
                hit = matches[0]
                lat, lon = hit.latitude, hit.longitude
                source = "timezone-city"
                label = hit.name

        return {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "source": source,
            "timezone": tz_name or None,
            "city": label,
            "note": (
                "Derived from this computer's timezone. It is the "
                "timezone's namesake city, not your location, and nothing "
                "left the machine to work it out."
            ),
        }

    @app.get("/api/firstrun")
    async def firstrun_status() -> dict[str, Any]:
        """What the first launch will cost, answered before it is spent.

        Deliberately cheap -- it reads directory entries and imports nothing
        heavy -- because the screen that consumes it has to appear while the
        window is still opening, not after PyTorch has finished importing.
        """
        return firstrun.status()

    @app.post("/api/firstrun/defer")
    async def firstrun_defer(payload: dict[str, Any]) -> dict[str, Any]:
        """Remember that the user said not yet, or that they changed their mind."""
        firstrun.set_deferred(bool(payload.get("deferred", True)))
        return {"deferred": firstrun.is_deferred()}

    @app.post("/api/warmup/stream")
    async def warmup_stream() -> StreamingResponse:
        """Warm up, narrating what is happening while it happens.

        Two sources of truth are merged here. The engine calls back as each
        stage begins, which is the only way to tell "downloading" from
        "encoding"; and a poll of the Hugging Face cache directory supplies
        the byte count, which is the only part a person can judge progress by.
        Neither alone is enough: a stage name with no number looks hung, and a
        number with no stage stops moving for a minute when the download ends
        and the gallery encode starts.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        state = {"stage": "starting", "message": "Starting"}

        def emit(payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, json.dumps(payload))

        def progress(key: str, message: str) -> None:
            state["stage"] = key
            state["message"] = message
            emit({"type": "stage", "stage": key, "message": message})

        rate = firstrun.Rate()
        began = time.time()

        async def poll() -> None:
            """Report bytes while the download stage is the one running."""
            while True:
                await asyncio.sleep(0.5)
                if state["stage"] not in ("starting", "locator"):
                    continue
                seen = await asyncio.to_thread(firstrun.watch)
                speed = rate.update(seen["bytes"], time.time())
                remaining = max(0, seen["total"] - seen["bytes"])
                emit({
                    "type": "download",
                    "bytes": seen["bytes"],
                    "total": seen["total"],
                    "fraction": seen["fraction"],
                    "bytes_per_second": round(speed),
                    "eta_s": round(remaining / speed) if speed > 1000 else None,
                })

        async def run() -> None:
            poller = loop.create_task(poll())
            try:
                status = await asyncio.to_thread(engine.warmup, progress)
                firstrun.set_deferred(False)
                await queue.put(json.dumps({
                    "type": "done",
                    "status": status,
                    "elapsed_s": round(time.time() - began, 1),
                }))
            except Exception as exc:  # pragma: no cover - guard
                log.exception("warmup failed")
                await queue.put(json.dumps({
                    "type": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "stage": state["stage"],
                }))
            finally:
                poller.cancel()
                await queue.put(None)

        task = loop.create_task(run())

        async def body():
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item + "\n"
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            body(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    def _run(data: bytes, filename: str | None) -> Response:
        try:
            report = engine.analyse(data, filename)
        except ImageRejected as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        if cfg.server.keep_uploads:
            _debug_dump(data, filename)
        return JSONResponse(report.as_dict(), status_code=200 if report.ok else 422)

    @app.post("/api/analyse")
    async def analyse(image: UploadFile = File(...)) -> Response:
        data = await image.read()
        _check_size(cfg, len(data))
        return await asyncio.to_thread(_run, data, image.filename)

    def _read_approved(path_str: str) -> tuple[bytes, str]:
        """Read a file the user chose in a native dialog.

        The path must be one the bridge handed out. Without that check this
        would be an arbitrary-file-read endpoint, token or no token.
        """
        if bridge is None:
            raise HTTPException(
                status_code=400,
                detail="Local file analysis is only available in the desktop app.",
            )
        resolved = str(Path(path_str).resolve())
        if resolved not in bridge.approved_paths:
            raise HTTPException(
                status_code=403,
                detail=(
                    "That file was not chosen through the application's file "
                    "dialog."
                ),
            )
        p = Path(resolved)
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _check_size(cfg, len(data))
        return data, p.name

    @app.post("/api/analyse/local")
    async def analyse_local(payload: dict[str, str]) -> Response:
        data, name = _read_approved(payload.get("path", ""))
        return await asyncio.to_thread(_run, data, name)

    # Streaming variants: the window's progress card reflects the stage the
    # pipeline is actually in, rather than an animation that guesses.
    def _stream(data: bytes, filename: str | None) -> StreamingResponse:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(key: str, message: str) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                json.dumps({"type": "progress", "stage": key, "message": message}),
            )

        async def run() -> None:
            try:
                if cfg.server.keep_uploads:
                    _debug_dump(data, filename)
                report = await asyncio.to_thread(
                    engine.analyse, data, filename, progress
                )
                await queue.put(
                    json.dumps({"type": "result", "report": report.as_dict()})
                )
            except Exception as exc:  # pragma: no cover - guard
                log.exception("analysis failed")
                await queue.put(json.dumps({
                    "type": "result",
                    "report": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                }))
            finally:
                await queue.put(None)

        task = loop.create_task(run())

        async def body():
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item + "\n"
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            body(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/analyse/stream")
    async def analyse_stream(image: UploadFile = File(...)) -> StreamingResponse:
        data = await image.read()
        _check_size(cfg, len(data))
        return _stream(data, image.filename)

    @app.post("/api/analyse/local/stream")
    async def analyse_local_stream(payload: dict[str, str]) -> StreamingResponse:
        data, name = _read_approved(payload.get("path", ""))
        return _stream(data, name)

    # ------------------------------------------------------------- batch
    #
    # One batch at a time. The engine is a single CPU-bound pipeline; running
    # two concurrently would halve the speed of both and make the progress
    # meaningless.
    # --------------------------------------------------- place sessions
    #
    # One at a time, and held in the process rather than handed to the
    # client. The session holds decoded image embeddings and candidate
    # pools -- megabytes per photograph -- and round-tripping that
    # through the browser to make the service stateless would cost more
    # than the statelessness is worth for a single-user desktop tool.
    session_state: dict[str, Any] = {"session": None}

    @app.post("/api/session/start")
    async def session_start() -> dict[str, Any]:
        """Begin collecting photographs the user says are of one place."""
        from .session import PlaceSession

        session_state["session"] = PlaceSession(engine)
        return {"started": True, "counted": 0}

    @app.post("/api/session/add")
    async def session_add(payload: dict[str, Any]) -> dict[str, Any]:
        """Add one more photograph, and re-combine everything so far."""
        from .session import PlaceSession

        current = session_state.get("session")
        if current is None:
            current = session_state["session"] = PlaceSession(engine)

        data, name = _read_approved(payload.get("path", ""))
        _check_size(cfg, len(data))

        # The combination is enriched like any other result: it is the
        # answer now, and an answer without a place name is half of one.
        return await asyncio.to_thread(_session_add, current, data, name)

    @app.post("/api/session/add/upload")
    async def session_add_upload(image: UploadFile = File(...)) -> dict[str, Any]:
        """Add a photograph by upload rather than by path.

        The same pair as the analysis endpoints: the desktop app hands
        over a path the file dialog approved, and anything else posts
        the bytes. Without this the feature would exist only in the
        packaged application and could not be exercised anywhere else.
        """
        from .session import PlaceSession

        current = session_state.get("session")
        if current is None:
            current = session_state["session"] = PlaceSession(engine)

        data = await image.read()
        _check_size(cfg, len(data))
        return await asyncio.to_thread(
            _session_add, current, data, image.filename
        )

    def _session_add(current: Any, data: bytes,
                     name: str | None) -> dict[str, Any]:
        obs = current.add(data, name)
        combined = current.combine()
        out: dict[str, Any] = {
            "observation": obs.as_dict(),
            "observations": [o.as_dict() for o in current.observations],
            "counted": len(current.counted()),
            "combined": None,
        }
        if combined is not None:
            report = engine.report_for_fusion(combined, name)
            out["combined"] = report
            confidence = (report.get("prediction") or {}).get("confidence")
            if current.should_ask_for_more(confidence or "insufficient"):
                out["ask"] = current.ask(confidence or "insufficient")
        return out

    @app.post("/api/session/end")
    async def session_end() -> dict[str, Any]:
        """The user has no more photographs. Give what there is."""
        current = session_state.get("session")
        session_state["session"] = None
        return {"ended": True,
                "counted": len(current.counted()) if current else 0}

    batch_state: dict[str, Any] = {"runner": None}

    @app.post("/api/batch/stream")
    async def batch_stream(payload: dict[str, Any]) -> StreamingResponse:
        from .batch import BatchRunner

        paths = payload.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise HTTPException(status_code=400, detail="No paths given.")
        if batch_state["runner"] is not None:
            raise HTTPException(
                status_code=409,
                detail="A batch is already running. Cancel it first.",
            )

        # Same rule as a single analysis: only files the native dialog
        # handed out.
        approved: list[str] = []
        for raw in paths:
            resolved = str(Path(str(raw)).resolve())
            if bridge is None:
                raise HTTPException(
                    status_code=400,
                    detail="Batch analysis is only available in the desktop app.",
                )
            if resolved not in bridge.approved_paths:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "One or more files were not chosen through the "
                        "application's folder dialog."
                    ),
                )
            approved.append(resolved)

        runner = BatchRunner(engine)
        batch_state["runner"] = runner

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, json.dumps(event))

        async def run() -> None:
            try:
                await asyncio.to_thread(runner.run, approved, emit)
            except Exception as exc:  # pragma: no cover - guard
                log.exception("batch failed")
                await queue.put(json.dumps({
                    "type": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }))
            finally:
                batch_state["runner"] = None
                await queue.put(None)

        task = loop.create_task(run())

        async def body():
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item + "\n"
            finally:
                if not task.done():
                    runner.cancel()

        return StreamingResponse(
            body(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/batch/cancel")
    async def batch_cancel() -> dict[str, Any]:
        runner = batch_state.get("runner")
        if runner is None:
            return {"cancelled": False, "reason": "no batch running"}
        runner.cancel()
        return {"cancelled": True}

    @app.post("/api/report/html")
    async def report_html(payload: dict[str, Any]) -> Response:
        """Render an analysis as a standalone HTML document.

        The source image is read from the path the native dialog approved, so
        it can be inlined without the client shipping the bytes back.
        """
        from .report import build_batch, build_single

        report = payload.get("report")
        if not isinstance(report, dict):
            raise HTTPException(status_code=400, detail="No report given.")

        if report.get("type") == "complete" or "clusters" in report:
            html = build_batch(report, engine.attribution_lines())
        else:
            image_bytes = None
            mime = "image/jpeg"
            path = payload.get("path")
            if path and bridge is not None:
                resolved = str(Path(str(path)).resolve())
                if resolved in bridge.approved_paths:
                    try:
                        image_bytes = Path(resolved).read_bytes()
                        suffix = Path(resolved).suffix.lower()
                        mime = {
                            ".png": "image/png", ".webp": "image/webp",
                        }.get(suffix, "image/jpeg")
                    except OSError:
                        image_bytes = None
            html = build_single(report, image_bytes, mime)

        return Response(content=html, media_type="text/html; charset=utf-8")

    @app.get("/api/attribution")
    async def attribution() -> dict[str, Any]:
        path = ROOT / "ATTRIBUTION.md"
        return {
            "summary": engine.attribution_lines(),
            "full_text": path.read_text(encoding="utf-8") if path.exists() else None,
        }

    # -------------------------------------------------------- static last

    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=False), name="web")
    return app


def _debug_dump(data: bytes, filename: str | None) -> None:
    """Keep a copy of an upload. Debugging only, off by default.

    The normal path holds an upload in memory and drops it when the request
    ends; this exists so a misbehaving image can be reproduced, and the config
    comment says as much.
    """
    import time

    out = state_dir() / "uploads"
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in (filename or "upload") if c.isalnum() or c in "._-")
    (out / f"{int(time.time())}_{safe[:80]}").write_bytes(data)


def _check_size(cfg: Config, size: int) -> None:
    if size > cfg.server.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Image is {size / 1e6:.1f} MB; the limit is "
                f"{cfg.server.max_upload_bytes / 1e6:.0f} MB."
            ),
        )


def main(argv: list[str] | None = None) -> int:
    """Headless mode: run the service without the desktop window.

    Kept for development, and for running the analysis on a machine you reach
    over SSH. The normal way to use Mapify is the application window --
    ``python -m mapify``, or Mapify.exe once built.
    """
    # Same as the desktop entry point, and for the same reason: this must
    # happen before anything imports huggingface_hub, which reads these
    # variables once at import time. Both ways of starting Mapify have to
    # look for the weights in the same place, or one of them re-downloads
    # 1.6 GB the other already has.
    apply_model_cache_env()

    ap = argparse.ArgumentParser(
        prog="mapify-service",
        description="Run the Mapify analysis service without the app window.",
    )
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--warmup", action="store_true")
    ap.add_argument("--token", default=None,
                    help="require this token on every request")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.config)
    if args.offline:
        cfg.network.offline_only = True
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port

    app = create_app(cfg, token=args.token)

    if args.warmup:
        log.info("loading models (the first run downloads ~1.7 GB of weights)")
        for name, info in app.state.engine.warmup().items():
            log.info("  %-18s %s", name, info)

    import uvicorn

    suffix = f"/?t={args.token}" if args.token else "/"
    print(
        f"\n  Mapify service {__version__}\n"
        f"  http://{cfg.server.host}:{cfg.server.port}{suffix}\n"
        f"  offline   {'yes' if cfg.network.offline_only else 'no'}\n",
        file=sys.stderr,
    )
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port,
                log_level=args.log_level, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
