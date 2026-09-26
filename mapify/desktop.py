"""Mapify as a native Windows application.

This is a desktop program, not a web page. It opens a real window with a real
title bar and a taskbar entry, uses Windows' own file dialogs, and never shows
a URL or asks anyone to open a browser.

The window is drawn by Edge WebView2, which ships with Windows 10 and 11, so
the globe gets a modern GPU-accelerated renderer without bundling a second
browser. pywebview (BSD-3-Clause) provides the window and the Python/JS
bridge.

Everything runs in one process. The analysis service listens only on the
loopback interface, on an ephemeral port, behind a random per-session token,
so no other program on the machine -- including a browser -- can reach it.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import ACCEPTED_EXTENSIONS, Config, load_config
from .paths import read_root, state_dir

log = logging.getLogger("mapify.desktop")

ROOT = read_root()
WEB_DIR = Path(__file__).resolve().parent / "web"

IMAGE_TYPES = ("Images (*.jpg;*.jpeg;*.png;*.webp)", "All files (*.*)")

# A ceiling on one batch. At roughly three seconds an image this is already
# half an hour of work, and an unbounded folder walk is a foot-gun.
MAX_BATCH = 500


def _free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# The pywebview Window is kept here rather than on the bridge object.
# pywebview serialises the js_api object's attributes to expose them to
# JavaScript, and Window.native is a .NET WinForms control: walking it
# recurses forever and touches COM interfaces from the wrong thread. Keeping
# the reference out of the bridge's attribute graph avoids all of that.
_WINDOW: dict[str, Any] = {"ref": None}


class NativeBridge:
    """The object JavaScript can call, exposed as ``window.pywebview.api``.

    Deliberately small. It does the things a browser cannot do -- native file
    dialogs, saving a report where the user chooses, closing the window -- and
    nothing else.
    """

    def __init__(self, cfg: Config, token: str):
        self.cfg = cfg
        self.token = token
        # Paths the user picked in a native dialog. The HTTP layer will read a
        # file from disk only if it is in here, so a stray request cannot walk
        # the filesystem even if it somehow got the token.
        self.approved_paths: set[str] = set()
        self._lock = threading.Lock()

    # -------------------------------------------------------------- dialogs

    def open_image(self) -> dict[str, Any]:
        """Native 'open file' dialog. Returns the chosen path, or nothing."""
        import webview

        window = _WINDOW["ref"]
        if window is None:
            return {"error": "The application window is not ready yet."}
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=IMAGE_TYPES,
        )
        if not result:
            return {"cancelled": True}
        path = Path(result[0]).resolve()
        if not path.is_file():
            return {"error": "That file could not be opened."}
        if path.suffix.lower() not in ACCEPTED_EXTENSIONS:
            return {"error": f"{path.suffix or 'That file type'} is not a supported image."}
        size = path.stat().st_size
        if size > self.cfg.server.max_upload_bytes:
            return {
                "error": (
                    f"{path.name} is {size / 1e6:.1f} MB; the limit is "
                    f"{self.cfg.server.max_upload_bytes / 1e6:.0f} MB."
                )
            }
        with self._lock:
            self.approved_paths.add(str(path))
        return {"path": str(path), "name": path.name, "bytes": size}

    def open_folder(self, recurse: bool = False) -> dict[str, Any]:
        """Native folder dialog, returning the images inside it.

        Every path is added to the approved set, the same as a single Open
        would, so the service will read them and nothing else.
        """
        import webview

        window = _WINDOW["ref"]
        if window is None:
            return {"error": "The application window is not ready yet."}
        chosen = window.create_file_dialog(webview.FOLDER_DIALOG)
        if not chosen:
            return {"cancelled": True}

        root = Path(chosen[0] if isinstance(chosen, (list, tuple))
                    else chosen).resolve()
        if not root.is_dir():
            return {"error": "That is not a folder."}

        walker = root.rglob("*") if recurse else root.glob("*")
        files: list[dict[str, Any]] = []
        skipped_big = 0
        for path in sorted(walker):
            if len(files) >= MAX_BATCH:
                break
            if not path.is_file():
                continue
            if path.suffix.lower() not in ACCEPTED_EXTENSIONS:
                continue
            size = path.stat().st_size
            if size > self.cfg.server.max_upload_bytes:
                skipped_big += 1
                continue
            resolved = str(path.resolve())
            with self._lock:
                self.approved_paths.add(resolved)
            files.append({"path": resolved, "name": path.name, "bytes": size})

        if not files:
            return {"error": f"No JPG, PNG or WEBP images in {root.name}."}
        return {
            "folder": str(root),
            "files": files,
            "skipped_oversize": skipped_big,
            "truncated": len(files) >= MAX_BATCH,
        }

    def save_report(self, content: str, suggested: str = "mapify-report.json"
                    ) -> dict[str, Any]:
        """Native 'save as' dialog for the JSON report."""
        import webview

        window = _WINDOW["ref"]
        if window is None:
            return {"error": "The application window is not ready yet."}
        target = window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=suggested,
            file_types=("JSON (*.json)", "All files (*.*)"),
        )
        if not target:
            return {"cancelled": True}
        path = Path(target if isinstance(target, str) else target[0])
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return {"error": str(exc)}
        return {"path": str(path)}

    def save_html(self, content: str, suggested: str = "mapify-report.html"
                  ) -> dict[str, Any]:
        """Native Save-as for a standalone HTML report."""
        import webview

        window = _WINDOW["ref"]
        if window is None:
            return {"error": "The application window is not ready yet."}
        target = window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=suggested,
            file_types=("Web page (*.html)", "All files (*.*)"),
        )
        if not target:
            return {"cancelled": True}
        path = Path(target if isinstance(target, str) else target[0])
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return {"error": str(exc)}
        return {"path": str(path)}

    def reveal(self, path: str) -> dict[str, Any]:
        """Show a saved file in Explorer."""
        p = Path(path)
        if not p.exists():
            return {"error": "File not found."}
        try:
            os.startfile(p.parent)  # noqa: S606 - Windows shell, user-initiated
        except Exception as exc:
            return {"error": str(exc)}
        return {"ok": True}

    # ----------------------------------------------------------- app facts

    def app_info(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "token": self.token,
            "platform": sys.platform,
            "native": True,
            "root": str(ROOT),
        }

    def quit(self) -> None:
        window = _WINDOW["ref"]
        if window is not None:
            window.destroy()


def _wait_for_server(port: int, token: str, timeout: float = 30.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/status"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"X-Mapify-Token": token})
            with urllib.request.urlopen(req, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


def run(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="Mapify",
        description="Image geolocation reconnaissance (desktop application).",
    )
    ap.add_argument("--config", default=None)
    ap.add_argument("--offline", action="store_true",
                    help="refuse every outbound request for this run")
    ap.add_argument("--debug", action="store_true",
                    help="open developer tools in the window")
    ap.add_argument("--log-level", default="warning")
    ap.add_argument("--selftest", action="store_true",
                    help="import and construct everything, then exit "
                         "(used by build_exe.py to prove a build works)")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # pywebview introspects the native window when its logger is verbose,
    # which on the WebView2 backend means touching COM from the wrong thread.
    logging.getLogger("pywebview").setLevel(logging.WARNING)

    cfg = load_config(args.config)
    if args.offline:
        cfg.network.offline_only = True

    token = secrets.token_urlsafe(24)
    port = _free_port()
    cfg.server.host = "127.0.0.1"
    cfg.server.port = port

    bridge = NativeBridge(cfg, token)

    # --- start the local service -----------------------------------------
    from .server import create_app

    app = create_app(cfg, token=token, bridge=bridge)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level=args.log_level,
            access_log=False,
            # The window closes the process; no graceful-shutdown dance.
            timeout_graceful_shutdown=1,
        )
    )
    thread = threading.Thread(target=server.run, name="mapify-service", daemon=True)
    thread.start()

    if not _wait_for_server(port, token):
        _fatal("Mapify could not start its local analysis service.")
        return 1

    # --- open the window --------------------------------------------------
    #
    # Check the renderer before asking pywebview for a window. Failing here
    # costs a registry read and produces an instruction; failing later
    # produces a COM HRESULT from inside .NET interop.
    if sys.platform == "win32" and webview2_runtime() is None:
        _fatal(WEBVIEW2_MISSING)
        return 1

    try:
        import webview
    except ImportError:
        _fatal(
            "pywebview is not installed.\n\n"
            "Run scripts\\setup_env.ps1, or:\n"
            "    pip install pywebview pythonnet"
        )
        return 1

    window = webview.create_window(
        f"Mapify {__version__}  —  Image Geolocation Reconnaissance",
        f"http://127.0.0.1:{port}/?t={token}",
        js_api=bridge,
        width=1580,
        height=940,
        min_size=(1040, 660),
        background_color="#05070D",
        text_select=True,
        confirm_close=False,
    )
    _WINDOW["ref"] = window

    def on_closing() -> None:
        try:
            server.should_exit = True
        except Exception:
            pass

    window.events.closing += on_closing

    icon = ROOT / "assets" / "mapify.ico"
    kwargs: dict[str, Any] = {"debug": args.debug, "private_mode": True}
    if icon.exists():
        kwargs["icon"] = str(icon)

    # EdgeChromium is the WebView2 runtime built into Windows 10/11. It is the
    # only backend with the WebGL2 support the globe needs.
    try:
        webview.start(gui="edgechromium", **kwargs)
    except Exception as exc:
        log.warning("EdgeChromium backend unavailable (%s); using the default", exc)
        try:
            webview.start(**kwargs)
        except Exception as exc2:
            _fatal(
                "Mapify could not create its window.\n\n"
                f"{type(exc2).__name__}: {exc2}\n\n"
                "The Microsoft Edge WebView2 Runtime is required. It ships "
                "with Windows 11; on older systems install it from "
                "https://developer.microsoft.com/microsoft-edge/webview2/"
            )
            return 1

    server.should_exit = True
    return 0


def _selftest() -> int:
    """Prove that a packaged build can reach everything it needs.

    PyInstaller reports success whether or not the lazily-imported model
    stack made it into the bundle, so the build script runs this inside the
    executable it just produced. It deliberately does not load model weights
    -- that is a 1.7 GB download -- only that every import resolves and the
    bundled data is where the code expects it.
    """
    import traceback

    checks: list[tuple[str, str]] = []
    failed = False

    def check(label: str, fn) -> None:
        nonlocal failed
        try:
            detail = fn()
            checks.append((label, f"ok    {detail}" if detail else "ok"))
        except Exception as exc:
            failed = True
            checks.append((label, f"FAIL  {type(exc).__name__}: {exc}"))
            traceback.print_exc()

    def _torch() -> str:
        import torch
        return f"{torch.__version__}"

    def _models() -> str:
        import torchvision, transformers  # noqa: F401
        from geoclip import GeoCLIP  # noqa: F401
        return f"transformers {transformers.__version__}"

    def _webview() -> str:
        import webview
        return getattr(webview, "__version__", "")

    def _assets() -> str:
        missing = [
            p.name for p in (
                WEB_DIR / "index.html",
                WEB_DIR / "styles.css",
                WEB_DIR / "js" / "app.js",
                WEB_DIR / "vendor" / "three.module.js",
            ) if not p.exists()
        ]
        if missing:
            raise FileNotFoundError("missing " + ", ".join(missing))
        return "interface present"

    def _geodata() -> str:
        from .geo.offline import get_geocoder
        g = get_geocoder()
        hit = g.country_at(12.9716, 77.5946)
        if not hit:
            raise RuntimeError("Natural Earth lookup returned nothing")
        return f"reverse geocode -> {hit.name}"

    def _countries() -> str:
        from .data.countries import get_table
        return f"{len(get_table())} countries"

    def _pipeline() -> str:
        from .pipeline import AnalysisEngine
        cfg = load_config()
        cfg.network.offline_only = True
        AnalysisEngine(cfg)
        return "engine constructed"

    check("torch", _torch)
    check("model stack", _models)
    check("pywebview", _webview)
    check("interface assets", _assets)
    check("natural earth", _geodata)
    check("country table", _countries)
    check("pipeline", _pipeline)

    width = max(len(label) for label, _ in checks)
    for label, result in checks:
        print(f"  {label.ljust(width)}  {result}")
    print("SELFTEST FAILED" if failed else "SELFTEST OK")
    return 1 if failed else 0


def webview2_runtime() -> str | None:
    """The installed Edge WebView2 runtime version, or None if absent.

    WebView2 ships with Windows 11 and with current Windows 10, but it is
    removable and it is genuinely missing on older or heavily managed
    machines. Without this check pywebview raises a COM error from inside
    .NET interop, which is a stack trace ending in ``0x80040154`` -- true,
    and of no use to anybody.

    The runtime registers itself under a well-known key, in both the
    machine-wide and per-user hives, and on 64-bit Windows the machine-wide
    one lives under WOW6432Node regardless of the runtime's own bitness.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - not Windows
        return None

    client = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    locations = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients" + "\\" + client),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients" + "\\" + client),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients" + "\\" + client),
    )
    for hive, path in locations:
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return str(version)
    return None


WEBVIEW2_MISSING = (
    "Mapify needs the Microsoft Edge WebView2 Runtime, and this machine "
    "does not have it.\n\n"
    "It is a free Microsoft component that draws the application window. "
    "Windows 11 includes it; some Windows 10 installations do not.\n\n"
    "Install the Evergreen Runtime from:\n"
    "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
    "Then start Mapify again. Nothing else needs to be reinstalled."
)


def _fatal(message: str) -> None:
    """Report a startup failure without a console."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Mapify", 0x10)
    except Exception:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(run())
