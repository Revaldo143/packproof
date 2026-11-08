# PackProof — an AI dispute judge for online sellers

**Every box leaves with a witness.**

Online sellers lose PayPal disputes they should win, because the proof
that an item left the warehouse in perfect condition is scattered or
gone by the time a "broken item" claim lands. PackProof fixes that —
and then goes further: its agent doesn't just defend, it **judges**.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/)
(Best Use of PayPal + AI). Sandbox only — no real money, no real accounts.

## How it works

**Layer 1 — Evidence sealed before the dispute exists.**
At pack time, every order gets a sealed evidence pack: a photo of the
item, its weight, and a timestamp, stored against the order. When a
dispute arrives, the agent retrieves the pack instead of scrambling.

**Layer 2 — The 3-way verdict.**
On a new dispute, the agent compares the customer's claim photo against
the sealed pack photo and issues exactly one verdict:

- **DEFEND** — the claim doesn't hold up → the evidence pack and the AI
  comparison report are submitted via the Disputes API
  (`provide-evidence`)
- **SETTLE** — the damage reads genuine → a partial-refund offer is
  sent automatically (`make-offer`), sized by damage severity
- **ESCALATE** — the agent isn't sure → a human gets the full report.
  Ambiguity is never auto-decided.

**Layer 3 — Repeat-claimant radar.**
Dispute history is scanned for buyers who file again and again, and
claim photos are hash-matched across disputes — the same "damage" photo
used twice is caught instantly.

## The verdict logic (auditable, no black box)

The vision layer only *scores signals*; arithmetic and gates decide.
Every point appears on the dashboard next to the verdict.

Gates, in order:

0. **Reason routing** — "not as described" goes down the photo path;
   "item not received" goes down a tracking path (delivered → DEFEND
   with `PROOF_OF_FULFILLMENT`; lost → SETTLE).
1. **Money gate** — disputes of $15 or less settle automatically
   (fighting costs more than the item) — *unless* the claim photo is
   reused from another dispute. Serial patterns beat the money gate.
2. **Hard gates** — no sealed pack, no readable claim photo, or low
   vision confidence → ESCALATE. The agent never guesses.
3. **Fraud score (0–100)** — deterministic signals (different item in
   the claim photo +40, photo reuse +40, photo timestamp before
   delivery +35, repeat claimant +10/+25, pack photo intact +15,
   damage inconsistent with transit +15) and genuine signals (plausible
   transit damage −25, first-time clean buyer −10).
4. **Bands** — ≥60 DEFEND · 30–59 ESCALATE (deliberately wide) · <30
   SETTLE at 20%/50%/100% by damage severity (cosmetic/functional/
   destroyed).

Photo analysis is deterministic image math: foreground colour
signatures for item identity, perceptual hashes for reuse, and edge
analysis (density + PCA linearity) that separates a jagged crack web
from a single straight cut. A vision-LLM provider hook
(`VISION_API_*`) can fill the same report in production.

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
# open http://localhost:8000
```

The app seeds a staged demo store on first run: a serial claimant with
a fake claim (wrong item, pre-delivery photo, reused photo), a genuine
transit-damage claim, an "item not received" claim where tracking shows
DELIVERED, and a $12 claim that trips the photo-reuse exception.

### Modes: SIM and LIVE

- **SIM (default)** — with no credentials configured, PackProof runs a
  faithful local simulator of the PayPal sandbox Disputes lifecycle
  (same state transitions, same endpoints logged). The demo never dies.
- **LIVE** — copy `.env.example` to `.env`, add your sandbox app's
  client ID/secret (**the app must have the "Disputes" feature
  enabled**), and the same actions go to `api-m.sandbox.paypal.com`.
  Verify your setup first:

  ```bash
  .venv/bin/python scripts/paypal_live_smoke.py
  ```

API facts this build relies on (verified against PayPal's docs):
`provide-evidence` is **multipart/form-data** (JSON part named `input`,
files as `file1`, `file2`, … — `multipart/related` silently drops
files); `adjudicate` is a **sandbox-only** endpoint, used in the demo
to close the loop; dispute **webhooks arrive minutes late** in the
sandbox, so PackProof polls instead of waiting on them.

### Demo script (under 3 minutes)

1. **D1 — the fake claim.** Run the verdict: score 100, DEFEND.
   Wrong item, photo taken before delivery, third dispute from this
   buyer, same photo as D4. Defend → evidence submitted → adjudicate
   (sandbox) → seller wins.
2. **D2 — the genuine claim.** Score 0, SETTLE. Same bottle, jagged
   crack, first-time buyer. The agent offers 50% ($44.50) on its own —
   judgment, not a hammer.
3. **Radar** — one buyer, four disputes, one recycled photo.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

21 tests: the verdict gates and bands, the vision math on the seeded
photos, and the full API flow (evaluate → defend → adjudicate,
settle amounts, radar).

## Layout

```
app/
  main.py           FastAPI routes + dashboard
  verdict.py        the verdict engine (the logic spec above)
  vision.py         deterministic photo analysis
  paypal_client.py  LIVE sandbox REST client + SIM lifecycle simulator
  seed.py           staged demo data (PIL-drawn photos, no real images)
  db.py             SQLite schema
scripts/
  paypal_live_smoke.py   verify real sandbox credentials + Disputes API
tests/              engine, vision and API tests
```

## License

MIT — see [LICENSE](LICENSE).
