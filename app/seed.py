"""Demo dataset for PackProof — fully staged, synthetic, no real people
or real transactions.

Cast:
- Buyer A (Karthik R.) — serial claimant: 2 old disputes + 2 live ones,
  reuses the same claim photo across disputes.
- Buyer B (Meena S.) — first-time claimant with genuine transit damage.
- Buyer C (Divya P.) — INR claim, but tracking shows DELIVERED.

Photos are drawn with PIL: pack photos show the item intact on the same
neutral background as the claim photos, so the vision module's colour /
hash / edge math has real signal to work on.
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, vision

BG = (216, 210, 198)
RED = (178, 44, 40)
BLUE = (42, 92, 168)
BLACK = (38, 38, 42)
WHITE = (232, 230, 226)
DARK = (30, 30, 30)


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (640, 480), BG)
    return img, ImageDraw.Draw(img)


def _mug(d: ImageDraw.ImageDraw, color) -> None:
    d.rounded_rectangle([220, 150, 400, 350], radius=18, fill=color, outline=DARK, width=3)
    d.arc([370, 190, 470, 310], start=270, end=90, fill=DARK, width=10)
    d.rectangle([220, 150, 400, 178], fill=tuple(max(0, c - 30) for c in color))


def _bottle(d: ImageDraw.ImageDraw, color) -> None:
    d.rounded_rectangle([270, 130, 370, 390], radius=26, fill=color, outline=DARK, width=3)
    d.rectangle([292, 92, 348, 132], fill=DARK)
    d.rectangle([270, 210, 370, 262], fill=WHITE, outline=DARK, width=2)


def _case(d: ImageDraw.ImageDraw, color) -> None:
    d.rounded_rectangle([240, 110, 400, 380], radius=30, fill=color, outline=DARK, width=3)
    d.ellipse([268, 138, 318, 188], outline=WHITE, width=5)


def _cable(d: ImageDraw.ImageDraw, color) -> None:
    d.arc([200, 140, 440, 380], start=20, end=340, fill=color, width=14)
    d.rectangle([408, 236, 452, 262], fill=DARK)


def _crack(d: ImageDraw.ImageDraw, seed: int, cx: int = 320, cy: int = 250) -> None:
    """Jagged branching crack web — plausible transit damage."""
    rng = random.Random(seed)
    for _ in range(12):
        x, y = cx, cy
        pts = [(x, y)]
        for _ in range(7):
            x += rng.randint(-40, 40)
            y += rng.randint(-32, 32)
            pts.append((x, y))
        d.line(pts, fill=DARK, width=4)


def _cut(d: ImageDraw.ImageDraw) -> None:
    """One bold straight slash — inconsistent with transit damage."""
    d.line([238, 330, 402, 168], fill=DARK, width=8)


_DRAWERS = {"mug": _mug, "bottle": _bottle, "case": _case, "cable": _cable}
_COLORS = {"red": RED, "blue": BLUE, "black": BLACK, "white": WHITE}


def _photo(name: str, kind: str, color_name: str, damage: str | None, seed: int = 7) -> str:
    img, d = _canvas()
    _DRAWERS[kind](d, _COLORS[color_name])
    if damage == "crack":
        _crack(d, seed)
    elif damage == "cut":
        _cut(d)
    path = config.MEDIA_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92)
    return f"media/{name}"


def generate_images() -> dict[str, str]:
    return {
        "pack_O1": _photo("pack_O1.jpg", "mug", "red", None),
        "claim_D1": _photo("claim_D1.jpg", "mug", "blue", "cut"),
        "pack_O2": _photo("pack_O2.jpg", "bottle", "blue", None),
        "claim_D2": _photo("claim_D2.jpg", "bottle", "blue", "crack"),
        "pack_O3": _photo("pack_O3.jpg", "case", "black", None),
        "pack_O4": _photo("pack_O4.jpg", "cable", "white", None),
        # Buyer A's second live dispute reuses the D1 claim photo file.
    }


def _hash(rel_path: str) -> str:
    return format(vision.average_hash(config.BASE_DIR / "app" / rel_path), "016x")


def seed(conn: sqlite3.Connection) -> bool:
    if conn.execute("SELECT COUNT(*) AS n FROM buyers").fetchone()["n"]:
        return False
    img = generate_images()

    conn.executemany(
        "INSERT INTO buyers (id, name, email) VALUES (?,?,?)",
        [("buyer-a", "Karthik R.", "karthik.r@example.com"),
         ("buyer-b", "Meena S.", "meena.s@example.com"),
         ("buyer-c", "Divya P.", "divya.p@example.com")],
    )
    conn.executemany(
        "INSERT INTO orders (id, item_name, sku, item_color, amount, buyer_id, "
        "tracking_status, created_at, delivered_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [("O-OLD-1", "Ceramic Dinner Plate — White", "PLT-WHT", "white", 32.0,
          "buyer-a", "delivered", "2026-09-03", "2026-09-07"),
         ("O-OLD-2", "Desk Lamp — Grey", "LMP-GRY", "grey", 54.0,
          "buyer-a", "delivered", "2026-10-02", "2026-10-06"),
         ("O1", "Aurora Ceramic Mug — Red", "MUG-RED", "red", 49.0,
          "buyer-a", "delivered", "2026-11-01", "2026-11-04"),
         ("O2", "Nimbus Steel Bottle — Blue", "BTL-BLU", "blue", 89.0,
          "buyer-b", "delivered", "2026-11-02", "2026-11-05"),
         ("O3", "Vertex Phone Case — Black", "CSE-BLK", "black", 25.0,
          "buyer-c", "delivered", "2026-11-03", "2026-11-06"),
         ("O4", "USB-C Braided Cable — White", "CBL-WHT", "white", 12.0,
          "buyer-a", "delivered", "2026-11-02", "2026-11-05")],
    )
    conn.executemany(
        "INSERT INTO packs (id, order_id, photo_path, weight_g, sealed_at) VALUES (?,?,?,?,?)",
        [("PK1", "O1", img["pack_O1"], 420, "2026-11-01T09:14:00Z"),
         ("PK2", "O2", img["pack_O2"], 680, "2026-11-02T11:02:00Z"),
         ("PK3", "O3", img["pack_O3"], 90, "2026-11-03T08:40:00Z"),
         ("PK4", "O4", img["pack_O4"], 60, "2026-11-02T15:21:00Z")],
    )
    snad = "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED"
    inr = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    conn.executemany(
        "INSERT INTO disputes (id, order_id, buyer_id, reason, amount, status, "
        "claim_photo_path, claim_photo_hash, claim_photo_taken_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("D-OLD-1", "O-OLD-1", "buyer-a", snad, 32.0, "RESOLVED_BUYER",
          None, None, None, "2026-09-10"),
         ("D-OLD-2", "O-OLD-2", "buyer-a", snad, 54.0, "RESOLVED_SETTLED",
          None, None, None, "2026-10-09"),
         ("D1", "O1", "buyer-a", snad, 49.0, "WAITING_FOR_SELLER_RESPONSE",
          img["claim_D1"], _hash(img["claim_D1"]), "2026-11-02", "2026-11-06"),
         ("D2", "O2", "buyer-b", snad, 89.0, "WAITING_FOR_SELLER_RESPONSE",
          img["claim_D2"], _hash(img["claim_D2"]), "2026-11-06", "2026-11-07"),
         ("D3", "O3", "buyer-c", inr, 25.0, "WAITING_FOR_SELLER_RESPONSE",
          None, None, None, "2026-11-07"),
         ("D4", "O4", "buyer-a", snad, 12.0, "WAITING_FOR_SELLER_RESPONSE",
          img["claim_D1"], _hash(img["claim_D1"]), "2026-11-06", "2026-11-07")],
    )
    conn.commit()
    return True
