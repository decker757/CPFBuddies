import hashlib
import json

from app.contracts import Listing


def calculate_basket_hash(items: list[Listing]) -> str:
    payload = [item.model_dump(mode="json") for item in items]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"0x{hashlib.sha256(encoded).hexdigest()}"
