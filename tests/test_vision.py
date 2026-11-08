"""Vision math on the seeded demo photos."""
import pytest

from app import config, vision

BASE = config.BASE_DIR / "app" / "media"


@pytest.fixture(scope="module", autouse=True)
def _images():
    from app import seed
    seed.generate_images()


def test_identical_photo_hash_distance_zero():
    a = vision.average_hash(BASE / "claim_D1.jpg")
    b = vision.average_hash(BASE / "claim_D1.jpg")
    assert vision.hamming(a, b) == 0


def test_fake_claim_item_mismatch_and_cut():
    r = vision.analyze(BASE / "pack_O1.jpg", BASE / "claim_D1.jpg")
    assert r.item_match is False
    assert r.damage_present is True
    assert r.damage_consistent is False  # single straight slash


def test_genuine_claim_match_and_plausible_crack():
    r = vision.analyze(BASE / "pack_O2.jpg", BASE / "claim_D2.jpg")
    assert r.item_match is True
    assert r.damage_present is True
    assert r.damage_consistent is True  # jagged crack web
    assert r.severity == "functional"
    assert r.pack_intact is True
