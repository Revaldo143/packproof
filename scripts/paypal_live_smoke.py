#!/usr/bin/env python3
"""PackProof — LIVE sandbox smoke test.

Run this after putting your PayPal sandbox credentials in .env.
It verifies, against the REAL PayPal sandbox (api-m.sandbox.paypal.com):

1. OAuth client-credentials token works.
2. The Disputes API answers (GET /v1/customer/disputes).

Then it prints the remaining manual steps for a full live cycle:
a test dispute must be created once from a sandbox buyer account
(PayPal only lets *customers* open disputes in the live flow; in the
sandbox you can also create one via the API with buyer credentials).

Usage:  .venv/bin/python scripts/paypal_live_smoke.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from app import config  # noqa: E402


def main() -> int:
    if not (config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET):
        print("✗ No credentials found. Copy .env.example to .env and fill in")
        print("  PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET from your sandbox app")
        print("  (the app must have the 'Disputes' feature enabled).")
        return 1
    print(f"→ POST {config.PAYPAL_BASE}/v1/oauth2/token")
    resp = requests.post(
        f"{config.PAYPAL_BASE}/v1/oauth2/token",
        data={"grant_type": "client_credentials"},
        auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"✗ OAuth failed: {resp.status_code} {resp.text[:300]}")
        return 1
    token = resp.json()["access_token"]
    print("✓ OAuth token OK")

    print(f"→ GET {config.PAYPAL_BASE}/v1/customer/disputes?page_size=5")
    resp = requests.get(
        f"{config.PAYPAL_BASE}/v1/customer/disputes?page_size=5",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"✗ Disputes list failed: {resp.status_code} {resp.text[:300]}")
        print("  Check that 'Disputes' is enabled in your sandbox app's features.")
        return 1
    items = resp.json().get("items", [])
    print(f"✓ Disputes API OK — {len(items)} dispute(s) visible to this app")
    for it in items:
        print(f"   - {it.get('dispute_id')} · {it.get('reason')} · {it.get('status')}")

    print()
    print("Next steps for one full live cycle:")
    print("1. In the sandbox, pay for a test order as the buyer, then open a")
    print("   dispute from that sandbox buyer account (or POST")
    print("   /v1/customer/disputes with buyer credentials, scope DISPUTE_CREATE).")
    print("2. Start PackProof with PACKPROOF_MODE=live and run the verdict +")
    print("   DEFEND action — provide-evidence goes to the real sandbox.")
    print("3. Webhooks arrive minutes late in the sandbox; PackProof polls,")
    print("   so no webhook setup is needed for the demo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
