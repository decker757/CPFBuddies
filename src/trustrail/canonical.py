"""One byte-stable encoding of a model, used wherever bytes must be reproducible.

Two places need to hash a model and get the same answer twice: the Evaluator's
signature over its findings, and the config fingerprint stamped onto every
verdict. Sorted keys and no incidental whitespace is all that takes — provided
everyone goes through this function, which is why it lives here rather than
being written out at each call site.
"""

from __future__ import annotations

import json

from pydantic import BaseModel


def canonical_json(model: BaseModel) -> bytes:
    """JSON with sorted keys and no whitespace, encoded as UTF-8."""
    payload = model.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
