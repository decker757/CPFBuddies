"""Verifier configuration.

Thresholds are config, not code — they get tuned during demo rehearsal, and a
number that needs a code change to move is a number nobody tunes.

Because they are tunable, every verdict carries `config_version`: a fingerprint
of the settings that produced it. Move a threshold and the audit trail shows
that the rules changed, rather than two verdicts silently disagreeing about the
same charge.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trustrail.canonical import canonical_json
from trustrail.models.evaluation import MAX_RISK_SCORE, MIN_RISK_SCORE
from trustrail.models.money import Currency
from trustrail.models.primitives import HexAddress
from trustrail.signing.crypto import hash_bytes
from trustrail.signing.eip712 import Eip712Domain

RiskScore = Annotated[int, Field(ge=MIN_RISK_SCORE, le=MAX_RISK_SCORE)]

_FINGERPRINT_LENGTH = 8


class VerifierConfig(BaseSettings):
    """Everything the decision function needs beyond the request itself."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTRAIL_", extra="forbid", frozen=True
    )

    issuer_address: HexAddress = Field(
        description="The address whose signatures make a mandate authentic. In "
        "production this is the KMS issuer key; the Verifier only ever needs "
        "the address, never the key."
    )
    settlement_currency: Currency = Field(
        default=Currency.XSGD,
        description="Charges in any other currency FAIL. XSGD on Avalanche "
        "C-Chain is a track rule, not a preference.",
    )
    domain: Eip712Domain = Field(default_factory=Eip712Domain)

    pass_score_max: RiskScore = Field(
        default=3, description="Risk scores at or below this settle straight through."
    )
    review_score_max: RiskScore = Field(
        default=7, description="Above this, the charge FAILs without a human."
    )

    clock_skew_seconds: Annotated[int, Field(ge=0, le=3600)] = Field(
        default=30,
        description="Expiry is brought forward by this much. Skew is applied in "
        "the safe direction only: a mandate can expire early, never late.",
    )
    review_hold_seconds: Annotated[int, Field(ge=1)] = Field(
        default=600,
        description="How long a REVIEW waits for a human. The hold also cannot "
        "outlive the mandate, so nothing becomes an indefinite pending queue.",
    )

    @model_validator(mode="after")
    def _check_bands_are_ordered(self) -> Self:
        if self.pass_score_max >= self.review_score_max:
            raise ValueError(
                "pass_score_max must be below review_score_max, otherwise the "
                "REVIEW band is empty and there is no human in the loop"
            )
        return self

    @classmethod
    def from_toml(cls, path: Path) -> Self:
        """Load settings from a TOML file, letting the environment override them.

        The precedence matters: the committed config carries a placeholder
        issuer address, and a deployment must be able to point at its real KMS
        key by setting `TRUSTRAIL_ISSUER_ADDRESS` alone. If the file won, that
        deployment would silently keep trusting the demo key.

        Overriding applies to top-level settings. The `[domain]` table is
        deliberately file-only: chain id and registry address are the things
        that should be written down and reviewed, not passed in at runtime.
        """
        # tomllib.load with a binary handle, not loads(read_text()): TOML is UTF-8 by
        # specification, and read_text() would decode with the platform default instead --
        # cp1252 on Windows. Harmless while the only non-ASCII here is in a comment, but it
        # would quietly change a parsed value the moment one appears in a string.
        with path.open("rb") as handle:
            from_file = tomllib.load(handle)
        return cls(**{k: v for k, v in from_file.items() if not _set_in_env(k)})

    @property
    def version(self) -> str:
        """A short fingerprint of these settings, stamped onto every verdict."""
        return hash_bytes(canonical_json(self))[2 : 2 + _FINGERPRINT_LENGTH]


def _set_in_env(setting: str) -> bool:
    prefix = VerifierConfig.model_config["env_prefix"]
    return f"{prefix}{setting}".upper() in os.environ
