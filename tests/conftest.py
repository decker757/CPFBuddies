"""Shared test fixtures.

Everything is built from `ScenarioBuilder`, the same helper that generates the
committed contract fixtures. Tests and fixtures therefore exercise identical
objects — a test cannot pass against a shape the other workstreams are not
seeing.
"""

from __future__ import annotations

import pytest

from trustrail.contracts.scenarios import ScenarioBuilder, demo_config
from trustrail.mandate.service import MandateService
from trustrail.signing.local import LocalSigner
from trustrail.stores.memory import (
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
)
from trustrail.verifier.config import VerifierConfig
from trustrail.verifier.service import VerifierService


@pytest.fixture
def build() -> ScenarioBuilder:
    return ScenarioBuilder()


@pytest.fixture
def config() -> VerifierConfig:
    return demo_config()


@pytest.fixture
def verifier(config: VerifierConfig) -> VerifierService:
    return VerifierService(config)


@pytest.fixture
def signer() -> LocalSigner:
    return LocalSigner.generate()


@pytest.fixture
def audit() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def store() -> InMemoryMandateStore:
    return InMemoryMandateStore()


@pytest.fixture
def mandates(
    signer: LocalSigner, audit: InMemoryAuditLog, store: InMemoryMandateStore
) -> MandateService:
    return MandateService(
        signer=signer,
        store=store,
        kill_switch=InMemoryKillSwitchStore(),
        audit=audit,
    )
