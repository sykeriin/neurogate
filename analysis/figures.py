"""Render the README figures from logs/sim.csv and logs/sweep.csv."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pathlib import Path

from neurogate.load import SimulatedLoad

OUT = Path("docs/img"); OUT.mkdir(parents=True, exist_ok=True)
BG, INK, MUTE, ACC, OK, BLUE, RED = "#0d1120", "#eef0f6", "#8d96b3", "#e8803a", "#4cc38a", "#6fa8ff", "#ff6b6b"
plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG, "axes.edgecolor": "#262f4d", "axes.labelcolor": MUTE,
                     "xtick.color": MUTE, "ytick.color": MUTE, "text.color": INK, "font.size": 11,
                     "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 160})


def dprime(g):
    bad, good = g[g.is_bad], g[~g.is_bad]
    h = (bad.rejected.sum() + .5) / (len(bad) + 1); f = (good.rejected.sum() + .5) / (len(good) + 1)
    from statistics import NormalDist
    z = NormalDist().inv_cdf
    return z(h) - z(f)


sim = pd.read_csv("logs/sim.csv")
order = ["immediate", "batch", "fatigue_budget", "neurogate", "boundary", "neurogate_and", "troughlock_noeeg", "troughlock"]
d = sim.groupby(["policy", "participant"]).apply(dprime).groupby("policy").agg(["mean", "sem"]).reindex(order)
fig, ax = plt.subplots(figsize=(9, 4.2))
cols = [ACC if p == "troughlock" else (BLUE if p == "troughlock_noeeg" else "#3a4366") for p in order]
ax.barh(order, d["mean"], xerr=1.96 * d["sem"], color=cols, error_kw={"ecolor": MUTE, "capsize": 3})
for i, v in enumerate(d["mean"]):
    ax.text(v + .03, i, f"{v:.2f}", va="center", color=INK, fontsize=10)
ax.set_xlabel("d′ (bad-action detection sensitivity)"); ax.set_title("Detection by scheduling policy · 48 synthetic reviewers", loc="left")
fig.tight_layout(); fig.savefig(OUT / "dprime.png")

sw = pd.read_csv("logs/sweep.csv")
s = sw[sw.noise == 0.6]
lab = [f"{m:.0%} / {f:.0%}" for m, f in zip(s["miss"], s["false"])]
fig, ax = plt.subplots(figsize=(9, 4.2))
for c, col, name in [("troughlock", ACC, "TroughLock (EEG + boundaries)"), ("troughlock_noeeg", BLUE, "TroughLock, boundaries only"),
                     ("boundary", "#5b6486", "Boundary-only"), ("immediate", RED, "Immediate")]:
    ax.plot(lab, s[c], "o-", color=col, lw=2.4, label=name)
ax.set_xlabel("app boundary events: missed / spurious"); ax.set_ylabel("expected hit rate")
ax.set_title("EEG is insurance when task signals get messy", loc="left"); ax.legend(frameon=False, fontsize=9)
fig.tight_layout(); fig.savefig(OUT / "robustness.png")

# what the scheduler sees
L = SimulatedLoad(3); t = np.arange(0, 420)
z = np.array([L.observe(x) for x in t]); b = np.array([L.at_boundary(x) for x in t])
ema = np.zeros_like(z); e = z[0]
for i, v in enumerate(z):
    e = .15 * v + .85 * e; ema[i] = e
fig, ax = plt.subplots(figsize=(9, 3.4))
ax.plot(t, z, color="#3a4366", lw=1, label="raw load z")
ax.plot(t, ema, color=ACC, lw=2.4, label="smoothed (EMA)")
thr = np.median(ema)
trough = b & (ema <= thr)
for i in np.where(trough)[0]:
    ax.axvspan(i - .5, i + .5, color=OK, alpha=.25, lw=0)
lo = min(z.min(), ema.min()) - .3
ax.vlines(np.where(b)[0], lo, lo + .25, color=BLUE, lw=1.2, label="task boundary")
ax.set_ylim(lo, max(z.max(), ema.max()) + .3)
ax.axhline(thr, ls="--", color=MUTE, lw=.8)
ax.set_xlabel("seconds"); ax.set_title("Release windows = task boundary AND smoothed load below median", loc="left")
ax.legend(frameon=False, fontsize=9, loc="upper right", ncol=3)
fig.tight_layout(); fig.savefig(OUT / "trace.png")
print("ok", list(OUT.iterdir()))
