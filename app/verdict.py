"""PackProof verdict engine — the locked logic spec.

Gates run in a fixed order. The LLM/vision layer only *scores signals*;
arithmetic and gates decide. Every point is returned in the breakdown so
the dashboard (and a judge) can audit the decision.

Step 0  Route by dispute reason (SNAD -> photo path, INR -> tracking path)
Step 1  Hard gates: no sealed pack / no readable claim photo / low
        vision confidence -> ESCALATE (the agent never guesses). The
        hard gates run FIRST: a small amount never settles a claim
        the agent cannot even see evidence for.
Step 2  Money gate: amount <= $15 -> SETTLE, unless the claim photo is
        reused from another dispute (serial pattern beats the amount)
Step 3  Fraud score 0..100 from deterministic + vision signals
Step 4  Bands: >=60 DEFEND, 30..59 ESCALATE (deliberately wide), <30 SETTLE
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .vision import VisionReport

MONEY_GATE = 15.00
DEFEND_MIN = 60
ESCALATE_MIN = 30
MIN_CONFIDENCE = 0.6
SEVERITY_FRACTIONS = {"cosmetic": 0.20, "functional": 0.50, "destroyed": 1.00, "none": 0.50}

SNAD_REASONS = {"MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "NOT_AS_DESCRIBED", "SNAD"}
INR_REASONS = {"MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "ITEM_NOT_RECEIVED", "INR"}


@dataclass
class VerdictResult:
    verdict: str  # DEFEND | SETTLE | ESCALATE
    score: int | None
    path: str  # photo | tracking
    settle_fraction: float | None = None
    breakdown: list[dict] = field(default_factory=list)

    def add(self, signal: str, points: int, note: str) -> None:
        self.breakdown.append({"signal": signal, "points": points, "note": note})


def evaluate(ctx: dict, vision: VisionReport | None) -> VerdictResult:
    dispute = ctx["dispute"]
    order = ctx["order"]
    pack = ctx["pack"]
    amount = dispute["amount"]
    reason = (dispute["reason"] or "").upper()

    # ---- Step 0: route by reason -------------------------------------
    if reason in INR_REASONS:
        return _evaluate_inr(ctx)
    # Anything else falls through to the photo path (SNAD family).

    result = VerdictResult(verdict="ESCALATE", score=None, path="photo")
    reuse = ctx["photo_reuse_count"] > 0

    # ---- Step 1: hard gates (before the money gate) --------------------
    # A small amount never settles a claim the agent cannot see
    # evidence for: no pack / no claim photo / low vision confidence
    # all escalate first, whatever the amount is.
    if pack is None:
        result.add("no_evidence_pack", 0,
                   "no sealed evidence pack for this order: the agent cannot "
                   "defend what it cannot prove -> escalate")
        return result
    if not dispute.get("claim_photo_path") or vision is None:
        result.add("no_claim_photo", 0,
                   "claim photo missing or unreadable -> escalate")
        return result
    if vision.confidence < MIN_CONFIDENCE:
        result.add("low_confidence", 0,
                   f"vision confidence {vision.confidence:.2f} < {MIN_CONFIDENCE} "
                   "-> escalate; the agent never guesses")
        return result

    # ---- Step 2: money gate ------------------------------------------
    if amount <= MONEY_GATE and not reuse:
        result.verdict = "SETTLE"
        frac = SEVERITY_FRACTIONS.get(vision.severity, 0.5) if vision else 0.5
        result.settle_fraction = frac
        result.add("money_gate", 0,
                   f"amount ${amount:.2f} <= ${MONEY_GATE:.0f}: defending costs more "
                   "than the item is worth -> settle")
        return result
    if amount <= MONEY_GATE and reuse:
        result.add("money_gate_exception", 0,
                   "small amount, BUT the claim photo is reused from another "
                   "dispute: serial pattern beats the money gate -> score path")

    # ---- Step 3: fraud score ------------------------------------------
    score = 0
    if not vision.item_match:
        score += 40
        result.add("item_mismatch", 40,
                   "claim photo shows a different item/colour than the sealed pack")
    if reuse:
        score += 40
        result.add("photo_reuse", 40,
                   "this exact claim photo was already used in another dispute")
    taken = dispute.get("claim_photo_taken_at")
    delivered = order.get("delivered_at") if order else None
    if taken and delivered and taken < delivered:
        score += 35
        result.add("impossible_timestamp", 35,
                   f"claim photo taken {taken}, before delivery {delivered}")
    priors = ctx["buyer_prior_disputes"]
    if priors >= 2:
        score += 25
        result.add("buyer_history", 25, f"buyer has {priors} prior disputes")
    elif priors == 1:
        score += 10
        result.add("buyer_history", 10, "buyer has 1 prior dispute")
    if vision.pack_intact:
        score += 15
        result.add("pack_intact", 15, "sealed pack photo shows the item intact")
    if vision.damage_present and not vision.damage_consistent:
        score += 15
        result.add("damage_inconsistent", 15,
                   "damage pattern is a concentrated straight line, "
                   "inconsistent with transit damage")
    if vision.damage_present and vision.damage_consistent:
        score -= 25
        result.add("damage_plausible", -25,
                   "damage is jagged and spread: plausible transit damage")
    if priors == 0:
        score -= 10
        result.add("clean_buyer", -10, "first-time claimant with a clean history")

    score = max(0, min(100, score))
    result.score = score

    # ---- Step 4: bands --------------------------------------------------
    if score >= DEFEND_MIN:
        result.verdict = "DEFEND"
    elif score >= ESCALATE_MIN:
        result.verdict = "ESCALATE"
        result.add("band", 0,
                   f"score {score} in the {ESCALATE_MIN}-{DEFEND_MIN - 1} band: "
                   "ambiguity belongs to a human")
    else:
        result.verdict = "SETTLE"
        result.settle_fraction = SEVERITY_FRACTIONS.get(vision.severity, 0.5)
        result.add("band", 0,
                   f"score {score} < {ESCALATE_MIN}: claim reads genuine -> settle "
                   f"at {int(result.settle_fraction * 100)}% (severity: {vision.severity})")
    return result


def _evaluate_inr(ctx: dict) -> VerdictResult:
    order = ctx["order"]
    result = VerdictResult(verdict="ESCALATE", score=None, path="tracking")
    tracking = (order or {}).get("tracking_status", "")
    if tracking == "delivered":
        result.verdict = "DEFEND"
        result.add("tracking_delivered", 0,
                   "carrier tracking shows DELIVERED: defend with "
                   "PROOF_OF_FULFILLMENT (tracking evidence)")
    elif tracking == "lost":
        result.verdict = "SETTLE"
        result.settle_fraction = 1.0
        result.add("tracking_lost", 0,
                   "tracking shows lost: the buyer is right -> full refund")
    elif tracking == "in_transit":
        result.add("tracking_in_transit", 0,
                   "tracking shows in_transit: the item may still arrive -> "
                   "escalate; never auto-refund in full")
    else:
        result.add("tracking_unknown", 0,
                   f"tracking status '{tracking}' is not decisive -> escalate")
    return result
