"""The ground's height in the 3D view (mapify/web/js/terrain.js), in Node.

Terrarium decoding, tile maths, bilinear sampling, the walker's sampler,
the mesh deck.gl's TerrainLayer draws, and which layers are lifted onto
the terrain. Skipped where Node is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_terrain_logic():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "terrain.test.mjs"), str(ROOT)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all passed" in result.stdout
