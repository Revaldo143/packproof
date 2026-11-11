"""Photo analysis for PackProof.

Deterministic image math, no black box:
- average-hash (perceptual) for photo identity / reuse detection
- dominant colour for item-match
- edge density for damage presence
- edge row-entropy to separate jagged cracks (plausible transit damage)
  from a single straight cut (inconsistent with transit)

These produce the *signals* the verdict engine scores. In production the
same VisionReport can be filled by a vision LLM via the provider hook in
``analyze`` — the engine never cares where the report came from.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageFilter


def average_hash(path: str | Path, size: int = 8) -> int:
    img = Image.open(path).convert("L").resize((size, size))
    px = list(img.getdata())
    avg = sum(px) / len(px)
    bits = 0
    for i, p in enumerate(px):
        if p > avg:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dominant_color(path: str | Path) -> tuple[int, int, int]:
    """Foreground colour signature: the mean colour of the pixels that
    are NOT the background (background = the corner pixel's colour).

    A plain dominant-colour would just return the backdrop; the item is
    what differs from it.
    """
    img = Image.open(path).convert("RGB").resize((160, 120))
    bg = img.getpixel((0, 0))
    px = list(img.getdata())
    fg = [p for p in px if color_distance(p, bg) > 40]
    if not fg:
        return bg
    n = len(fg)
    return (sum(p[0] for p in fg) // n,
            sum(p[1] for p in fg) // n,
            sum(p[2] for p in fg) // n)


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _edge_map(path: str | Path) -> Image.Image:
    img = Image.open(path).convert("L").resize((320, 240))
    return img.filter(ImageFilter.FIND_EDGES)


def edge_density(path: str | Path) -> float:
    edges = _edge_map(path)
    px = list(edges.getdata())
    strong = sum(1 for p in px if p > 60)
    return strong / len(px)


def _added_edge_points(pack_path: str | Path, claim_path: str | Path):
    """Strong-edge pixels present in the claim photo but not the pack
    photo (pixel-aligned difference) — i.e. the damage itself, with the
    product outline factored out. Returns (points, added_density)."""
    pe = _edge_map(pack_path)
    ce = _edge_map(claim_path)
    w, h = ce.size
    pp, cp = pe.load(), ce.load()
    pts = [(x, y) for y in range(h) for x in range(w)
           if cp[x, y] > 60 and pp[x, y] <= 60]
    return pts, len(pts) / (w * h)


def edge_linearity(pts: list[tuple[int, int]]) -> float:
    """PCA linearity of a point set. Points on one straight cut have a
    single dominant principal axis (ratio >> 1); a jagged crack web
    spreads across two dimensions (ratio close to 1)."""
    if len(pts) < 30:
        return 0.0
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts) / n
    syy = sum((p[1] - my) ** 2 for p in pts) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts) / n
    trace = sxx + syy
    disc = math.sqrt(max(0.0, ((sxx - syy) / 2) ** 2 + sxy ** 2))
    lam1 = trace / 2 + disc
    lam2 = max(trace / 2 - disc, 1e-9)
    return lam1 / lam2


@dataclass
class VisionReport:
    item_match: bool
    damage_present: bool
    damage_consistent: bool
    pack_intact: bool
    severity: str  # cosmetic | functional | destroyed | none
    confidence: float
    notes: list[str] = field(default_factory=list)


def analyze(pack_photo: str | Path, claim_photo: str | Path) -> VisionReport:
    """Compare a sealed pack photo with a customer's claim photo."""
    notes: list[str] = []

    pack_hash = average_hash(pack_photo)
    claim_hash = average_hash(claim_photo)
    hash_dist = hamming(pack_hash, claim_hash)

    pack_color = dominant_color(pack_photo)
    claim_color = dominant_color(claim_photo)
    cdist = color_distance(pack_color, claim_color)
    item_match = cdist < 90
    notes.append(
        f"item colour distance {cdist:.0f}, photo hash distance {hash_dist} "
        f"-> item {'MATCHES' if item_match else 'DOES NOT MATCH'} the sealed pack"
    )

    pack_edges = edge_density(pack_photo)
    claim_edges = edge_density(claim_photo)
    pack_intact = pack_edges < 0.06
    added_pts, added_density = _added_edge_points(pack_photo, claim_photo)
    damage_present = added_density > 0.0025
    notes.append(
        f"edge density pack {pack_edges:.3f} vs claim {claim_edges:.3f}, "
        f"added damage edges {added_density:.3f} "
        f"-> damage {'present' if damage_present else 'not visible'}; "
        f"pack {'intact' if pack_intact else 'not clean'}"
    )

    damage_consistent = False
    severity = "none"
    if damage_present:
        linearity = edge_linearity(added_pts)
        damage_consistent = linearity < 12
        notes.append(
            f"damage linearity {linearity:.1f} -> "
            f"{'jagged web spread across the item (plausible transit damage)' if damage_consistent else 'a single straight line (inconsistent with transit damage)'}"
        )
        if added_density > 0.028:
            severity = "destroyed"
        elif added_density > 0.010:
            severity = "functional"
        else:
            severity = "cosmetic"

    # Confidence is how decisive the item-match evidence is. The match
    # call hinges on cdist < 90: far from that threshold the call is
    # clear. Near it (60..120) the colour alone cannot decide — but if
    # the two photos are structurally different items (average-hash
    # distance > 10; measured 0 for the same outline, 11+ for a
    # different one), the shape settles it and confidence stays high.
    # Only when the colour is borderline AND nothing structural
    # disambiguates is the report honest about not knowing: 0.5 is
    # below MIN_CONFIDENCE (0.6), so the verdict engine escalates
    # instead of guessing.
    colour_ambiguous = 60 <= cdist <= 120
    same_structure = hash_dist <= 10
    confidence = 0.5 if (colour_ambiguous and same_structure) else 0.9
    if confidence < 0.6:
        notes.append(
            f"item colour distance {cdist:.0f} is near the match threshold "
            f"(90) and the photos share the same structure (hash distance "
            f"{hash_dist}) -> match call is not decisive, confidence 0.50"
        )
    return VisionReport(
        item_match=item_match,
        damage_present=damage_present,
        damage_consistent=damage_consistent,
        pack_intact=pack_intact,
        severity=severity,
        confidence=confidence,
        notes=notes,
    )
