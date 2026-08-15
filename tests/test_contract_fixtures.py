"""The golden corpus.

Every committed fixture must still produce the verdict it claims, and the JSON
on disk must still match what the code generates. Together those two make
`contracts/` trustworthy: workstreams B, C and D are coding against files, and
these tests are what stop those files quietly going stale.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trustrail.contracts.scenarios import Scenario, build_scenarios
from trustrail.models.verdict import CheckKind, Decision
from trustrail.verifier.service import VerifierService

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "fixtures"

SCENARIOS = build_scenarios()


def _ids(scenarios: list[Scenario]) -> list[str]:
    return [scenario.name for scenario in scenarios]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids(SCENARIOS))
def test_scenario_produces_its_expected_verdict(
    scenario: Scenario, verifier: VerifierService
) -> None:
    verdict = verifier.verify(scenario.request)

    assert verdict.decision is scenario.expected_decision
    assert tuple(verdict.reason_codes) == scenario.expected_reasons


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids(SCENARIOS))
def test_committed_fixture_matches_generated_scenario(scenario: Scenario) -> None:
    """Guards against editing a scenario and forgetting to re-export."""
    path = FIXTURE_DIR / f"{scenario.name}.json"
    assert path.exists(), f"run `python -m trustrail.contracts.export` to add {path.name}"

    committed = json.loads(path.read_text())

    assert committed["request"] == scenario.request.model_dump(mode="json")
    assert committed["expected"]["decision"] == scenario.expected_decision.value
    assert committed["expected"]["reason_codes"] == [
        code.value for code in scenario.expected_reasons
    ]


def test_no_stray_fixture_files() -> None:
    """A deleted scenario must not leave its fixture behind."""
    on_disk = {path.stem for path in FIXTURE_DIR.glob("*.json")}

    assert on_disk == {scenario.name for scenario in SCENARIOS}


def test_every_failure_is_attributable_to_a_named_check(
    verifier: VerifierService,
) -> None:
    """Nothing is rejected without a check and a reason code to point at."""
    for scenario in SCENARIOS:
        verdict = verifier.verify(scenario.request)
        if verdict.decision is Decision.PASS:
            continue
        rejecting = [c for c in verdict.checks if c.decision is not Decision.PASS]

        assert rejecting, f"{scenario.name} was rejected with no failing check"
        assert all(check.reason is not None for check in rejecting)
        assert all(check.detail for check in rejecting)


def test_deterministic_failures_are_flagged_as_non_overridable(
    verifier: VerifierService,
) -> None:
    """The approval UI reads this flag to decide whether to offer a button."""
    for scenario in SCENARIOS:
        verdict = verifier.verify(scenario.request)
        deterministic_failure = any(
            check.decision is Decision.FAIL
            and check.kind is CheckKind.DETERMINISTIC
            for check in verdict.checks
        )

        assert verdict.failed_deterministically is deterministic_failure
