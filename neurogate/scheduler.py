"""The five scheduling policies compared in the study.

Each policy answers one question per tick: which queued requests to show now?
All policies share the same hard rules:
  * a request is always released once its deadline arrives;
  * Tier 0 is never held past its deadline;
  * an anti-flooding cap limits how many requests are shown per 10 minutes
    (deadline-forced releases bypass the cap so nothing is silently dropped).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .models import ApprovalRequest, Tier


@dataclass
class TickState:
    now: float
    load_z: float            # current smoothed load (z vs own baseline)
    load_pct: float          # percentile of load_z in user's own history
    at_boundary: bool        # subtask boundary from app events
    session_start: float


class Policy:
    name = "base"
    flood_cap = 12           # max non-forced releases per 600 s

    def __init__(self) -> None:
        self.recent_releases: deque[float] = deque()

    def _cap_ok(self, now: float) -> bool:
        while self.recent_releases and now - self.recent_releases[0] > 600:
            self.recent_releases.popleft()
        return len(self.recent_releases) < self.flood_cap

    def wants(self, req: ApprovalRequest, s: TickState) -> bool:
        raise NotImplementedError

    def select(self, queue: list[ApprovalRequest], s: TickState) -> list[ApprovalRequest]:
        out = []
        for r in sorted(queue, key=lambda r: (r.tier, r.deadline_at)):
            forced = s.now >= r.deadline_at
            if forced:
                out.append(r)
            elif self.wants(r, s) and self._cap_ok(s.now):
                out.append(r)
                self.recent_releases.append(s.now)
        return out


class Immediate(Policy):
    name = "immediate"
    flood_cap = 10_000

    def wants(self, r, s):
        return True


class FixedBatch(Policy):
    name = "batch"

    def __init__(self, every_s: float = 600):
        super().__init__()
        self.every = every_s

    def wants(self, r, s):
        if r.tier == Tier.T0:
            return True
        return int((s.now - s.session_start) % self.every) < 2


class BoundaryOnly(Policy):
    name = "boundary"

    def wants(self, r, s):
        return r.tier == Tier.T0 or s.at_boundary


class ModelledFatigue(Policy):
    """Re-implementation of the 'Oversight Has a Capacity' idea: a reviewer
    attention budget that depletes with each review and recovers over time."""
    name = "fatigue_budget"

    def __init__(self, capacity: float = 6.0, cost: float = 1.0, recovery_per_s: float = 1 / 90):
        super().__init__()
        self.capacity, self.cost, self.rec = capacity, cost, recovery_per_s
        self.budget = capacity
        self.last = None

    def wants(self, r, s):
        if self.last is not None:
            self.budget = min(self.capacity, self.budget + (s.now - self.last) * self.rec)
        self.last = s.now
        if r.tier == Tier.T0 or self.budget >= self.cost * 2:
            self.budget -= self.cost
            return True
        return False


class NeuroGate(Policy):
    """EEG + boundary fusion: release when load is in the user's bottom
    `pct` of their own history OR at a subtask boundary."""
    name = "neurogate"

    def __init__(self, pct: float = 0.40):
        super().__init__()
        self.pct = pct

    def wants(self, r, s):
        if r.tier == Tier.T0 and s.load_pct <= 0.6:
            return True
        return s.load_pct <= self.pct or s.at_boundary


class NeuroGateAnd(Policy):
    """The simple fix: release only at a boundary that the EEG confirms is a
    real dip (smoothed load in the user's bottom half). T0 behaves as in v1."""
    name = "neurogate_and"

    def __init__(self, pct: float = 0.5, ema: float = 0.15):
        super().__init__()
        self.pct, self.ema, self.z = pct, ema, None
        self.hist: deque[float] = deque(maxlen=600)

    def _smooth(self, s: TickState) -> float:
        self.z = s.load_z if self.z is None else self.ema * s.load_z + (1 - self.ema) * self.z
        self.hist.append(self.z)
        return sum(v <= self.z for v in self.hist) / len(self.hist) if len(self.hist) >= 10 else 0.5

    def select(self, queue, s):
        self.zpct = self._smooth(s)
        return super().select(queue, s)

    def wants(self, r, s):
        if r.tier == Tier.T0:
            return True
        return s.at_boundary and self.zpct <= self.pct


class TroughLock(NeuroGateAnd):
    """NeuroGate v2: risk-inverted, trough-locked release.

    Three changes over v1, each fixing a measured failure:
      1. Risk inversion. Every policy (and every product) shows Tier 0 at once
         because it's 'urgent'. But T0 deadlines (120-300 s) are longer than a
         subtask cycle, so the riskiest items can always wait for the next
         trough. v2 holds T0 *for* the trough instead of exempting it.
      2. Smoothed, confirmed troughs. v1 thresholded raw per-second load, so
         noise alone opened the gate within seconds of every arrival. v2 uses
         an EMA and requires a boundary AND a low smoothed percentile, with an
         EEG-only fallback when app boundaries are missing.
      3. One at a time. A trough releases the single riskiest item; the next
         item waits `spacing_s`, so decisions never bunch.
    Deadline safety: when slack runs low the item takes the first moment
    whose smoothed load is below the user's median, and the hard deadline
    force-release still applies.
    """
    name = "troughlock"
    flood_cap = 10_000

    def __init__(self, pct: float = 0.5, eeg_only_pct: float = 0.1, spacing_s: float = 3, urgent_s: float = 30,
                 use_eeg: bool = True):
        super().__init__(pct)
        self.use_eeg = use_eeg
        self.eeg_only_pct, self.spacing, self.urgent = eeg_only_pct, spacing_s, urgent_s
        self.last_release = -1e9
        self.gate: bool | None = None    # last trough verdict, for the UI

    def select(self, queue, s):
        zp = self.zpct = self._smooth(s)
        out = [r for r in queue if s.now >= r.deadline_at]           # hard deadline, never dropped
        rest = sorted((r for r in queue if r not in out), key=lambda r: (r.tier, r.deadline_at))
        if not rest or s.now - self.last_release < self.spacing:
            self.gate = None if not rest else self.gate
            return out
        if self.use_eeg:
            trough = (s.at_boundary and zp <= self.pct) or zp <= self.eeg_only_pct
        else:
            trough = s.at_boundary
        self.gate = bool(trough)
        nxt = rest[0]
        urgent = [r for r in rest if r.deadline_at - s.now <= self.urgent]
        if trough:
            out.append(nxt)
        elif urgent and (zp <= 0.5 or not self.use_eeg):
            out.append(urgent[0])
        if len(out) and out[-1] in rest:
            self.last_release = s.now
        return out


class TroughLockNoEEG(TroughLock):
    """Ablation: the same risk-inverted, one-at-a-time scheduler, using app
    boundaries only. TroughLock minus this = what the EEG itself adds."""
    name = "troughlock_noeeg"

    def __init__(self):
        super().__init__(use_eeg=False)


POLICIES = {p.name: p for p in (Immediate, FixedBatch, BoundaryOnly, ModelledFatigue, NeuroGate, NeuroGateAnd,
                                TroughLock, TroughLockNoEEG)}


def make_policy(name: str) -> Policy:
    return POLICIES[name]()
