"""PackProof configuration — env-driven, no secrets in code."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_DIR = BASE_DIR / "app" / "media"
DB_PATH = Path(os.environ.get("PACKPROOF_DB", BASE_DIR / "packproof.db"))


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_ENV = os.environ.get("PAYPAL_ENV", "sandbox")
PAYPAL_BASE = (
    "https://api-m.sandbox.paypal.com"
    if PAYPAL_ENV == "sandbox"
    else "https://api-m.paypal.com"
)

_MODE = os.environ.get("PACKPROOF_MODE", "auto").lower()
if _MODE == "live":
    MODE = "live"
elif _MODE == "sim":
    MODE = "sim"
else:  # auto
    MODE = "live" if (PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET) else "sim"
