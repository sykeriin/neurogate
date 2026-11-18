"""Offline simulation: run all five policies on the same seeded stream with a
synthetic reviewer whose detection falls with true load and with recent
review count (fatigue). Use it to sanity-check the scheduler and to power
the pilot (how big might the d' difference be?).

    python simulate.py --participants 24 --out logs/sim.csv
"""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import pandas as pd

from neurogate.load import SimulatedLoad, LoadHistory
from neurogate.scenarios import generate
from neurogate.scheduler import TickState, make_policy, POLICIES

DURATION = 4800


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def run(policy: str, pid: int, stream_seed: int, noise: float, miss: float = 0.0, false_rate: float = 0.0) -> list[dict]:
    rng = random.Random(pid * 1000 + sum(map(ord, policy)))
    sim = SimulatedLoad(seed=pid, miss=miss, false_rate=false_rate)
    hist = LoadHistory()
    pol = make_policy(policy)
    stream = [(t, r) for t, r in generate(stream_seed)]
    for i, (t, r) in enumerate(stream):
        r.created_at, r.idx = t, i          # idx is stable across policies (ids are random)
    queue, rows, recent = [], [], []
    skill = rng.gauss(1.4, 0.35)                     # per-participant baseline sensitivity
    for now in range(DURATION):
        while stream and stream[0][0] <= now:
            queue.append(stream.pop(0)[1])
        z = sim.observe(now, noise)
        hist.add(z)
        st = TickState(now, z, hist.percentile(z), sim.at_boundary(now), 0.0)
        for r in pol.select(queue, st):
            queue.remove(r)
            recent = [x for x in recent if now - x < 300]
            recent.append(now)
            true = sim.true_load(now)
            # catching a bad action: worse under load and under bunching
            p_hit = sigmoid(skill - 1.1 * true - 0.15 * (len(recent) - 1))
            p_fa = sigmoid(-3.0 + 0.4 * true)
            # common random numbers: the same request gets the same uniform draw
            # under every policy, so policy differences aren't sampling noise
            u = random.Random(f"{pid}-{r.idx}").random()
            rejected = u < (p_hit if r.is_bad else p_fa)
            rows.append(dict(participant=f"P{pid:02d}", policy=policy, request=r.id, tier=int(r.tier),
                             is_bad=r.is_bad, bad_kind=r.bad_kind, rejected=rejected, p_detect=p_hit if r.is_bad else p_fa,
                             wait_s=now - r.created_at, load_at_show=z, true_load=true,
                             forced=now >= r.deadline_at))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--participants", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--noise", type=float, default=0.6, help="decoder noise (higher = worse EEG)")
    ap.add_argument("--miss", type=float, default=0.0, help="share of real dips the app misses")
    ap.add_argument("--false-rate", type=float, default=0.0, help="share of cycles with a spurious boundary")
    ap.add_argument("--out", default="logs/sim.csv")
    a = ap.parse_args()
    rows = []
    for pid in range(a.participants):
        for p in POLICIES:
            rows += run(p, pid, a.seed, a.noise, a.miss, a.false_rate)
    df = pd.DataFrame(rows)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False)
    print(f"wrote {len(df)} decisions to {a.out}")


if __name__ == "__main__":
    main()
