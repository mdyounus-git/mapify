"""``python -m mapify`` opens the application window.

The analysis service is an implementation detail; to run it on its own use
``python -m mapify.server``.
"""

from .desktop import run

raise SystemExit(run())
