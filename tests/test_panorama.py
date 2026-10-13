"""360-degree panoramas: recognised conservatively, cut into four views.

Nothing here loads a model: the views are checked by colour, and the
pipeline by a stand-in locator that records what it was handed.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from conftest import requires_geodata
from mapify.panorama import VIEW_YAWS, is_equirectangular, views

QUARTERS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def sphere(w=4096, h=2048) -> Image.Image:
    """Four quarters, red / green / blue / yellow from left to right."""
    a = np.zeros((h, w, 3), np.uint8)
    for i, colour in enumerate(QUARTERS):
        a[:, i * w // 4:(i + 1) * w // 4] = colour
    return Image.fromarray(a)


def test_a_full_sphere_is_recognised_and_a_wide_crop_is_not():
    assert is_equirectangular(sphere())
    assert not is_equirectangular(Image.new("RGB", (3000, 1000)))     # 3:1 panorama
    assert not is_equirectangular(Image.new("RGB", (2000, 1000)))     # 2:1, but small
    assert not is_equirectangular(Image.new("RGB", (1600, 1200)))


def test_the_files_own_tag_is_believed_at_any_size():
    small = Image.new("RGB", (2000, 1000))
    small.info["xmp"] = (b'<x:xmpmeta><rdf:Description GPano:ProjectionType='
                         b'"equirectangular"/></x:xmpmeta>')
    assert is_equirectangular(small)


def test_four_views_face_ahead_right_behind_and_left():
    got = views(sphere())
    assert len(got) == len(VIEW_YAWS) and all(v.size == (448, 448) for v in got)
    # The panorama's centre column is where green meets blue: ahead is
    # blue, a quarter turn right is yellow, behind is the seam of red, left
    # is green.
    assert [v.getpixel((224, 224)) for v in got] == [QUARTERS[2], QUARTERS[3],
                                                     QUARTERS[0], QUARTERS[1]]


@requires_geodata
def test_the_locator_is_handed_the_four_views(tmp_path):
    from mapify.config import load_config
    from mapify.models.base import RawCandidate
    from mapify.pipeline import AnalysisEngine

    class Recording:
        name = "stub"
        load_error = None
        loaded = True
        gallery_size = 1

        def __init__(self):
            self.handed = []

        def load(self):
            return True

        def shared_clip(self):
            return None, None

        def locate_with_embedding(self, image, top_k=12):
            self.handed.append(image)
            return [RawCandidate(48.8584, 2.2945, 0.2, self.name)], None

    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = False
    cfg.models.geoclip_enabled = False
    cfg.models.clip_evidence_enabled = False
    cfg.models.plonk_enabled = False     # a stand-in locator, alone
    engine = AnalysisEngine(cfg)
    engine.geoclip = Recording()
    buf = io.BytesIO()
    sphere().save(buf, "JPEG", quality=90)
    report = engine.analyse(buf.getvalue(), "360.jpg")
    assert report.ok, report.error
    assert report.image.get("panorama") is True
    handed = engine.geoclip.handed[0]
    assert isinstance(handed, list) and len(handed) == 4

    plain = io.BytesIO()
    Image.new("RGB", (1200, 800), (90, 120, 60)).save(plain, "JPEG")
    engine.geoclip.handed.clear()
    report = engine.analyse(plain.getvalue(), "street.jpg")
    assert "panorama" not in report.image
    assert isinstance(engine.geoclip.handed[0], Image.Image)
