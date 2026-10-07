"""The walk's weather and ambience (mapify/web/js/scene.js), tested in Node.

The weather is drawn from the reading and nothing else; the ambience --
illustrative people and vehicles -- stays on the mapped streets and keeps
moving. Plain JavaScript with no browser in it, so it is tested directly.
Skipped where Node is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_scene_logic():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "scene.test.mjs"), str(ROOT)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all passed" in result.stdout
