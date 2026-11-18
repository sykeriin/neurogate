"""Where does EEG earn its place? Sweep app-boundary reliability and decoder
noise; report expected hit rate for TroughLock (EEG) vs its no-EEG ablation
and vs Boundary-only / Immediate."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd
from simulate import run

POLS = ["immediate", "boundary", "troughlock_noeeg", "troughlock"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 24
out = []
for miss, fr in [(0, 0), (0.2, 0.2), (0.3, 0.4), (0.4, 0.6)]:
    for noise in (0.3, 0.6, 1.0):
        rows = [x for pid in range(N) for p in POLS for x in run(p, pid, 1, noise, miss, fr)]
        df = pd.DataFrame(rows)
        e = df[df.is_bad].groupby(["participant", "policy"]).p_detect.mean().unstack()
        d = e.troughlock - e.troughlock_noeeg
        out.append(dict(miss=miss, false=fr, noise=noise, **e.mean().round(3).to_dict(),
                        eeg_gain=round(d.mean(), 3), eeg_gain_lo=round(d.mean() - 1.96 * d.std() / len(d) ** .5, 3),
                        wait_tl=round(df[df.policy == "troughlock"].wait_s.mean(), 1)))
        print(out[-1], flush=True)
pd.DataFrame(out).to_csv("logs/sweep.csv", index=False)
