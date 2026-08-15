"""Block-explorer links.

The demo ends by opening a transaction on Snowtrace, so these URLs are user-facing output, not
a debugging convenience.
"""

from __future__ import annotations

AVALANCHE_MAINNET = 43114
AVALANCHE_FUJI = 43113

_EXPLORERS: dict[int, str] = {
    AVALANCHE_MAINNET: "https://snowtrace.io",
    AVALANCHE_FUJI: "https://testnet.snowtrace.io",
}


def transaction_url(chain_id: int, tx_hash: str) -> str | None:
    """Explorer link for a transaction, or ``None`` on a chain without one (e.g. localhost)."""
    base = _EXPLORERS.get(chain_id)
    if base is None:
        return None
    normalised = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    return f"{base}/tx/{normalised}"
