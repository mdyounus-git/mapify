"""The numbered techniques, and the machinery they share.

Importing this package registers every technique that has been built.
The registry is keyed by the specification's own numbering, so a
technique can be found by the number it was asked for rather than by
whatever the code ended up calling it.

Phases are the specification's, not the project's build phases. Both
exist and they are not the same thing, which is confusing enough that
the distinction is worth stating once: a *technique phase* here is one
of the ten groups the 153 techniques were specified in; a *project
phase* is a unit of work in PHASES/.
"""

from .base import (
    REGISTRY, Constraint, Context, Finding, Region, Technique,
    by_phase, run_all, technique,
)
from .casefile import CaseFile, case_dir

# Importing for the side effect of registration. Each module registers
# its own block of technique numbers when it loads.
from . import phase1_metadata          # noqa: F401  techniques 1-15

__all__ = [
    "REGISTRY", "Constraint", "Context", "Finding", "Region", "Technique",
    "by_phase", "run_all", "technique", "CaseFile", "case_dir",
]
