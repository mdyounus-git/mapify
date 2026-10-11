"""What a file declares about its own making: C2PA read and checked, and
generator names matched as words, not as letters inside camera tags.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mapify.forensics.provenance import read_provenance

DATA = Path(__file__).parent / "data" / "c2pa"


def test_a_signed_manifest_is_read_and_its_signature_checked():
    pytest.importorskip("c2pa")
    got = read_provenance((DATA / "C.jpg").read_bytes())
    assert got.c2pa and got.c2pa_details
    assert got.c2pa_details["validation"].lower() == "valid"
    assert got.c2pa_details["signer"] == "C2PA Test Signing Cert"
    assert "c2pa.created" in got.c2pa_details["actions"]
    assert got.ai_declared and got.source_type == "algorithmicmedia"
    assert any("the C2PA manifest says" in e for e in got.evidence)


def test_a_camera_tag_is_not_a_generator():
    """aux:ImageNumber contains the letters of Google's 'Imagen'."""
    got = read_provenance((DATA / "A.jpg").read_bytes())
    assert not got.ai_declared and got.generator is None and not got.c2pa


def test_a_named_generator_is_still_found():
    assert read_provenance(b"... Created with Midjourney v6 ...").generator == "midjourney"
    assert read_provenance(b"tool=imagen 3").generator == "imagen"
    assert read_provenance(b"aux:imagenumber=0").generator is None
