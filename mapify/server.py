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
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import __version__, firstrun, heif
from .config import Config, load_config
from .paths import apply_model_cache_env, cache_dir, read_root, state_dir
from .pipeline import AnalysisEngine, ImageRejected

log = logging.getLogger("mapify.server")


class _PinpointCancelled(Exception):
    """The page that asked for a Pinpoint search has gone away."""


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
        # Only the configured tile host, not all of https. Ground imagery
        # is one known origin; opening the policy to every origin on the
        # internet to reach it would give away far more than it buys, and
        # the whole point of a content policy is that it names things.
        origin = _tile_origin(cfg.network.basemap_tile_url)
        img = f"img-src 'self' data: blob: {origin}; " if origin else               "img-src 'self' data: blob:; "
        connect = f"connect-src 'self' {origin}; " if origin else                   "connect-src 'self'; "
    else:
        img = "img-src 'self' data: blob:; "
        connect = "connect-src 'self'; "
    policy = _CSP_BASE.replace("default-src 'self'; ", "default-src 'self'; " + img)
    policy = policy.replace("connect-src 'self'; ", connect)
    return policy.replace("script-src 'self'", f"script-src 'self' 'nonce-{nonce}'")


def _tile_origin(url: str) -> str | None:
    """The scheme and host of the tile source, for the content policy.

    Returns None for anything that is not a plain https origin, which
    closes the policy rather than opening it: a malformed or http URL in
    the config should mean no tiles, not tiles from anywhere.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return f"https://{parts.hostname}"


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
    # Previews, Pinpoint and exports open the photograph too, not only the
    # analysis; with the optional decoder installed they all read HEIC.
    heif.enable()

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
                "basemap_max_zoom": cfg.network.basemap_max_zoom,
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

    @app.get("/api/preview")
    async def preview(path: str) -> Response:
        """An upright, downscaled copy of a file chosen in the native dialog.

        The window cannot read file:// URLs, so without this an image
        opened through the dialog had no thumbnail and the evidence map
        nothing to draw on. Same rule as analysis: only a path the dialog
        handed out, and the copy is made in memory and never stored.
        """
        data, _name = _read_approved(path)
        return await _preview_response(data)

    @app.post("/api/preview/upload")
    async def preview_upload(image: UploadFile = File(...)) -> Response:
        """The same copy for a file dropped on the window.

        The page shows such a file straight from the browser, which cannot
        draw HEIC; it asks for this instead, so the thumbnail, the evidence
        map and the walk overlay have something to show.
        """
        data = await image.read()
        _check_size(cfg, len(data))
        return await _preview_response(data)

    async def _preview_response(data: bytes) -> Response:
        try:
            body = await asyncio.to_thread(_preview_jpeg, data)
        except Exception as exc:
            raise HTTPException(status_code=415, detail=f"Cannot preview: {exc}") from exc
        return Response(body, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

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

    # ----------------------------------------------------------- pinpoint
    #
    # On request only: matching against reference photographs takes from
    # seconds (cached) to several minutes, and asks Commons which
    # photographs exist near the candidates, so it is not part of every
    # analysis. Closing the request stops the search at its next step.

    def _pinpoint_engine():
        from .paths import state_dir
        from .pinpoint.engine import PinpointEngine
        net = cfg.network
        return PinpointEngine(state_dir() / "pinpoint",
                              online=not net.offline_only and net.pinpoint_enabled,
                              mapillary_token=net.mapillary_token,
                              cache_mb=net.pinpoint_cache_mb,
                              panoramax=net.pinpoint_panoramax,
                              second_matcher=net.pinpoint_second_matcher)

    def _areas(items: Any) -> list:
        from .pinpoint.engine import Area
        if isinstance(items, str):
            try:
                items = json.loads(items or "[]")
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="areas must be JSON") from exc
        out = []
        for a in (items or [])[:4]:
            try:
                lat, lon = float(a["lat"]), float(a["lon"])
                radius = float(a.get("radius_m", 1500))
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="bad area") from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise HTTPException(status_code=400, detail="area out of range")
            out.append(Area(lat, lon, max(100.0, min(10000.0, radius)),
                            str(a.get("label") or "the estimate")[:60]))
        return out

    def _budget(value: Any) -> int:
        from .pinpoint.engine import BUDGET, MAX_BUDGET
        try:
            return max(50, min(MAX_BUDGET, int(value or BUDGET)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="bad budget") from exc

    def _pinpoint_stream(data: bytes, areas: list, budget: int,
                         mirrored: bool = False) -> StreamingResponse:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        gone = threading.Event()

        def progress(key: str, message: str) -> None:
            if gone.is_set():
                raise _PinpointCancelled
            loop.call_soon_threadsafe(queue.put_nowait, json.dumps(
                {"type": "progress", "stage": key, "message": message}))

        def work() -> dict:
            from io import BytesIO
            from PIL import Image, ImageOps
            from .pinpoint.engine import clip_namer
            with Image.open(BytesIO(data)) as img:
                img.load()
                if mirrored:
                    # The analysis found the photograph mirrored. Features
                    # do not match their mirror image, so compare the scene
                    # the right way round -- upright first, as it was read.
                    img = ImageOps.mirror(ImageOps.exif_transpose(img) or img)
                # The analysis's CLIP, already loaded, weighs the landmark
                # names near each area against the photograph; never loaded
                # just for this.
                clip = getattr(engine, "clip", None)
                namer = clip_namer(clip, img) if clip is not None and clip.loaded else None
                return _pinpoint_engine().run(img, areas, progress, budget=budget,
                                              namer=namer)

        async def run() -> None:
            try:
                result = await asyncio.to_thread(work)
                await queue.put(json.dumps({"type": "result", "pinpoint": result}))
            except _PinpointCancelled:
                log.info("pinpoint stopped: the page went away")
            except Exception as exc:  # pragma: no cover - guard
                log.exception("pinpoint failed")
                await queue.put(json.dumps({"type": "result", "pinpoint": {
                    "verdict": "unavailable",
                    "message": f"Pinpoint failed: {type(exc).__name__}: {exc}"}}))
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
                gone.set()

        return StreamingResponse(body(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store",
                                          "X-Accel-Buffering": "no"})

    @app.post("/api/pinpoint/stream")
    async def pinpoint_stream(image: UploadFile = File(...),
                              areas: str = Form("[]"),
                              budget: str = Form(""),
                              mirrored: str = Form("")) -> StreamingResponse:
        data = await image.read()
        _check_size(cfg, len(data))
        return _pinpoint_stream(data, _areas(areas), _budget(budget),
                                mirrored=mirrored == "1")

    @app.post("/api/pinpoint/local/stream")
    async def pinpoint_local_stream(payload: dict[str, Any]) -> StreamingResponse:
        data, _name = _read_approved(str(payload.get("path", "")))
        return _pinpoint_stream(data, _areas(payload.get("areas", [])),
                                _budget(payload.get("budget")),
                                mirrored=payload.get("mirrored") is True)

    @app.get("/api/pinpoint/thumb/{key}")
    async def pinpoint_thumb(key: str) -> Response:
        """A matched reference's cached thumbnail (the page may load only
        its own images, so Commons thumbnails are served from the cache)."""
        import re as _re
        from .paths import state_dir
        if not _re.fullmatch(r"[0-9a-f]{20}", key):
            raise HTTPException(status_code=404, detail="no such thumbnail")
        path = state_dir() / "pinpoint" / "thumbs" / f"{key}.jpg"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no such thumbnail")
        return FileResponse(path, media_type="image/jpeg")

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
                        if heif.looks_like_heif(image_bytes):
                            # A browser opening the report cannot draw HEIC.
                            image_bytes = _preview_jpeg(image_bytes)
                    except Exception:
                        image_bytes = None
            html = build_single(report, image_bytes, mime)

        return Response(content=html, media_type="text/html; charset=utf-8")

    # ------------------------------------------------ ground: 3D and imagery
    #
    # Buildings, streets and water come from the local store (OSM regions
    # the user imported, plus Google Open Buildings fetched per area and
    # kept). Imagery tiles are fetched here and cached on disk, so the
    # page's content policy never has to name an outside host.

    geo_state: dict[str, Any] = {}

    def geo():
        from .geodata.service import get_service
        return get_service(online=not cfg.network.offline_only)

    def tiles():
        cache = geo_state.get("tiles")
        if cache is None:
            from .geodata.tiles import TileCache
            cache = TileCache(offline=cfg.network.offline_only)
            geo_state["tiles"] = cache
        return cache

    def _coord(lat: float, lon: float) -> None:
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise HTTPException(status_code=400, detail="coordinate out of range")

    @app.get("/api/geodata/status")
    def geodata_status(lat: float | None = None, lon: float | None = None
                       ) -> dict[str, Any]:
        from .geodata.tiles import sources_for
        out = geo().status(lat, lon)
        if lat is not None and lon is not None:
            _coord(lat, lon)
            out["imagery"] = sources_for(lat, lon)
        return out

    @app.get("/api/geodata/around")
    def geodata_around(lat: float, lon: float, radius: float = 800.0
                       ) -> dict[str, Any]:
        _coord(lat, lon)
        return geo().around(lat, lon, radius)

    @app.get("/api/weather/scene")
    def weather_scene(lat: float, lon: float, at: str | None = None) -> dict[str, Any]:
        """The weather the 3D walk is drawn in: at ``at`` (the photograph's
        capture time, ISO 8601) or now. Only the coordinate and the time go
        out. Off when offline or when weather history is switched off."""
        _coord(lat, lon)
        net = cfg.network
        if net.offline_only or not net.weather_history_enabled:
            return {"available": False,
                    "reason": "offline" if net.offline_only else "weather is switched off"}
        when = None
        if at:
            from datetime import datetime, timezone
            try:
                when = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="bad time") from exc
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        from .geodata.weather import WeatherHistory
        got = WeatherHistory(cache_dir()).scene(lat, lon, when)
        if not got:
            return {"available": False, "reason": "no weather data for this place and time"}
        return {"available": True, **got}

    @app.get("/api/events/near")
    def events_near(lat: float, lon: float, at: str | None = None) -> dict[str, Any]:
        """Earthquakes (USGS) and natural events (NASA EONET) around a place:
        around ``at`` (the photograph's time, ISO) when given, else lately.
        Context for the reader; it never moves the estimate. Only a
        coordinate and dates go out. Off when offline or switched off."""
        _coord(lat, lon)
        net = cfg.network
        if net.offline_only or not net.events_enabled:
            return {"available": False,
                    "reason": "offline" if net.offline_only else "events are switched off"}
        when = None
        if at:
            from datetime import datetime, timezone
            try:
                when = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="bad time") from exc
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        from .geodata.events import NearbyEvents
        return NearbyEvents(cache_dir() / "events").near(lat, lon, when)

    @app.get("/api/tiles/{source}/{z}/{x}/{y}")
    def tile(source: str, z: int, x: int, y: int, date: str | None = None):
        data, why = tiles().get(source, z, x, y, date)
        if data is None:
            # 204 rather than 404: "no imagery here" is an answer the
            # view draws as empty ground, not an error it reports.
            return Response(status_code=204, headers={"X-Mapify-Reason": why})
        return Response(content=data, media_type=why, headers={
            "Cache-Control": "private, max-age=604800"})

    @app.get("/api/imagery/hls-latest")
    def hls_latest(lat: float, lon: float, days: int = 40) -> dict[str, Any]:
        """The most recent date Landsat actually covered this spot.

        HLS is daily granules, not a mosaic: on most days a given place
        was not imaged, and the tile comes back empty. Walk back until a
        tile holds something.
        """
        import datetime as dt
        import math as _m
        _coord(lat, lon)
        z = 10
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - _m.asinh(_m.tan(_m.radians(lat))) / _m.pi) / 2 * n)
        today = dt.date.today()
        for back in range(1, max(2, min(90, days))):
            day = (today - dt.timedelta(days=back)).isoformat()
            data, _why = tiles().get("hls", z, x, y, day)
            # An empty PNG tile is a few hundred bytes; anything carrying
            # imagery is several kilobytes.
            if data is not None and len(data) > 2000:
                return {"date": day, "days_ago": back}
        return {"date": None, "days_ago": None,
                "note": f"No Landsat pass with imagery here in {days} days."}

    # ------------------------------------------------ exports: QGIS, kepler

    def _around_for(report: dict[str, Any]) -> dict[str, Any] | None:
        pred = report.get("prediction") or {}
        est = pred.get("estimated")
        if not (pred.get("determinate") and est):
            return None
        try:
            return geo().around(est["latitude"], est["longitude"], 900)
        except Exception as exc:                       # pragma: no cover
            log.warning("export: no surroundings (%s)", exc)
            return None

    @app.post("/api/export/kepler")
    def export_kepler(payload: dict[str, Any]) -> dict[str, Any]:
        from .exporters import build_kepler
        report = payload.get("report")
        if not isinstance(report, dict):
            raise HTTPException(status_code=400, detail="No report given.")
        return build_kepler(report, _around_for(report))

    @app.post("/api/export/gpkg")
    def export_gpkg(payload: dict[str, Any]) -> Response:
        import tempfile
        from .exporters import build_gpkg
        report = payload.get("report")
        if not isinstance(report, dict):
            raise HTTPException(status_code=400, detail="No report given.")
        tmp = Path(tempfile.mkdtemp(dir=str(state_dir()))) / "case.gpkg"
        try:
            build_gpkg(report, _around_for(report), tmp)
            data = tmp.read_bytes()
        finally:
            try:
                tmp.unlink()
                tmp.parent.rmdir()
            except OSError:
                pass
        return Response(content=data,
                        media_type="application/geopackage+sqlite3")

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


def _preview_jpeg(data: bytes) -> bytes:
    """An upright JPEG copy, at most 1600 px, made in memory and not stored."""
    from io import BytesIO

    from PIL import Image, ImageOps
    img = Image.open(BytesIO(data))
    img = ImageOps.exif_transpose(img) or img
    img.thumbnail((1600, 1600))
    out = BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=88)
    return out.getvalue()


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
