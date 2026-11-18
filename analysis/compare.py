"""Quick policy comparison: empirical d', model-expected hit rate (low-noise),
load at show by tier, waits, and paired contrasts against Boundary-only."""
import sys
import pandas as pd
sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from analyze import per_cell, paired

df = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else "logs/sim.csv")
c = per_cell(df)
exp = df[df.is_bad].groupby(["participant", "policy"]).p_detect.mean().rename("exp_hit").reset_index()
c = c.merge(exp, on=["participant", "policy"])
print(c.groupby("policy")[["dprime", "hit", "fa", "exp_hit", "wait_mean", "wait_p95"]].mean().round(3).sort_values("exp_hit"))
print(df.groupby(["policy", "tier"]).true_load.mean().unstack().round(2))
print("forced:", df.groupby("policy").forced.mean().round(3).to_dict())
for a, b in [("troughlock", "boundary"), ("troughlock", "immediate"), ("neurogate", "boundary"), ("neurogate_and", "boundary"), ("troughlock", "troughlock_noeeg")]:
    print(paired(c, a, b), "| exp_hit:", paired(c, a, b, "exp_hit")["mean"], paired(c, a, b, "exp_hit")["ci95"])
