"""Walking through the 3D model (mapify/web/js/walk.js), tested in Node.

The walking logic -- solid walls, open courtyards, finding the street
when a walk starts inside a building, sliding along a wall, storeys only
up to a building's height -- is plain JavaScript with no browser in it,
so it is tested directly. Skipped where Node is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_walking_logic():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "walk.test.mjs"), str(ROOT)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all passed" in result.stdout
