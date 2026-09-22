"""The local HTTP server.

Binds to the loopback interface by default, serves the console, and exposes a
small JSON API. There is no account system, no telemetry, no analytics, and no
outbound call that is not listed in :mod:`mapify.config`.

Uploads are held in memory and dropped when the request ends, unless
``server.keep_uploads`` is switched on for debugging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Config, load_config
from .pipeline import AnalysisEngine, ImageRejected

log = logging.getLogger("mapify.server")

WEB_DIR = Path(__file__).resolve().parent / "web"

# Everything the page needs comes from this origin. The one exception the
# operator can switch on is raster basemap tiles, and the policy is widened
# only when they do.
_CSP_BASE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "worker-src 'self' blob:; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


def _csp(cfg: Config) -> str:
    if cfg.network.basemap_tiles_enabled:
        return _CSP_BASE.replace(
            "default-src 'self'; ",
            "default-src 'self'; img-src 'self' data: blob: https:; ",
        )
    return _CSP_BASE.replace(
        "default-src 'self'; ", "default-src 'self'; img-src 'self' data: blob:; "
    )


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    engine = AnalysisEngine(cfg)

    app = FastAPI(
        title="Mapify",
        version=__version__,
        description="Open-source image geolocation reconnaissance.",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.cfg = cfg
    app.state.engine = engine

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _csp(cfg)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=(), interest-cohort=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response

    # ------------------------------------------------------------- pages

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

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
            },
        }

    @app.post("/api/warmup")
    async def warmup() -> dict[str, Any]:
        """Force model loading now rather than on the first upload."""
        return await asyncio.to_thread(engine.warmup)

    @app.post("/api/analyse")
    async def analyse(image: UploadFile = File(...)) -> JSONResponse:
        data = await image.read()
        if len(data) > cfg.server.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Image is {len(data) / 1e6:.1f} MB; the limit is "
                    f"{cfg.server.max_upload_bytes / 1e6:.0f} MB."
                ),
            )
        try:
            report = await asyncio.to_thread(
                engine.analyse, data, image.filename
            )
        except ImageRejected as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc

        if cfg.server.keep_uploads:
            _debug_dump(data, image.filename)

        payload = report.as_dict()
        return JSONResponse(payload, status_code=200 if report.ok else 422)

    # Streaming variant: the console uses this so the progress card reflects
    # the stage the pipeline is actually in.
    @app.post("/api/analyse/stream")
    async def analyse_stream(image: UploadFile = File(...)) -> Response:
        from fastapi.responses import StreamingResponse

        data = await image.read()
        if len(data) > cfg.server.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Image too large.")
        filename = image.filename
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(key: str, message: str) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                json.dumps({"type": "progress", "stage": key, "message": message}),
            )

        async def run() -> None:
            try:
                report = await asyncio.to_thread(
                    engine.analyse, data, filename, progress
                )
                await queue.put(json.dumps({"type": "result", "report": report.as_dict()}))
            except Exception as exc:  # pragma: no cover - guard
                log.exception("stream analysis failed")
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

    @app.get("/api/attribution")
    async def attribution() -> dict[str, Any]:
        path = Path(__file__).resolve().parent.parent / "ATTRIBUTION.md"
        return {
            "summary": engine._attribution(),
            "full_text": path.read_text(encoding="utf-8") if path.exists() else None,
        }

    # -------------------------------------------------------- static last

    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


def _debug_dump(data: bytes, filename: str | None) -> None:
    import time

    out = Path(__file__).resolve().parent.parent / "data" / "uploads"
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in (filename or "upload") if c.isalnum() or c in "._-")
    (out / f"{int(time.time())}_{safe}").write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mapify",
        description="Run the Mapify reconnaissance console locally.",
    )
    ap.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=None, help="port (default 8771)")
    ap.add_argument("--config", default=None, help="path to config.toml")
    ap.add_argument("--offline", action="store_true",
                    help="refuse every outbound request for this run")
    ap.add_argument("--warmup", action="store_true",
                    help="load the models before serving")
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

    app = create_app(cfg)

    if args.warmup:
        log.info("warming models up (first run downloads ~1.7 GB of weights)")
        for name, info in app.state.engine.warmup().items():
            log.info("  %-18s %s", name, info)

    import uvicorn

    banner = (
        f"\n  Mapify {__version__}\n"
        f"  console   http://{cfg.server.host}:{cfg.server.port}/\n"
        f"  offline   {'yes -- no outbound requests' if cfg.network.offline_only else 'no -- OSM enrichment allowed'}\n"
        f"  device    {cfg.models.device}\n"
    )
    print(banner, file=sys.stderr)

    uvicorn.run(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=args.log_level,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
