"""PackProof — an AI dispute judge for online sellers.

Every box leaves with a witness: at pack time each order gets a sealed
evidence pack. When a PayPal dispute arrives, the agent compares the
claim against the pack and issues a 3-way verdict — DEFEND (submit
evidence), SETTLE (offer a partial refund), or ESCALATE (a human
decides). The LLM/vision layer scores signals; arithmetic decides.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, seed, verdict as verdict_mod, vision
from .paypal_client import PayPalError, get_client, now_iso

app = FastAPI(title="PackProof", version="1.0.0")
client = get_client()

STATIC_DIR = Path(__file__).parent / "static"


def _conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


@app.on_event("startup")
def _startup() -> None:
    conn = _conn()
    seed.seed(conn)
    conn.close()


def _vision_for(ctx: dict):
    """Vision report for a SNAD dispute, or None when it can't run."""
    dispute, pack = ctx["dispute"], ctx["pack"]
    if not dispute.get("claim_photo_path") or pack is None:
        return None
    base = config.BASE_DIR / "app"
    try:
        return vision.analyze(base / pack["photo_path"], base / dispute["claim_photo_path"])
    except Exception:
        return None


def _evaluate_and_store(conn, dispute_id: str) -> dict:
    ctx = db.get_dispute_ctx(conn, dispute_id)
    if ctx is None:
        raise HTTPException(404, "dispute not found")
    report = _vision_for(ctx)
    result = verdict_mod.evaluate(ctx, report)
    conn.execute(
        "INSERT INTO verdicts (dispute_id, verdict, score, path, settle_fraction, "
        "breakdown, created_at) VALUES (?,?,?,?,?,?,?)",
        (dispute_id, result.verdict, result.score, result.path,
         result.settle_fraction, json.dumps(result.breakdown), now_iso()),
    )
    conn.commit()
    return {
        "verdict": result.verdict,
        "score": result.score,
        "path": result.path,
        "settle_fraction": result.settle_fraction,
        "breakdown": result.breakdown,
        "vision": (None if report is None else {
            "item_match": report.item_match,
            "damage_present": report.damage_present,
            "damage_consistent": report.damage_consistent,
            "pack_intact": report.pack_intact,
            "severity": report.severity,
            "confidence": report.confidence,
            "notes": report.notes,
        }),
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "mode": config.MODE, "paypal_env": config.PAYPAL_ENV}


@app.get("/api/disputes")
def list_disputes() -> list[dict]:
    conn = _conn()
    rows = conn.execute("SELECT * FROM disputes ORDER BY created_at").fetchall()
    out = []
    for row in rows:
        ctx = db.get_dispute_ctx(conn, row["id"])
        out.append({
            "dispute": ctx["dispute"],
            "order": ctx["order"],
            "buyer": ctx["buyer"],
            "buyer_prior_disputes": ctx["buyer_prior_disputes"],
            "photo_reuse_count": ctx["photo_reuse_count"],
            "has_pack": ctx["pack"] is not None,
            "verdict": ctx["verdict"],
        })
    conn.close()
    return out


@app.get("/api/disputes/{dispute_id}")
def dispute_detail(dispute_id: str) -> dict:
    conn = _conn()
    ctx = db.get_dispute_ctx(conn, dispute_id)
    if ctx is None:
        conn.close()
        raise HTTPException(404, "dispute not found")
    report = _vision_for(ctx)
    ctx["vision"] = None if report is None else {
        "item_match": report.item_match,
        "damage_present": report.damage_present,
        "damage_consistent": report.damage_consistent,
        "pack_intact": report.pack_intact,
        "severity": report.severity,
        "confidence": report.confidence,
        "notes": report.notes,
    }
    actions = conn.execute(
        "SELECT * FROM actions WHERE dispute_id = ? ORDER BY id", (dispute_id,)
    ).fetchall()
    ctx["actions"] = [
        {**db.row_dict(a), "detail": json.loads(a["detail"])} for a in actions
    ]
    conn.close()
    return ctx


@app.post("/api/disputes/{dispute_id}/evaluate")
def evaluate(dispute_id: str) -> dict:
    conn = _conn()
    try:
        return _evaluate_and_store(conn, dispute_id)
    finally:
        conn.close()


class ActRequest(BaseModel):
    action: str  # defend | settle | escalate
    force: bool = False


@app.post("/api/disputes/{dispute_id}/act")
def act(dispute_id: str, req: ActRequest) -> dict:
    conn = _conn()
    try:
        ctx = db.get_dispute_ctx(conn, dispute_id)
        if ctx is None:
            raise HTTPException(404, "dispute not found")
        if ctx["verdict"] is None:
            _evaluate_and_store(conn, dispute_id)
            ctx = db.get_dispute_ctx(conn, dispute_id)
        v = ctx["verdict"]
        dispute = ctx["dispute"]
        action = req.action.lower()
        followed = (v["verdict"].lower() == action)
        base = config.BASE_DIR / "app"

        if action == "escalate":
            conn.execute("UPDATE disputes SET status = 'ESCALATED' WHERE id = ?",
                         (dispute_id,))
            conn.commit()
            db.log_action(conn, dispute_id, "escalate", config.MODE,
                          {"reason": "agent verdict or human choice: ambiguity "
                                     "belongs to a human",
                           "followed_verdict": followed}, now_iso())
            return {"status": "ESCALATED", "followed_verdict": followed}

        if action == "defend":
            if v["path"] == "tracking":
                evidence = {
                    "evidence_type": "PROOF_OF_FULFILLMENT",
                    "note": "Carrier tracking shows DELIVERED for this order. "
                            "See tracking record attached to the order.",
                    "documents": ["carrier tracking record"],
                    "file_paths": [],
                }
            else:
                pack = ctx["pack"]
                evidence = {
                    "evidence_type": "OTHER",
                    "note": "Sealed evidence pack photo taken at pack time "
                            "shows the item intact. Claim photo comparison "
                            "report attached.",
                    "documents": ["sealed pack photo", "AI comparison report"],
                    "file_paths": [str(base / pack["photo_path"])] if pack else [],
                }
            result = client.provide_evidence(conn, dispute_id, evidence)
            return {**result, "followed_verdict": followed}

        if action == "settle":
            frac = v["settle_fraction"] or 0.5
            amount = round(dispute["amount"] * frac, 2)
            note = (f"Partial refund offer ({int(frac * 100)}% of "
                    f"${dispute['amount']:.2f}) per PackProof verdict.")
            if frac >= 1.0:
                result = client.accept_claim(conn, dispute_id, amount,
                                             dispute["currency"])
            else:
                result = client.make_offer(conn, dispute_id, amount,
                                           dispute["currency"], note)
            return {**result, "offer_amount": amount, "followed_verdict": followed}

        raise HTTPException(400, f"unknown action: {action}")
    except PayPalError as e:
        raise HTTPException(502, str(e))
    finally:
        conn.close()


class AdjudicateRequest(BaseModel):
    outcome: str  # SELLER | BUYER


@app.post("/api/disputes/{dispute_id}/adjudicate")
def adjudicate(dispute_id: str, req: AdjudicateRequest) -> dict:
    conn = _conn()
    try:
        ctx = db.get_dispute_ctx(conn, dispute_id)
        if ctx is None:
            raise HTTPException(404, "dispute not found")
        outcome = req.outcome.strip().upper()
        if outcome not in ("SELLER", "BUYER"):
            raise HTTPException(400, f"invalid outcome: {req.outcome!r} "
                                 "(expected SELLER or BUYER)")
        return client.adjudicate(conn, dispute_id, outcome)
    except PayPalError as e:
        raise HTTPException(502, str(e))
    finally:
        conn.close()


@app.get("/api/radar")
def radar() -> dict:
    conn = _conn()
    repeat_buyers = conn.execute(
        "SELECT buyer_id, COUNT(*) AS n FROM disputes GROUP BY buyer_id "
        "HAVING n >= 2 ORDER BY n DESC"
    ).fetchall()
    buyers = []
    for r in repeat_buyers:
        b = db.row_dict(conn.execute(
            "SELECT * FROM buyers WHERE id = ?", (r["buyer_id"],)).fetchone())
        buyers.append({**b, "dispute_count": r["n"]})
    reused = conn.execute(
        "SELECT claim_photo_hash, COUNT(*) AS n FROM disputes "
        "WHERE claim_photo_hash IS NOT NULL GROUP BY claim_photo_hash HAVING n >= 2"
    ).fetchall()
    conn.close()
    return {"repeat_buyers": buyers,
            "reused_photos": [db.row_dict(r) for r in reused]}


@app.get("/api/actions")
def all_actions() -> list[dict]:
    conn = _conn()
    rows = conn.execute("SELECT * FROM actions ORDER BY id").fetchall()
    conn.close()
    return [{**db.row_dict(r), "detail": json.loads(r["detail"])} for r in rows]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# app/media/ is gitignored and only seeded at startup, but the mounts
# run at import — create it first so a fresh clone can start at all.
config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/media", StaticFiles(directory=config.MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
