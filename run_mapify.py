#!/usr/bin/env python3
"""Entry point for the packaged application.

``python -m mapify`` runs ``mapify/__main__.py`` as part of the package, so
its relative imports resolve. PyInstaller does not: it takes the entry script
and runs it as a top-level ``__main__`` with no parent package, and a relative
import there fails with

    ImportError: attempted relative import with no known parent package

So the frozen build points at this file instead, which imports absolutely.
Running it directly from a source checkout works too.
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    from mapify.desktop import run

    return run()


if __name__ == "__main__":
    # PyInstaller re-executes the bundled binary for any child process, and
    # torch's data loaders and pywebview both may spawn one. Without this the
    # application would relaunch its own window instead.
    multiprocessing.freeze_support()
    sys.exit(main())
