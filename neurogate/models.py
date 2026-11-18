"""Core data types for NeuroGate approval requests."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Optional
import time
import uuid


class Tier(IntEnum):
    T0 = 0  # irreversible, high-stakes: never delayed past deadline, needs a reason
    T1 = 1  # reversible but consequential: schedulable
    T2 = 2  # low risk: batched


@dataclass
class ApprovalRequest:
    action: str                      # e.g. "send_email", "transfer", "delete_folder"
    summary: str                     # one-line human description
    diff: dict                       # what changes: recipient, amount, path, ...
    provenance: str                  # content that triggered the action
    tier: Tier
    deadline_s: float                # seconds after creation by which it must be shown
    is_bad: bool = False             # ground truth (hidden from the reviewer)
    bad_kind: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: float = field(default_factory=time.time)
    shown_at: Optional[float] = None
    decided_at: Optional[float] = None
    decision: Optional[str] = None   # "approve" | "reject"
    reason: Optional[str] = None
    load_at_show: Optional[float] = None
    load_at_decision: Optional[float] = None

    @property
    def deadline_at(self) -> float:
        return self.created_at + self.deadline_s

    def public(self) -> dict:
        """What the reviewer UI may see (no ground truth)."""
        d = asdict(self)
        d.pop("is_bad"); d.pop("bad_kind")
        d["tier"] = int(self.tier)
        return d

    def record(self) -> dict:
        d = asdict(self)
        d["tier"] = int(self.tier)
        return d
