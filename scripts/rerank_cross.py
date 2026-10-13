#!/usr/bin/env python3
"""Train the re-ranker on one benchmark, test on the other: the honest
check before there is training material of its own (161 world photographs
learnt, 46 Indian ones tested, and the reverse; no photograph in both).

    python scripts/rerank_cross.py
"""
import copy, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import train_reranker as T
from mapify.config import load_config
from mapify.evidence.fusion import FusionEngine
from mapify.geo.offline import get_geocoder
geo = get_geocoder(); geo.use_geonames = True
cfg = copy.deepcopy(load_config().fusion); cfg.candidates_returned = T.KEEP
fe = FusionEngine(cfg, geo)
world = T.build_rows(T.load_benchmark("bench", ROOT / "samples"), fe)
india = T.build_rows(T.load_benchmark("india", ROOT / "samples" / "india"), fe)
for l2 in (0.05, 0.5):
    print(f"--- l2={l2}")
    m = T.train(world, l2=l2); T.compare(m, india, "train world -> India")
    m = T.train(india, l2=l2); T.compare(m, world, "train India -> world")
print("weights (world-trained, l2 0.5):", {f: round(w, 2) for f, w in zip(T.FEATURES, T.train(world, l2=0.5)["weights"])})
