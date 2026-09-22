"""The local service's security boundary.

The service exists only to feed the application window. These tests pin down
the two things that keep it from being something worse than that: the token
gate, and the rule that a file path is only readable if the user chose it in a
native dialog.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_jpeg
from mapify.config import load_config
from mapify.server import COOKIE_NAME, create_app

TOKEN = "test-token-not-a-secret"


def config():
    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.geoclip_enabled = False
    cfg.models.clip_evidence_enabled = False
    cfg.models.ocr_enabled = False
    return cfg


class FakeBridge:
    """Stands in for the desktop bridge's path allowlist."""

    def __init__(self, approved=()):
        self.approved_paths = set(approved)


@pytest.fixture
def open_client():
    """No token: the development/headless mode."""
    return TestClient(create_app(config()))


@pytest.fixture
def locked_client():
    return TestClient(create_app(config(), token=TOKEN))


class TestTokenGate:
    def test_open_mode_serves_without_a_token(self, open_client):
        assert open_client.get("/api/status").status_code == 200

    def test_locked_mode_refuses_without_a_token(self, locked_client):
        r = locked_client.get("/api/status")
        assert r.status_code == 403
        assert "private to the running application" in r.text

    def test_a_wrong_token_is_refused(self, locked_client):
        r = locked_client.get("/api/status", headers={"X-Mapify-Token": "wrong"})
        assert r.status_code == 403

    def test_the_header_works(self, locked_client):
        r = locked_client.get("/api/status", headers={"X-Mapify-Token": TOKEN})
        assert r.status_code == 200

    def test_the_query_parameter_works_and_sets_a_cookie(self, locked_client):
        r = locked_client.get(f"/?t={TOKEN}")
        assert r.status_code == 200
        assert COOKIE_NAME in r.cookies or COOKIE_NAME in locked_client.cookies

    def test_static_files_are_gated_too(self, locked_client):
        assert locked_client.get("/js/app.js").status_code == 403
        assert locked_client.get("/styles.css").status_code == 403

    def test_analysis_is_gated(self, locked_client):
        r = locked_client.post(
            "/api/analyse", files={"image": ("x.jpg", make_jpeg(), "image/jpeg")}
        )
        assert r.status_code == 403


class TestLocalPathReads:
    def test_refused_without_a_desktop_bridge(self, open_client):
        r = open_client.post("/api/analyse/local", json={"path": "C:/Windows/win.ini"})
        assert r.status_code == 400
        assert "desktop app" in r.json()["detail"]

    def test_a_path_the_dialog_never_returned_is_refused(self, tmp_path):
        secret = tmp_path / "private.jpg"
        secret.write_bytes(make_jpeg())
        app = create_app(config(), bridge=FakeBridge(approved=()))
        client = TestClient(app)
        r = client.post("/api/analyse/local", json={"path": str(secret)})
        assert r.status_code == 403
        assert "file dialog" in r.json()["detail"]

    def test_traversal_does_not_reach_an_approved_sibling(self, tmp_path):
        approved = tmp_path / "ok.jpg"
        approved.write_bytes(make_jpeg())
        other = tmp_path / "other.jpg"
        other.write_bytes(make_jpeg())
        app = create_app(config(), bridge=FakeBridge(approved={str(approved)}))
        client = TestClient(app)
        sneaky = str(tmp_path / "sub" / ".." / "other.jpg")
        r = client.post("/api/analyse/local", json={"path": sneaky})
        assert r.status_code == 403

    def test_an_approved_path_is_read(self, tmp_path):
        good = tmp_path / "photo.jpg"
        good.write_bytes(make_jpeg())
        app = create_app(config(), bridge=FakeBridge(approved={str(good.resolve())}))
        client = TestClient(app)
        r = client.post("/api/analyse/local", json={"path": str(good)})
        # 200 or 422 depending on whether a location could be produced; the
        # point is that it was allowed to read the file at all.
        assert r.status_code in (200, 422)
        assert r.json()["image"]["filename"] == "photo.jpg"


class TestSecurityHeaders:
    def test_csp_is_strict_and_carries_a_nonce(self, open_client):
        r = open_client.get("/")
        csp = r.headers["content-security-policy"]
        assert "'unsafe-inline'" not in csp.split("style-src")[0]
        assert "nonce-" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp

    def test_the_nonce_in_the_header_matches_the_page(self, open_client):
        r = open_client.get("/")
        nonce = r.headers["content-security-policy"].split("nonce-")[1].split("'")[0]
        assert f'nonce="{nonce}"' in r.text

    def test_the_placeholder_is_never_served(self, open_client):
        assert "__CSP_NONCE__" not in open_client.get("/").text
        assert "__CSP_NONCE__" not in open_client.get("/index.html").text

    def test_a_fresh_nonce_per_response(self, open_client):
        a = open_client.get("/").headers["content-security-policy"]
        b = open_client.get("/").headers["content-security-policy"]
        assert a != b

    def test_tiles_are_not_permitted_by_default(self, open_client):
        csp = open_client.get("/").headers["content-security-policy"]
        assert "img-src 'self' data: blob:;" in csp
        assert "https:" not in csp

    def test_enabling_tiles_widens_only_images_and_connections(self):
        cfg = config()
        cfg.network.basemap_tiles_enabled = True
        client = TestClient(create_app(cfg))
        csp = client.get("/").headers["content-security-policy"]
        assert "img-src 'self' data: blob: https:;" in csp
        assert "script-src 'self' 'nonce-" in csp

    def test_the_usual_hardening_headers_are_set(self, open_client):
        h = open_client.get("/").headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert "geolocation=()" in h["permissions-policy"]


class TestUploads:
    def test_rejects_an_oversized_upload(self, open_client):
        cfg = config()
        cfg.server.max_upload_bytes = 500
        client = TestClient(create_app(cfg))
        r = client.post(
            "/api/analyse",
            files={"image": ("big.jpg", make_jpeg(900, 900), "image/jpeg")},
        )
        assert r.status_code == 413

    def test_rejects_a_non_image(self, open_client):
        r = open_client.post(
            "/api/analyse", files={"image": ("x.jpg", b"nope", "image/jpeg")}
        )
        assert r.status_code == 422
        assert r.json()["ok"] is False

    def test_status_declares_what_is_switched_off(self, open_client):
        body = open_client.get("/api/status").json()
        assert body["engine"]["offline_only"] is True
        assert body["client"]["basemap_tiles_enabled"] is False
        assert body["client"]["native"] is False
