"""A durable record of one image's analysis.

The specification asks for a persistent structured case file per image,
and for provenance on every conclusion. Those are the same requirement
seen from two directions: a conclusion you cannot trace is not evidence,
it is an assertion.

A case file is one JSON document holding what was analysed, every
technique that ran and what it found, every technique that did *not* run
and why, and the tools and datasets involved. It is written to the
user's own state directory, keyed by a hash of the image bytes, so
re-analysing the same photograph appends a run rather than starting
again with no memory.

Why the skipped ones are kept
-----------------------------

A case file listing only what was found reads as though everything else
was looked for and absent. It was not: most of it could not be attempted
on that image. "No GPS tag in the file" and "GPS not checked" support
very different conclusions, and a reader who cannot tell them apart will
reach the wrong one.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import state_dir

__all__ = ["CaseFile", "case_dir"]


def case_dir() -> Path:
    path = state_dir() / "cases"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _case_id(data: bytes) -> str:
    """Identity is the image's content, not its filename.

    The same photograph renamed is the same photograph, and a case file
    keyed by name would quietly start over.
    """
    return hashlib.sha256(data).hexdigest()[:24]


@dataclass
class CaseFile:
    """One image, every run against it, and everything each run rested on."""

    case_id: str
    filename: str | None = None
    image: dict[str, Any] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ making

    @classmethod
    def for_image(cls, data: bytes, filename: str | None = None) -> "CaseFile":
        case_id = _case_id(data)
        existing = cls.load(case_id)
        if existing is not None:
            if filename:
                existing.filename = filename
            return existing
        return cls(case_id=case_id, filename=filename)

    def add_run(self, findings: list[Any], prediction: dict[str, Any] | None,
                provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record one analysis of this image."""
        ran = [f for f in findings if f.ran]
        run = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "techniques_run": len(ran),
            "techniques_found": sum(1 for f in ran if f.found),
            "techniques_skipped": sum(1 for f in findings if not f.ran),
            "prediction": prediction,
            "findings": [f.as_dict() for f in findings],
            "provenance": provenance or {},
        }
        self.runs.append(run)
        return run

    # ------------------------------------------------------------ storing

    @property
    def path(self) -> Path:
        return case_dir() / f"{self.case_id}.json"

    def save(self) -> Path:
        payload = {
            "case_id": self.case_id,
            "filename": self.filename,
            "image": self.image,
            "runs": self.runs,
        }
        # Written whole and renamed into place: a case file half-written
        # by a process that died is worse than no case file, because it
        # looks like a record.
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)
        return self.path

    @classmethod
    def load(cls, case_id: str) -> "CaseFile | None":
        path = case_dir() / f"{case_id}.json"
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return cls(case_id=raw.get("case_id", case_id),
                   filename=raw.get("filename"),
                   image=raw.get("image") or {},
                   runs=raw.get("runs") or [])

    # ------------------------------------------------------------ reading

    @property
    def latest(self) -> dict[str, Any] | None:
        return self.runs[-1] if self.runs else None

    def summary(self) -> dict[str, Any]:
        last = self.latest or {}
        return {
            "case_id": self.case_id,
            "filename": self.filename,
            "runs": len(self.runs),
            "first_seen": self.runs[0]["at"] if self.runs else None,
            "last_seen": last.get("at"),
            "techniques_run": last.get("techniques_run", 0),
            "techniques_found": last.get("techniques_found", 0),
            "techniques_skipped": last.get("techniques_skipped", 0),
            "path": str(self.path),
        }
