"""The Verifier: a pure decision function.

Input: a mandate, a charge, the evaluator's evidence, and the state needed to
judge them. Output: PASS, REVIEW or FAIL with reason codes. No network calls, no
side effects, no clock. Every one of those absences is load-bearing — this is
the piece that has to be defensible under questioning, and the way to make it
defensible is to make it a function you can exhaustively test.

Two invariants are enforced here rather than trusted to the check authors:

1. A deterministic check can only ever produce FAIL. If one tried to return
   REVIEW, this module would still record FAIL — a fact does not get a
   human-override path.
2. Judgement checks never run until every deterministic check has passed. A risk
   score cannot rescue a forged signature, because the scoring never happens.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from trustrail import __version__
from trustrail.models.verdict import (
    CheckKind,
    CheckResult,
    Decision,
    Verdict,
)
from trustrail.models.verification import VerificationRequest
from trustrail.verifier.checks import (
    DETERMINISTIC_CHECKS,
    JUDGEMENT_CHECKS,
    Rejection,
    VerificationContext,
)
from trustrail.verifier.config import VerifierConfig

Check = Callable[[VerificationContext], Rejection | None]


class VerifierService:
    """Decides whether a charge may settle against a mandate."""

    def __init__(self, config: VerifierConfig) -> None:
        self._config = config

    def verify(self, request: VerificationRequest) -> Verdict:
        context = VerificationContext(request, self._config)
        results = _run_deterministic(context)
        if not _contains_failure(results):
            results.extend(_run_judgement(context))
        return self._assemble(request, results)

    def _assemble(
        self, request: VerificationRequest, results: list[CheckResult]
    ) -> Verdict:
        return Verdict(
            decision=Decision.worst([result.decision for result in results]),
            reason_codes=[r.reason for r in results if r.reason is not None],
            checks=results,
            mandate_id=request.signed_mandate.mandate.mandate_id,
            charge_id=request.charge.charge_id,
            risk_score=request.evaluation.evaluation.risk_score,
            evaluated_at=request.now,
            verifier_version=__version__,
            config_version=self._config.version,
        )


def _run_deterministic(context: VerificationContext) -> list[CheckResult]:
    """Run the facts, stopping at the first one that rejects.

    Stopping early means the trace shows the checks that ran, not the ones that
    would have. That is the honest record: once a signature is forged, nothing
    later in the list was ever consulted.
    """
    results: list[CheckResult] = []
    for check in DETERMINISTIC_CHECKS:
        rejection = check(context)
        results.append(_record(check, CheckKind.DETERMINISTIC, rejection))
        if rejection is not None:
            break
    return results


def _run_judgement(context: VerificationContext) -> Iterable[CheckResult]:
    """Run every judgement check; a human deserves all the reasons at once."""
    return [
        _record(check, CheckKind.JUDGEMENT, check(context))
        for check in JUDGEMENT_CHECKS
    ]


def _record(
    check: Check, kind: CheckKind, rejection: Rejection | None
) -> CheckResult:
    if rejection is None:
        return CheckResult(name=_name(check), kind=kind, decision=Decision.PASS)
    return CheckResult(
        name=_name(check),
        kind=kind,
        # Deterministic checks are facts, so their outcome is FAIL whatever the
        # check itself asked for. This is the one place that is enforced.
        decision=(
            Decision.FAIL
            if kind is CheckKind.DETERMINISTIC
            else rejection.decision
        ),
        reason=rejection.reason,
        detail=rejection.detail,
    )


def _contains_failure(results: Iterable[CheckResult]) -> bool:
    return any(result.decision is Decision.FAIL for result in results)


def _name(check: Check) -> str:
    return check.__name__.removeprefix("check_")
