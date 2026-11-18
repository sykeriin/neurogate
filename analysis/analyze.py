"""Signal-detection analysis per participant x policy, plus H1/H2 paired tests
and (if statsmodels is installed) the H4 mixed-effects logistic model.

Input: a CSV with columns participant, policy, is_bad, rejected, wait_s,
load_at_show (simulate.py output, or logs converted with logs_to_csv()).

    python analysis/analyze.py logs/sim.csv
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

Z = NormalDist().inv_cdf


def dprime(hits: int, n_sig: int, fas: int, n_noise: int) -> tuple[float, float, float, float]:
    # log-linear correction (Hautus 1995) keeps rates off 0 and 1
    h = (hits + 0.5) / (n_sig + 1)
    f = (fas + 0.5) / (n_noise + 1)
    return h, f, Z(h) - Z(f), -0.5 * (Z(h) + Z(f))


def per_cell(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (pid, pol), g in df.groupby(["participant", "policy"]):
        bad, good = g[g.is_bad], g[~g.is_bad]
        h, f, d, c = dprime(int(bad.rejected.sum()), len(bad), int(good.rejected.sum()), len(good))
        out.append(dict(participant=pid, policy=pol, hit=h, fa=f, dprime=d, criterion=c,
                        wait_mean=g.wait_s.mean(), wait_p95=g.wait_s.quantile(.95)))
    return pd.DataFrame(out)


def paired(cells: pd.DataFrame, a: str, b: str, col: str = "dprime") -> dict:
    w = cells.pivot(index="participant", columns="policy", values=col)
    diff = (w[a] - w[b]).dropna()
    rng = np.random.default_rng(0)
    boots = [rng.choice(diff.values, len(diff)).mean() for _ in range(5000)]
    return dict(contrast=f"{a} - {b}", metric=col, mean=round(diff.mean(), 3),
                ci95=[round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)],
                dz=round(diff.mean() / (diff.std(ddof=1) + 1e-12), 3), n=len(diff))


def h4(df: pd.DataFrame):
    try:
        import statsmodels.formula.api as smf
    except ImportError:
        return "statsmodels not installed: pip install statsmodels (or fit glmer(missed ~ load + (1|participant) + (1|request)) in lme4)"
    bad = df[df.is_bad].copy()
    bad["missed"] = (~bad.rejected).astype(int)
    m = smf.mixedlm("missed ~ load_at_show", bad, groups=bad["participant"]).fit()
    return m.summary().as_text()


def logs_to_csv(log_dir: str, out: str) -> None:
    """Convert broker JSONL logs into the analysis CSV."""
    rows = []
    for p in Path(log_dir).glob("*.jsonl"):
        pid, pol = p.stem.split("_")[0], "_".join(p.stem.split("_")[1:-1])
        enq, forced = {}, {}
        for line in p.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e["event"] == "enqueue":
                enq[e["id"]] = e["t"]
            elif e["event"] == "show":
                forced[e["id"]] = e["forced"]
            elif e["event"] == "decision":
                rows.append(dict(participant=pid, policy=pol, request=e["id"], is_bad=e["is_bad"],
                                 rejected=e["decision"] == "reject", wait_s=e["shown_at"] - enq.get(e["id"], e["shown_at"]),
                                 load_at_show=e["load_at_show"], latency=e["latency"], forced=forced.get(e["id"], False)))
    pd.DataFrame(rows).to_csv(out, index=False)


def main(path: str):
    df = pd.read_csv(path)
    cells = per_cell(df)
    print("Mean by policy\n", cells.groupby("policy")[["hit", "fa", "dprime", "criterion", "wait_mean", "wait_p95"]].mean().round(3), "\n")
    print("H1", paired(cells, "neurogate", "immediate"))
    print("H1 (false alarms)", paired(cells, "neurogate", "immediate", "fa"))
    print("H2", paired(cells, "neurogate", "boundary"))
    print("vs modelled fatigue", paired(cells, "neurogate", "fatigue_budget"))
    if "forced" in df:
        print("\nFraction schedulable (not deadline-forced):", round(1 - df.forced.mean(), 3))
    print("\nH4\n", h4(df))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/sim.csv")
