"""API smoke: full demo flow through the simulator client."""
import os
import tempfile

os.environ["PACKPROOF_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["PACKPROOF_MODE"] = "sim"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_sim_mode():
    with client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["mode"] == "sim"


def test_seeded_disputes_listed():
    with client:
        r = client.get("/api/disputes")
        ids = [d["dispute"]["id"] for d in r.json()]
        assert ids == ["D-OLD-1", "D-OLD-2", "D1", "D2", "D3", "D4"]


def test_d1_defend_flow_and_adjudication():
    with client:
        r = client.post("/api/disputes/D1/evaluate")
        body = r.json()
        assert body["verdict"] == "DEFEND" and body["score"] == 100

        r = client.post("/api/disputes/D1/act", json={"action": "defend"})
        assert r.json()["status"] == "UNDER_REVIEW"
        assert r.json()["followed_verdict"] is True

        r = client.post("/api/disputes/D1/adjudicate", json={"outcome": "SELLER"})
        assert r.json()["status"] == "RESOLVED_SELLER"

        detail = client.get("/api/disputes/D1").json()
        assert detail["dispute"]["status"] == "RESOLVED_SELLER"
        actions = [a["action"] for a in detail["actions"]]
        assert actions == ["provide_evidence", "adjudicate"]


def test_d2_settle_offer_is_half():
    with client:
        r = client.post("/api/disputes/D2/evaluate")
        assert r.json()["verdict"] == "SETTLE"
        r = client.post("/api/disputes/D2/act", json={"action": "settle"})
        body = r.json()
        assert body["status"] == "RESOLVED_SETTLED"
        assert body["offer_amount"] == 44.5  # 50% of $89, functional damage


def test_d3_inr_defend_uses_tracking_evidence():
    with client:
        r = client.post("/api/disputes/D3/act", json={"action": "defend"})
        assert r.json()["status"] == "UNDER_REVIEW"


def test_d4_money_gate_exception_defends():
    with client:
        r = client.post("/api/disputes/D4/evaluate")
        body = r.json()
        assert body["verdict"] == "DEFEND"
        signals = [b["signal"] for b in body["breakdown"]]
        assert "money_gate_exception" in signals


def test_radar_flags_serial_buyer_and_reused_photo():
    with client:
        r = client.get("/api/radar").json()
        names = [b["name"] for b in r["repeat_buyers"]]
        assert "Karthik R." in names
        assert len(r["reused_photos"]) == 1
        assert r["reused_photos"][0]["n"] == 2


def test_unknown_dispute_404():
    with client:
        assert client.get("/api/disputes/NOPE").status_code == 404
