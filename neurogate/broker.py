"""Approval broker: sits between the agent and the human.

Agents POST requests to /requests (or the scenario player feeds them).
The scheduler ticks once a second and pushes released requests to the
reviewer UI over /ws. Decisions come back on /decide and are logged
with the load at show-time and decision-time.

Load comes from the built-in simulator unless an external estimator
(e.g. an LSL/BrainFlow bridge) POSTs to /load.

Privacy: only the scalar load z and a boundary flag ever reach the broker.
Raw EEG stays in the estimator process.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .load import SimulatedLoad, LoadHistory
from .models import ApprovalRequest, Tier
from .scenarios import generate
from .scheduler import TickState, make_policy, POLICIES

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)


class Session:
    def __init__(self, participant: str, policy: str, seed: int, speed: float, scenario: bool):
        self.participant, self.policy_name, self.seed, self.speed = participant, policy, seed, speed
        self.policy = make_policy(policy)
        self.t0 = time.time()
        self.sim = SimulatedLoad(seed)
        self.hist = LoadHistory()
        self.external_load: tuple[float, float] | None = None   # (z, wall time)
        self.external_boundary = False
        self.queue: list[ApprovalRequest] = []
        self.shown: dict[str, ApprovalRequest] = {}
        self.done: list[ApprovalRequest] = []
        self.pending_script = generate(seed) if scenario else []
        self.log_path = LOGS / f"{participant}_{policy}_{int(self.t0)}.jsonl"
        self.enabled = True

    def t(self) -> float:
        return (time.time() - self.t0) * self.speed

    def load(self) -> tuple[float, bool]:
        t = self.t()
        if self.external_load and time.time() - self.external_load[1] < 5:
            return self.external_load[0], self.external_boundary
        return self.sim.observe(t), self.sim.at_boundary(t)

    def log(self, event: str, **kw) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, "t": round(self.t(), 2), **kw}) + "\n")


app = FastAPI(title="NeuroGate broker")
S: Session | None = None
clients: set[WebSocket] = set()


async def broadcast(msg: dict) -> None:
    for ws in list(clients):
        try:
            await ws.send_json(msg)
        except Exception:
            clients.discard(ws)


class StartBody(BaseModel):
    participant: str = "P00"
    policy: str = "troughlock"
    seed: int = 1
    speed: float = 1.0          # >1 compresses time for demos
    scenario: bool = True


@app.post("/session")
async def start(b: StartBody):
    global S
    if b.policy not in POLICIES:
        raise HTTPException(400, f"policy must be one of {list(POLICIES)}")
    S = Session(b.participant, b.policy, b.seed, b.speed, b.scenario)
    S.log("session_start", policy=b.policy, seed=b.seed, speed=b.speed)
    return {"ok": True, "log": S.log_path.name, "requests": len(S.pending_script)}


class ReqBody(BaseModel):
    action: str
    summary: str
    diff: dict
    provenance: str = ""
    tier: int = 1
    deadline_s: float = 600


@app.post("/requests")
async def submit(b: ReqBody):
    if not S:
        raise HTTPException(409, "no session")
    r = ApprovalRequest(b.action, b.summary, b.diff, b.provenance, Tier(b.tier), b.deadline_s / S.speed)
    S.queue.append(r)
    S.log("enqueue", **r.record())
    return {"id": r.id}


@app.get("/requests/{rid}")
async def status(rid: str):
    """Agents poll this to learn the decision."""
    for r in (S.done if S else []):
        if r.id == rid:
            return {"decision": r.decision, "reason": r.reason}
    return {"decision": None}


class LoadBody(BaseModel):
    z: float
    boundary: bool = False


@app.post("/load")
async def push_load(b: LoadBody):
    """External estimator hook (LSL/BrainFlow bridge)."""
    if S:
        S.external_load, S.external_boundary = (b.z, time.time()), b.boundary
    return {"ok": True}


class Decision(BaseModel):
    id: str
    decision: str
    reason: str | None = None


@app.post("/decide")
async def decide(d: Decision):
    if not S or d.id not in S.shown:
        raise HTTPException(404, "unknown request")
    r = S.shown[d.id]
    if r.tier == Tier.T0 and d.decision == "approve" and not d.reason:
        raise HTTPException(400, "Tier 0 approvals need a reason")
    S.shown.pop(d.id)
    z, _ = S.load()
    r.decided_at, r.decision, r.reason, r.load_at_decision = S.t(), d.decision, d.reason, z
    S.done.append(r)
    S.log("decision", **r.record(), latency=round(r.decided_at - r.shown_at, 2))
    return {"ok": True}


@app.post("/toggle")
async def toggle():
    """User kill switch: off = no load data used (falls back to boundary-only behaviour)."""
    if S:
        S.enabled = not S.enabled
        S.log("toggle", enabled=S.enabled)
    return {"enabled": S.enabled if S else None}


@app.get("/state")
async def state():
    if not S:
        return {"session": None}
    z, b = S.load()
    now = time.time()
    held = [{"id": r.id, "tier": int(r.tier), "action": r.action,
             "deadline_in": round((r.deadline_at - now) * S.speed, 1)} for r in sorted(S.queue, key=lambda r: (r.tier, r.deadline_at))]
    lat = [r.decided_at - r.shown_at for r in S.done if r.shown_at is not None]
    return {"policy": S.policy_name, "participant": S.participant, "t": round(S.t(), 1), "speed": S.speed,
            "load": round(z, 2), "pct": round(S.hist.percentile(z), 2), "smooth_pct": getattr(S.policy, "zpct", None),
            "gate": getattr(S.policy, "gate", None), "boundary": b, "queued": len(S.queue), "held": held,
            "shown": len(S.shown), "done": len(S.done), "remaining": len(S.pending_script),
            "approved": sum(r.decision == "approve" for r in S.done),
            "mean_latency": round(sum(lat) / len(lat), 1) if lat else None, "enabled": S.enabled}


@app.get("/debrief")
async def debrief():
    """Ground truth, revealed only once every scripted request is decided."""
    if not S:
        raise HTTPException(409, "no session")
    if S.pending_script or S.queue or S.shown:
        raise HTTPException(409, "session still running")
    rows = [{"action": r.action, "summary": r.summary, "tier": int(r.tier), "bad": r.is_bad, "kind": r.bad_kind,
             "decision": r.decision, "load": r.load_at_show} for r in S.done]
    bad = [r for r in rows if r["bad"]]
    good = [r for r in rows if not r["bad"]]
    return {"rows": rows, "caught": sum(r["decision"] == "reject" for r in bad), "bad": len(bad),
            "false_alarms": sum(r["decision"] == "reject" for r in good), "good": len(good)}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    clients.add(sock)
    try:
        if S:
            for r in S.shown.values():
                await sock.send_json({"type": "request", "req": r.public()})
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        clients.discard(sock)


async def ticker():
    while True:
        await asyncio.sleep(1.0)
        if not S:
            continue
        t = S.t()
        while S.pending_script and S.pending_script[0][0] <= t:
            off, r = S.pending_script.pop(0)
            r.created_at = S.t0 + off / S.speed     # wall-clock aligned
            r.deadline_s = r.deadline_s / S.speed
            S.queue.append(r)
            S.log("enqueue", **r.record())
        z, boundary = S.load()
        if not S.enabled:
            z = 0.0
        S.hist.add(z)
        st = TickState(now=time.time(), load_z=z, load_pct=S.hist.percentile(z) if S.enabled else 1.0,
                       at_boundary=boundary, session_start=S.t0)
        for r in S.policy.select(S.queue, st):
            S.queue.remove(r)
            r.shown_at, r.load_at_show = S.t(), z
            S.shown[r.id] = r
            forced = time.time() >= r.deadline_at
            S.log("show", id=r.id, load=z, pct=st.load_pct, boundary=boundary, forced=forced)
            await broadcast({"type": "request", "req": {**r.public(), "forced": forced, "load_pct": round(st.load_pct, 2)}})
        await broadcast({"type": "state", **(await state())})


@app.on_event("startup")
async def _go():
    asyncio.create_task(ticker())


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")
