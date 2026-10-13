"""The 2D map's arithmetic (mapify/web/js/map2d.js), tested in Node.

The scale the globe hands over at and takes back, the uncertainty circle
drawn round a result, and building footprints as flat GeoJSON are plain
JavaScript with no browser in them, so they are tested directly. Skipped
where Node is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_map_arithmetic():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "map2d.test.mjs"), str(ROOT)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all passed" in result.stdout
