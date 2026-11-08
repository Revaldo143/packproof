"""Verdict engine: gates, bands, branches — the locked logic spec."""
from app import verdict as vm
from app.vision import VisionReport


def rep(**kw):
    base = dict(item_match=True, damage_present=True, damage_consistent=True,
                pack_intact=True, severity="functional", confidence=0.9, notes=[])
    base.update(kw)
    return VisionReport(**base)


def ctx(**over):
    c = {
        "dispute": {"id": "DX", "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
                    "amount": 80.0, "claim_photo_path": "media/c.jpg",
                    "claim_photo_taken_at": "2026-11-06"},
        "order": {"id": "OX", "delivered_at": "2026-11-04", "tracking_status": "delivered"},
        "pack": {"id": "PKX"},
        "buyer_prior_disputes": 0,
        "photo_reuse_count": 0,
    }
    c.update(over)
    return c


def test_fake_claim_defends():
    c = ctx(buyer_prior_disputes=3, photo_reuse_count=1)
    c["dispute"]["claim_photo_taken_at"] = "2026-11-02"  # before delivery
    r = vm.evaluate(c, rep(item_match=False, damage_consistent=False))
    assert r.verdict == "DEFEND"
    assert r.score >= 60


def test_genuine_claim_settles_at_severity_fraction():
    r = vm.evaluate(ctx(), rep())
    assert r.verdict == "SETTLE"
    assert r.score < 30
    assert r.settle_fraction == 0.5  # functional


def test_money_gate_settles_small_amount_without_scoring():
    c = ctx(buyer_prior_disputes=3)
    c["dispute"]["amount"] = 12.0
    r = vm.evaluate(c, rep(item_match=False, damage_consistent=False))
    assert r.verdict == "SETTLE"
    assert r.score is None
    assert r.breakdown[0]["signal"] == "money_gate"


def test_photo_reuse_bypasses_money_gate():
    c = ctx(photo_reuse_count=1, buyer_prior_disputes=2)
    c["dispute"]["amount"] = 12.0
    r = vm.evaluate(c, rep(item_match=False, damage_consistent=False))
    assert r.verdict == "DEFEND"
    signals = [b["signal"] for b in r.breakdown]
    assert "money_gate_exception" in signals and "photo_reuse" in signals


def test_no_pack_escalates():
    r = vm.evaluate(ctx(pack=None), rep())
    assert r.verdict == "ESCALATE"
    assert r.breakdown[0]["signal"] == "no_evidence_pack"


def test_missing_claim_photo_escalates():
    c = ctx()
    c["dispute"]["claim_photo_path"] = None
    r = vm.evaluate(c, None)
    assert r.verdict == "ESCALATE"


def test_low_confidence_escalates():
    r = vm.evaluate(ctx(), rep(confidence=0.3))
    assert r.verdict == "ESCALATE"


def test_mid_band_escalates():
    # Item mismatch (+40) + one prior dispute (+10) = 50 -> the wide band.
    c = ctx(buyer_prior_disputes=1)
    r = vm.evaluate(c, rep(item_match=False, damage_present=False,
                           damage_consistent=False, pack_intact=False))
    assert r.score == 50
    assert r.verdict == "ESCALATE"


def test_inr_delivered_defends_via_tracking():
    c = ctx()
    c["dispute"]["reason"] = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    r = vm.evaluate(c, None)
    assert r.verdict == "DEFEND" and r.path == "tracking"


def test_inr_lost_settles_full():
    c = ctx()
    c["dispute"]["reason"] = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    c["order"]["tracking_status"] = "lost"
    r = vm.evaluate(c, None)
    assert r.verdict == "SETTLE" and r.settle_fraction == 1.0
