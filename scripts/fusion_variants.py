#!/usr/bin/env python3
"""Compare ways of weighing the evidence on exactly the same evidence.

The expensive part of an analysis -- GeoCLIP, the CLIP readings, OCR -- does
not depend on how the evidence is weighed, so it runs once per photograph
and its inputs to fusion are cached under bench/cache/fusion/<set>/ (one
pickle each; a stopped run resumes). Each variant then re-fuses those inputs
in a fraction of a second:

  baseline            the fusion at a git revision (--baseline, default HEAD)
  baseline+geonames   the same, GeoNames consulted for read place names
  working tree        the fusion as it is now in the checkout
  working+geonames    the same, GeoNames consulted

and every variant is compared photograph by photograph with the first.

    python scripts/fusion_variants.py india bench
    python scripts/fusion_variants.py bench --baseline 0fd11a33
    python scripts/fusion_variants.py bench --cached-only   # what is cached, no analysis

Sets: bench (samples/, scripts/fetch_samples.py), india (samples/india/,
--frame india), text (samples/text/, scripts/fetch_text_samples.py).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pickle
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                          # noqa: E402
from mapify.evidence import fusion as fusion_new               # noqa: E402
from mapify.geo.geodesy import haversine_km                    # noqa: E402

SETS = {"bench": ROOT / "samples", "india": ROOT / "samples" / "india",
        "text": ROOT / "samples" / "text"}
BANDS = [("street", 1), ("city", 25), ("region", 200), ("country", 750), ("continent", 2500)]
CACHE = ROOT / "bench" / "cache" / "fusion"


def fusion_at(revision: str):
    """mapify/evidence/fusion.py as it was at ``revision``, as a module."""
    source = subprocess.run(["git", "show", f"{revision}:mapify/evidence/fusion.py"],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout
    path = ROOT / "bench" / "tmp" / f"fusion_at_{revision.replace('/', '_')}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    name = f"mapify.evidence.fusion_at_{abs(hash(revision))}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                     # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


def inputs_for(eng, set_name, sample, folder):
    cache = CACHE / set_name / f"{sample['site']}.pkl"
    if cache.exists():
        try:
            return pickle.loads(cache.read_bytes())
        except Exception:                       # cut short by a killed run
            cache.unlink(missing_ok=True)
    if eng is None:
        return None
    data = (folder / sample["file"]).read_bytes()
    rep = eng.analyse(data, filename=sample["file"])
    got = None
    if rep.ok and rep.locatable and rep.raw_candidates:
        got = {"raw": list(rep.raw_candidates), "evidence": rep.visual_evidence,
               "ocr": rep.ocr_result, "script": getattr(rep, "script_reading", None),
               "read_places": list(getattr(rep, "read_place_objs", []) or []),
               "pipeline_pred": (rep.prediction.get("estimated") or {})}
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(got))
    tmp.replace(cache)
    return got


def run_set(set_name, eng, geocoder, fusion_cfg, variants):
    folder = SETS[set_name]
    samples = [s for s in json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["samples"]
               if (folder / s["file"]).is_file()]
    results = {v: {} for v in variants}
    t0 = time.time()
    for i, s in enumerate(samples, 1):
        got = inputs_for(eng, set_name, s, folder)
        if got is None:
            continue
        for name, (module, geonames) in variants.items():
            geocoder.use_geonames = geonames
            fused = module.FusionEngine(fusion_cfg, geocoder).fuse(
                got["raw"], evidence=got["evidence"], ocr=got["ocr"],
                script_reading=got["script"], read_places=got["read_places"] or None)
            if fused.candidates:
                top = fused.candidates[0]
                results[name][s["site"]] = haversine_km(top.latitude, top.longitude,
                                                        s["true_lat"], s["true_lon"])
        if eng is not None and i % 10 == 0:
            print(f"  {set_name}: {i}/{len(samples)} ({time.time() - t0:.0f} s)", flush=True)
    return results


def report(set_name, results):
    print(f"\n=== {set_name} ===")
    base = next(iter(results))
    for name, errs in results.items():
        e = list(errs.values())
        if not e:
            continue
        cells = "  ".join(f"<={km}km {sum(x <= km for x in e) / len(e):4.0%}" for _, km in BANDS)
        print(f"{name:22} n={len(e)}  {cells}  median {statistics.median(e):7.1f} km")
    for name, errs in list(results.items())[1:]:
        changed = [(s, results[base][s], e) for s, e in errs.items()
                   if s in results[base] and abs(results[base][s] - e) > 1]
        better = sum(1 for _, b, e in changed if e < b)
        print(f"\n{name} vs {base}: {len(changed)} changed, {better} better, "
              f"{len(changed) - better} worse")
        for s, b, e in sorted(changed, key=lambda c: c[2] - c[1]):
            print(f"   {s:26} {b:8.0f} -> {e:8.0f} km")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sets", nargs="*", default=["india"], choices=sorted(SETS))
    ap.add_argument("--baseline", default="HEAD", help="git revision to compare against")
    ap.add_argument("--cached-only", action="store_true",
                    help="use what is cached; analyse nothing")
    args = ap.parse_args()

    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = True
    cfg.models.plonk_enabled = False            # PLONK is measured on its own cache
    eng = None
    if not args.cached_only:
        from mapify.pipeline import AnalysisEngine
        eng = AnalysisEngine(cfg)
    from mapify.geo.offline import get_geocoder
    geocoder = eng.geocoder if eng is not None else get_geocoder()
    base = fusion_at(args.baseline)
    variants = {"baseline": (base, False), "baseline+geonames": (base, True),
                "working tree": (fusion_new, False), "working+geonames": (fusion_new, True)}
    out = {}
    for set_name in args.sets:
        results = run_set(set_name, eng, geocoder, cfg.fusion, variants)
        report(set_name, results)
        out[set_name] = results
    (ROOT / "bench" / f"fusion-variants-{'-'.join(args.sets)}.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
