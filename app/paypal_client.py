"""PayPal access layer: LIVE (real sandbox REST) or SIM (faithful local
simulator of the same Disputes lifecycle).

Both modes expose the same operations and log every action, so the demo,
the dashboard and the tests behave identically. SIM is the default when
no sandbox credentials are configured — the demo never dies; add
PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET to go LIVE against
api-m.sandbox.paypal.com.

Verified API facts baked into the LIVE client:
- provide-evidence is multipart/form-data: a JSON part named ``input``
  plus file parts ``file1``, ``file2``... (multipart/related silently
  drops the files).
- adjudicate / require-evidence are sandbox-only endpoints — exactly
  what a sandbox demo needs to close the loop.
- Dispute webhooks can arrive minutes late, so callers poll instead.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config, db


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PayPalError(RuntimeError):
    pass


# A local evidence file is missing/unreadable. This is a client error
# (our own evidence path is wrong), not a PayPal failure, so callers
# must map it to 4xx -- never to a 500 or a 502.
class EvidenceFileError(FileNotFoundError):
    pass


class BaseClient:
    mode = "base"

    def provide_evidence(self, conn, dispute_id: str, evidence: dict) -> dict:
        raise NotImplementedError

    def make_offer(self, conn, dispute_id: str, amount: float, currency: str, note: str) -> dict:
        raise NotImplementedError

    def accept_claim(self, conn, dispute_id: str, amount: float, currency: str) -> dict:
        raise NotImplementedError

    def adjudicate(self, conn, dispute_id: str, outcome: str) -> dict:
        raise NotImplementedError


class SimClient(BaseClient):
    """Local simulator: same state transitions as the sandbox lifecycle."""

    mode = "sim"

    def _set_status(self, conn, dispute_id: str, status: str) -> None:
        conn.execute("UPDATE disputes SET status = ? WHERE id = ?", (status, dispute_id))
        conn.commit()

    def provide_evidence(self, conn, dispute_id: str, evidence: dict) -> dict:
        detail = {
            "endpoint": f"POST /v1/customer/disputes/{dispute_id}/provide-evidence",
            "evidence_type": evidence.get("evidence_type", "OTHER"),
            "documents": evidence.get("documents", []),
            "note": evidence.get("note", ""),
            "simulated": True,
        }
        self._set_status(conn, dispute_id, "UNDER_REVIEW")
        db.log_action(conn, dispute_id, "provide_evidence", self.mode, detail, now_iso())
        return {"status": "UNDER_REVIEW", **detail}

    def make_offer(self, conn, dispute_id: str, amount: float, currency: str, note: str) -> dict:
        detail = {
            "endpoint": f"POST /v1/customer/disputes/{dispute_id}/make-offer",
            "offer_amount": {"value": f"{amount:.2f}", "currency_code": currency},
            "note": note,
            "simulated": True,
            "buyer_response": "ACCEPTED (simulated buyer)",
        }
        self._set_status(conn, dispute_id, "RESOLVED_SETTLED")
        db.log_action(conn, dispute_id, "make_offer", self.mode, detail, now_iso())
        return {"status": "RESOLVED_SETTLED", **detail}

    def accept_claim(self, conn, dispute_id: str, amount: float, currency: str) -> dict:
        detail = {
            "endpoint": f"POST /v1/customer/disputes/{dispute_id}/accept-claim",
            "refund_amount": {"value": f"{amount:.2f}", "currency_code": currency},
            "simulated": True,
        }
        self._set_status(conn, dispute_id, "RESOLVED_SETTLED")
        db.log_action(conn, dispute_id, "accept_claim", self.mode, detail, now_iso())
        return {"status": "RESOLVED_SETTLED", **detail}

    def adjudicate(self, conn, dispute_id: str, outcome: str) -> dict:
        # Sandbox-only endpoint: simulates PayPal settling the case.
        if outcome not in ("SELLER", "BUYER"):
            raise ValueError(f"invalid adjudication outcome: {outcome!r} "
                             "(expected SELLER or BUYER)")
        status = "RESOLVED_SELLER" if outcome == "SELLER" else "RESOLVED_BUYER"
        detail = {
            "endpoint": f"POST /v1/customer/disputes/{dispute_id}/adjudicate",
            "adjudication_outcome": outcome,
            "sandbox_only": True,
            "simulated": True,
        }
        self._set_status(conn, dispute_id, status)
        db.log_action(conn, dispute_id, "adjudicate", self.mode, detail, now_iso())
        return {"status": status, **detail}


class LiveClient(BaseClient):
    """Real PayPal sandbox REST calls via requests."""

    mode = "live"

    def __init__(self) -> None:
        self._token: str | None = None

    def _set_status(self, conn, dispute_id: str, status: str) -> None:
        # Same as SimClient: every PayPal action persists the dispute's
        # new lifecycle status to the local DB, so the dashboard, the
        # radar and the RESOLVED guards see the same state PayPal has.
        conn.execute("UPDATE disputes SET status = ? WHERE id = ?", (status, dispute_id))
        conn.commit()

    def _auth(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            f"{config.PAYPAL_BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise PayPalError(f"OAuth failed: {resp.status_code} {resp.text[:200]}")
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._auth()}",
                "Content-Type": "application/json"}

    def provide_evidence(self, conn, dispute_id: str, evidence: dict) -> dict:
        # multipart/form-data: JSON part named "input", files file1, file2...
        input_part = {
            "evidences": [{
                "evidence_type": evidence.get("evidence_type", "OTHER"),
                "notes": evidence.get("note", ""),
            }]
        }
        files: dict = {"input": (None, json.dumps(input_part), "application/json")}
        for i, path in enumerate(evidence.get("file_paths", []), start=1):
            # Fail fast, before any HTTP call: a missing local file is
            # our error (4xx), not PayPal's, and never a 500.
            if not Path(path).is_file():
                raise EvidenceFileError(
                    f"evidence file not found: {path} "
                    f"(dispute {dispute_id})")
            files[f"file{i}"] = (path.split("/")[-1], open(path, "rb"), "image/jpeg")
        resp = requests.post(
            f"{config.PAYPAL_BASE}/v1/customer/disputes/{dispute_id}/provide-evidence",
            headers={"Authorization": f"Bearer {self._auth()}"},
            files=files,
            timeout=60,
        )
        if resp.status_code not in (200, 201):
            raise PayPalError(f"provide-evidence failed: {resp.status_code} {resp.text[:300]}")
        detail = {"endpoint": f"POST /v1/customer/disputes/{dispute_id}/provide-evidence",
                  "evidence_type": input_part["evidences"][0]["evidence_type"],
                  "paypal_response_status": resp.status_code}
        self._set_status(conn, dispute_id, "UNDER_REVIEW")
        db.log_action(conn, dispute_id, "provide_evidence", self.mode, detail, now_iso())
        return {"status": "UNDER_REVIEW", **detail}

    def make_offer(self, conn, dispute_id: str, amount: float, currency: str, note: str) -> dict:
        body = {"offer_amount": {"value": f"{amount:.2f}", "currency_code": currency},
                "note": note}
        resp = requests.post(
            f"{config.PAYPAL_BASE}/v1/customer/disputes/{dispute_id}/make-offer",
            headers=self._headers(), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise PayPalError(f"make-offer failed: {resp.status_code} {resp.text[:300]}")
        detail = {"endpoint": f"POST /v1/customer/disputes/{dispute_id}/make-offer",
                  "offer_amount": body["offer_amount"],
                  "paypal_response_status": resp.status_code}
        self._set_status(conn, dispute_id, "OFFER_SENT")
        db.log_action(conn, dispute_id, "make_offer", self.mode, detail, now_iso())
        return {"status": "OFFER_SENT", **detail}

    def accept_claim(self, conn, dispute_id: str, amount: float, currency: str) -> dict:
        body = {"note": "Accepting the claim with a full refund.",
                "accept_claim_type": "REFUND_FULL",
                "refund_amount": {"value": f"{amount:.2f}", "currency_code": currency}}
        resp = requests.post(
            f"{config.PAYPAL_BASE}/v1/customer/disputes/{dispute_id}/accept-claim",
            headers=self._headers(), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise PayPalError(f"accept-claim failed: {resp.status_code} {resp.text[:300]}")
        detail = {"endpoint": f"POST /v1/customer/disputes/{dispute_id}/accept-claim",
                  "refund_amount": body["refund_amount"],
                  "paypal_response_status": resp.status_code}
        self._set_status(conn, dispute_id, "RESOLVED_SETTLED")
        db.log_action(conn, dispute_id, "accept_claim", self.mode, detail, now_iso())
        return {"status": "RESOLVED_SETTLED", **detail}

    def adjudicate(self, conn, dispute_id: str, outcome: str) -> dict:
        # Sandbox-only endpoint.
        body = {"adjudication_outcome": f"FAVOR_{outcome}"}
        resp = requests.post(
            f"{config.PAYPAL_BASE}/v1/customer/disputes/{dispute_id}/adjudicate",
            headers=self._headers(), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise PayPalError(f"adjudicate failed: {resp.status_code} {resp.text[:300]}")
        detail = {"endpoint": f"POST /v1/customer/disputes/{dispute_id}/adjudicate",
                  "adjudication_outcome": outcome, "sandbox_only": True,
                  "paypal_response_status": resp.status_code}
        self._set_status(conn, dispute_id,
                         "RESOLVED_SELLER" if outcome == "SELLER" else "RESOLVED_BUYER")
        db.log_action(conn, dispute_id, "adjudicate", self.mode, detail, now_iso())
        return {"status": "ADJUDICATED", **detail}


def get_client() -> BaseClient:
    return LiveClient() if config.MODE == "live" else SimClient()
