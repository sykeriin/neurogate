"""Workload estimation.

`SimulatedLoad` stands in for EEG so the whole system runs without hardware.
`BandPowerEstimator` is the real per-user pipeline: feed it raw EEG windows
(e.g. from an LSL inlet via pylsl/BrainFlow) after calibration.
"""
from __future__ import annotations

import math
import random
from collections import deque

import numpy as np

THETA = (4.0, 7.0)
ALPHA = (8.0, 12.0)


def band_power(x: np.ndarray, fs: float, band: tuple[float, float]) -> float:
    """Mean power in a band via Hann-windowed periodogram. x: (n_samples,)."""
    x = x - x.mean()
    w = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * w)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / fs)
    m = (freqs >= band[0]) & (freqs <= band[1])
    return float(spec[m].mean()) if m.any() else 0.0


class BandPowerEstimator:
    """Per-user relative load: z-scored log(frontal theta / parietal alpha).

    Calibrate on rest + 0/1/2-back windows, then call `update()` on each 2 s
    window (50% overlap). Output is a z-score against the user's own baseline,
    EMA-smoothed, plus a running uncertainty (std of recent raw z).
    """

    def __init__(self, fs: float, frontal_idx: list[int], parietal_idx: list[int], ema: float = 0.3):
        self.fs, self.f, self.p, self.ema = fs, frontal_idx, parietal_idx, ema
        self.mu = 0.0
        self.sd = 1.0
        self.z = 0.0
        self.recent: deque[float] = deque(maxlen=15)

    def feature(self, win: np.ndarray) -> float:
        """win: (n_channels, n_samples)."""
        th = np.mean([band_power(win[i], self.fs, THETA) for i in self.f])
        al = np.mean([band_power(win[i], self.fs, ALPHA) for i in self.p])
        return math.log((th + 1e-12) / (al + 1e-12))

    def calibrate(self, windows: list[np.ndarray]) -> None:
        feats = np.array([self.feature(w) for w in windows])
        self.mu, self.sd = float(feats.mean()), float(feats.std() + 1e-9)

    def update(self, win: np.ndarray) -> tuple[float, float]:
        raw = (self.feature(win) - self.mu) / self.sd
        self.recent.append(raw)
        self.z = self.ema * raw + (1 - self.ema) * self.z
        return self.z, float(np.std(self.recent)) if len(self.recent) > 2 else 1.0


class SimulatedLoad:
    """Plausible per-user load trace: slow drift + task-phase bumps + noise.

    Subtask boundaries produce load dips (Bailey & Iqbal 2008). Vigilance drift
    slowly raises baseline over the session.
    """

    def __init__(self, seed: int = 0, boundary_every_s: float = 90.0, miss: float = 0.0, false_rate: float = 0.0):
        """miss: share of real dips the app never reports as a boundary.
        false_rate: share of cycles with a spurious boundary event mid-subtask
        (e.g. a tab switch while still thinking). Both 0 = oracle boundaries."""
        self.rng = random.Random(seed)
        self.boundary_every = boundary_every_s
        self.phase_offset = self.rng.uniform(0, boundary_every_s)
        ev = random.Random(f"ev{seed}")
        self._missed = [ev.random() < miss for _ in range(500)]
        self._false = [ev.uniform(25, 65) if ev.random() < false_rate else None for _ in range(500)]

    def at_boundary(self, t: float) -> bool:
        k, pos = divmod(t + self.phase_offset, self.boundary_every)
        k = int(k) % 500
        if pos < 6.0 and not self._missed[k]:
            return True
        f = self._false[k]
        return f is not None and f <= pos < f + 6.0

    def true_load(self, t: float) -> float:
        cyc = ((t + self.phase_offset) % self.boundary_every) / self.boundary_every
        task = math.sin(math.pi * cyc)              # rises mid-subtask, falls at boundary
        drift = 0.4 * (t / 3600.0)                  # vigilance decrement
        return 1.2 * task + drift - 0.6

    def observe(self, t: float, noise: float = 0.6) -> float:
        """Noisy estimate, like a per-user EEG decoder with AUC ~0.7."""
        return self.true_load(t) + self.rng.gauss(0, noise)


class LoadHistory:
    """Rolling percentile of the user's own load, for 'bottom 40%' release."""

    def __init__(self, maxlen: int = 600):
        self.buf: deque[float] = deque(maxlen=maxlen)

    def add(self, z: float) -> None:
        self.buf.append(z)

    def percentile(self, z: float) -> float:
        if len(self.buf) < 10:
            return 0.5
        return sum(1 for v in self.buf if v <= z) / len(self.buf)
