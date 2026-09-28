"""The technique framework: one registry, one shape of finding.

A 153-technique pipeline is unmanageable as 153 special cases wired into
the analyser. It is quite manageable as 153 small things that share one
interface, declare what they need, and are individually switchable --
which is what "modular architecture so every technique can be
independently enabled, replaced, or improved" asks for.

The distinction this framework exists to make
---------------------------------------------

**Reporting a clue and acting on it are different decisions.**

A technique that spots a yellow diamond road sign has found something
real, and it belongs in the evidence for the reader to weigh. Whether
that observation should also *move the coordinate* is a separate
question, and one with an answer that can only come from measurement.
Four such techniques were built in Phase 22, measured on the 163-sample
benchmark, and found to be inert or harmful when fed into the location
fusion -- road-sign convention moved zero of 163 samples, roof form and
street furniture each pushed the median error from 342 km to 368.

So a Finding carries its observation always, and carries a geographic
*constraint* only when feeding that constraint to the fusion has been
shown to help. ``feeds_fusion`` records which, and why, in the code
rather than in somebody's memory.

What a technique may not do
---------------------------

Invent. A technique that cannot run says so and returns
``Finding.not_applicable``; it does not guess. A technique that runs and
finds nothing returns a finding that says nothing was found. Silence and
absence are different, and a reader deserves to know which they are
looking at.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

log = logging.getLogger("mapify.techniques")

__all__ = [
    "Technique", "Finding", "Region", "Constraint", "Context",
    "technique", "REGISTRY", "run_all", "by_phase",
]


# What a technique can ask the analysis for. The orchestrator skips a
# technique whose needs are not met rather than letting it fail, and the
# case file records that it was skipped and why.
NEEDS = frozenset({
    "exif",        # EXIF was present and parsed
    "pixels",      # the decoded image
    "ocr",         # OCR ran and produced lines
    "embedding",   # the CLIP image embedding
    "evidence",    # the zero-shot prompt-group readings
    "candidates",  # the locator proposed somewhere
    "network",     # outbound requests are permitted
    "gps",         # an embedded GPS fix exists
    "time",        # a capture timestamp exists
})


@dataclass(frozen=True)
class Region:
    """A rectangle in the image, normalised 0..1, for the evidence map."""

    x0: float
    y0: float
    x1: float
    y1: float
    label: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"box": [round(self.x0, 4), round(self.y0, 4),
                        round(self.x1, 4), round(self.y1, 4)],
                "label": self.label}


@dataclass(frozen=True)
class Constraint:
    """A geographic restriction a finding implies, if any.

    Only attached by techniques whose effect on the location estimate has
    been measured. Everything else reports its observation and stops
    there.
    """

    kind: str                       # "countries" | "point" | "lat_band" | ...
    value: Any
    strength: float = 1.0           # 0..1, how hard it argues
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        value = self.value
        if isinstance(value, (set, frozenset)):
            value = sorted(value)
        return {"kind": self.kind, "value": value,
                "strength": round(self.strength, 3), "note": self.note}


@dataclass
class Finding:
    """What one technique concluded, and what it rests on."""

    technique: int
    key: str
    name: str
    phase: int
    # Did the technique actually run? A technique that could not run is
    # not the same as one that ran and found nothing.
    ran: bool = True
    # Did it find anything? False means "looked, saw nothing", which is
    # itself information.
    found: bool = False
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    # How much this technique believes its own finding, 0..1. Not a
    # location confidence -- that belongs to the fusion.
    confidence: float = 0.0
    regions: list[Region] = field(default_factory=list)
    constraint: Constraint | None = None
    sources: list[str] = field(default_factory=list)
    skipped_because: str = ""
    elapsed_ms: float = 0.0

    @classmethod
    def not_applicable(cls, spec: "Technique", why: str) -> "Finding":
        return cls(technique=spec.id, key=spec.key, name=spec.name,
                   phase=spec.phase, ran=False, found=False,
                   summary=f"Not applicable: {why}", skipped_because=why)

    @classmethod
    def nothing_found(cls, spec: "Technique", summary: str = "") -> "Finding":
        return cls(technique=spec.id, key=spec.key, name=spec.name,
                   phase=spec.phase, ran=True, found=False,
                   summary=summary or "Nothing of this kind in the image.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "technique": self.technique,
            "key": self.key,
            "name": self.name,
            "phase": self.phase,
            "ran": self.ran,
            "found": self.found,
            "summary": self.summary,
            "detail": self.detail,
            "confidence": round(self.confidence, 3),
            "regions": [r.as_dict() for r in self.regions],
            "constraint": self.constraint.as_dict() if self.constraint else None,
            "sources": self.sources,
            "skipped_because": self.skipped_because,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass(frozen=True)
class Technique:
    """One numbered technique from the specification."""

    id: int
    key: str
    name: str
    phase: int
    needs: frozenset[str]
    run: Callable[["Context"], Finding]
    # True only where measurement showed that letting this technique
    # constrain the location improves the benchmark. Everything else
    # reports and does not steer.
    feeds_fusion: bool = False
    # Why it does or does not, so the next person does not have to
    # rediscover it.
    fusion_note: str = ""
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "key": self.key, "name": self.name,
                "phase": self.phase, "needs": sorted(self.needs),
                "feeds_fusion": self.feeds_fusion,
                "fusion_note": self.fusion_note, "enabled": self.enabled}


@dataclass
class Context:
    """Everything a technique may look at.

    Deliberately a bag of already-computed things rather than a handle on
    the engine: a technique should not be able to trigger a model load,
    make an unbudgeted request, or re-run the pipeline. What it gets is
    what the analysis already paid for.
    """

    image: Any = None                 # PIL image, prepared
    raw_bytes: bytes = b""
    filename: str | None = None
    exif: Any = None                  # ExifReport
    ocr: Any = None                   # OcrResult
    script_reading: Any = None
    embedding: Any = None
    evidence: Any = None              # VisualEvidence
    candidates: list[Any] = field(default_factory=list)
    read_places: list[Any] = field(default_factory=list)
    cfg: Any = None
    engine: Any = None                # for the few that need a geocoder
    online: bool = False
    # What the analysis concluded, for the techniques that correlate
    # evidence (phases 5 and 10): the prediction as reported, the fusion
    # result behind it, and the locatability verdict.
    prediction: dict[str, Any] = field(default_factory=dict)
    fused: Any = None
    locatability: dict[str, Any] = field(default_factory=dict)
    # Shared, per-run results several techniques need -- the resolved
    # capture time, the sun at the leading candidate -- computed once by
    # whichever technique asks first. Also holds the findings made so far
    # ("findings", by id), because techniques run in number order and the
    # correlating ones read the earlier ones.
    memo: dict[str, Any] = field(default_factory=dict)

    def finding(self, technique_id: int):
        """An earlier technique's finding in this run, or None."""
        return self.memo.get("findings", {}).get(technique_id)

    def has(self, need: str) -> bool:
        """Is this need met? One place, so techniques never re-litigate it."""
        if need == "exif":
            return bool(getattr(self.exif, "has_exif", False))
        if need == "gps":
            return getattr(self.exif, "gps", None) is not None
        if need == "time":
            return bool(getattr(self.exif, "captured_at", None))
        if need == "pixels":
            return self.image is not None
        if need == "ocr":
            return bool(getattr(self.ocr, "lines", None))
        if need == "embedding":
            return self.embedding is not None
        if need == "evidence":
            return bool(getattr(self.evidence, "readings", None))
        if need == "candidates":
            return bool(self.candidates)
        if need == "network":
            return bool(self.online)
        return False

    def missing(self, needs: Iterable[str]) -> list[str]:
        return [n for n in needs if not self.has(n)]


REGISTRY: dict[int, Technique] = {}

# What each unmet need should say to a reader, in words rather than as a
# key. "Not applicable: no OCR text" beats "Not applicable: ocr".
_NEED_WORDS = {
    "exif": "the file carries no metadata",
    "gps": "the file carries no GPS tag",
    "time": "the file carries no capture timestamp",
    "pixels": "no decoded image",
    "ocr": "no text was read from the image",
    "embedding": "the image encoder did not run",
    "evidence": "the zero-shot pass did not run",
    "candidates": "no location was proposed",
    "network": "offline mode",
}


def technique(*, id: int, key: str, name: str, phase: int,
              needs: Iterable[str] = (), feeds_fusion: bool = False,
              fusion_note: str = "", enabled: bool = True):
    """Register one numbered technique."""

    def wrap(fn: Callable[[Context], Finding]) -> Callable[[Context], Finding]:
        # The specification's own wording and phase win over whatever the
        # module wrote, so a report names each technique exactly as it was
        # asked for. Ids outside the specification (tests) keep their own.
        from .spec import NAMES, phase_of
        spec_name = NAMES.get(id, name)
        spec_phase = phase_of(id) if id in NAMES else phase
        if id in NAMES and spec_phase != phase:
            raise ValueError(f"technique {id} is in phase {spec_phase} of the "
                             f"specification, not {phase}")
        need_set = frozenset(needs)
        unknown = need_set - NEEDS
        if unknown:
            raise ValueError(f"technique {id} declares unknown needs: {unknown}")
        if id in REGISTRY:
            raise ValueError(f"technique {id} is already registered "
                             f"as {REGISTRY[id].key}")
        REGISTRY[id] = Technique(
            id=id, key=key, name=spec_name, phase=spec_phase, needs=need_set,
            run=fn, feeds_fusion=feeds_fusion, fusion_note=fusion_note,
            enabled=enabled,
        )
        return fn

    return wrap


def by_phase(phase: int) -> list[Technique]:
    return [t for t in sorted(REGISTRY.values(), key=lambda t: t.id)
            if t.phase == phase]


def run_all(ctx: Context, phases: Iterable[int] | None = None,
            only: Iterable[int] | None = None) -> list[Finding]:
    """Run every applicable technique and collect what they found.

    Selection is automatic and by capability: a technique whose needs the
    image cannot meet is skipped with a reason rather than run and
    allowed to fail. That is the "automatically select applicable
    techniques based on the image" requirement, and it is also what keeps
    a photograph with no EXIF from producing fifteen tracebacks.

    A technique that raises is contained. One broken detector must not
    cost the other hundred and fifty-two, and the failure is recorded as
    a finding so it is visible rather than silently absent.
    """
    wanted = set(phases) if phases is not None else None
    chosen = set(only) if only is not None else None

    findings: list[Finding] = []
    for spec in sorted(REGISTRY.values(), key=lambda t: t.id):
        if chosen is not None and spec.id not in chosen:
            continue
        if wanted is not None and spec.phase not in wanted:
            continue
        if not spec.enabled:
            continue

        missing = ctx.missing(spec.needs)
        if missing:
            why = ", ".join(_NEED_WORDS.get(m, m) for m in missing)
            skipped = Finding.not_applicable(spec, why)
            findings.append(skipped)
            ctx.memo.setdefault("findings", {})[spec.id] = skipped
            continue

        started = time.perf_counter()
        try:
            found = spec.run(ctx)
        except Exception as exc:                      # pragma: no cover
            log.exception("technique %d (%s) failed", spec.id, spec.key)
            found = Finding(
                technique=spec.id, key=spec.key, name=spec.name,
                phase=spec.phase, ran=False, found=False,
                summary=f"Failed: {type(exc).__name__}: {exc}",
                skipped_because="the technique raised",
            )
        found.elapsed_ms = (time.perf_counter() - started) * 1000.0
        findings.append(found)
        ctx.memo.setdefault("findings", {})[spec.id] = found
    return findings
