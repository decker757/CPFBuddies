"""Which implementations a process ends up running on.

The bug this file exists to catch is not a crash. It is a test run, or a demo,
silently writing to a real DynamoDB table because the machine happened to have
credentials — or the reverse, a deployment that looks healthy and quietly keeps
every mandate in a dict that dies with the process. Both are invisible until
something is lost, so the selection logic is asserted rather than trusted.

Nothing here reaches AWS. The DynamoDB half runs under moto, the same way
`tests/test_store_contract.py` does.
"""

from __future__ import annotations

import pytest
from trustrail.settlement.queue.memory import InMemorySettlementQueue
from trustrail.stores.memory import (
    InMemoryAgentDirectory,
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
    InMemoryMerchantDirectory,
    InMemoryReviewHoldStore,
)

from app.resources import PERSISTENCE_ENV, QUEUE_URL_ENV, Resources

REGION = "ap-southeast-1"


@pytest.fixture
def aws(monkeypatch):
    """A mocked account with credentials that cannot reach anything real."""
    moto = pytest.importorskip("moto", reason="moto is needed for the AWS half")
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(name, value)
    with moto.mock_aws():
        yield


class TestInMemoryIsTheDefault:
    def test_every_store_is_in_memory(self):
        resources = Resources.in_memory()

        assert isinstance(resources.mandates, InMemoryMandateStore)
        assert isinstance(resources.kill_switch, InMemoryKillSwitchStore)
        assert isinstance(resources.audit, InMemoryAuditLog)
        assert isinstance(resources.merchants, InMemoryMerchantDirectory)
        assert isinstance(resources.agents, InMemoryAgentDirectory)
        assert isinstance(resources.holds, InMemoryReviewHoldStore)
        assert isinstance(resources.queue, InMemorySettlementQueue)

    def test_it_does_not_claim_to_be_durable(self):
        """`durable` is printed to operators, so a wrong answer is worse than none."""
        assert Resources.in_memory().durable is False

    def test_an_unset_environment_means_memory(self, monkeypatch):
        monkeypatch.delenv(PERSISTENCE_ENV, raising=False)

        assert isinstance(Resources.from_environment().mandates, InMemoryMandateStore)

    @pytest.mark.parametrize("value", ["", "false", "0", "no", "memory", "AWSX", "true"])
    def test_only_the_exact_word_aws_opts_in(self, monkeypatch, value):
        """Including `true`. This is not a boolean, and reading it as one would
        turn `TRUSTRAIL_PERSISTENCE=true` into an unintended write to a real table."""
        monkeypatch.setenv(PERSISTENCE_ENV, value)

        assert isinstance(Resources.from_environment().mandates, InMemoryMandateStore)

    def test_credentials_alone_never_opt_in(self, aws, monkeypatch):
        """The whole point: a developer with a live AWS profile still gets memory.

        `aws` sets real-looking credentials and a region. Without an explicit
        opt-in this must still be a dict, or every test run on a laptop with a
        configured profile becomes a write to whatever account it can reach.
        """
        monkeypatch.delenv(PERSISTENCE_ENV, raising=False)

        assert isinstance(Resources.from_environment().mandates, InMemoryMandateStore)


class TestAwsSelection:
    def test_state_moves_to_dynamo(self, aws, monkeypatch):
        monkeypatch.setenv(PERSISTENCE_ENV, "aws")
        monkeypatch.delenv(QUEUE_URL_ENV, raising=False)

        resources = Resources.from_environment()

        assert type(resources.mandates).__name__ == "DynamoMandateStore"
        assert type(resources.kill_switch).__name__ == "DynamoKillSwitchStore"
        assert type(resources.audit).__name__ == "DynamoAuditLog"
        assert type(resources.holds).__name__ == "DynamoReviewHoldStore"
        assert resources.durable is True

    def test_the_registries_stay_in_memory_and_say_so(self, aws, caplog):
        """These two have no Dynamo implementation, and that must be loud.

        Failing closed makes it survivable — an unseeded registry turns every
        charge into a deterministic FAIL rather than letting one through — but
        an operator who believes the registries persist will be surprised by a
        restart at the worst possible time.
        """
        with caplog.at_level("WARNING"):
            resources = Resources.from_aws()

        assert isinstance(resources.merchants, InMemoryMerchantDirectory)
        assert isinstance(resources.agents, InMemoryAgentDirectory)
        assert "re-seeded at boot" in caplog.text

    def test_a_queue_url_selects_sqs(self, aws, monkeypatch):
        monkeypatch.setenv(
            QUEUE_URL_ENV, f"https://sqs.{REGION}.amazonaws.com/123456789012/q"
        )

        assert type(Resources.from_aws().queue).__name__ == "SqsQueue"

    def test_no_queue_url_falls_back_to_memory_rather_than_refusing(self, aws, caplog):
        """A rail that will not start beats nothing, but not by much, mid-demo.

        Verification still works and charges still queue; they just do not
        survive a restart. That is a worse deployment and a better failure than
        an exception on boot.
        """
        with caplog.at_level("WARNING"):
            resources = Resources.from_aws(queue_url=None)

        assert isinstance(resources.queue, InMemorySettlementQueue)
        assert QUEUE_URL_ENV in caplog.text


class TestBuildRailHonoursThem:
    def test_the_rail_uses_the_resources_it_is_given(self):
        """Not a tautology: `build_rail` used to construct these itself."""
        from app.rail import build_rail

        resources = Resources.in_memory()
        rail = build_rail(resources=resources)

        assert rail.queue is resources.queue
        assert rail.audit is resources.audit
        assert rail.merchants is resources.merchants

    def test_omitting_them_still_works_offline(self):
        """Every existing test calls `build_rail()` with no resources at all."""
        from app.rail import build_rail

        assert isinstance(build_rail().queue, InMemorySettlementQueue)
