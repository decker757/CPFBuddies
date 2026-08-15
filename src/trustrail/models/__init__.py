"""The wire contract.

These models are what workstreams B, C and D code against. They are exported to
JSON Schema in `contracts/schemas/` and exercised by the golden fixtures in
`contracts/fixtures/`, so the schemas, the fixtures and this code cannot drift
apart without a test failing.
"""

from trustrail.models.audit import AuditEntry, AuditEventType
from trustrail.models.charge import Charge
from trustrail.models.evaluation import (
    EvaluationSubject,
    EvaluatorFlags,
    EvaluatorOutput,
    SignedEvaluatorOutput,
)
from trustrail.models.mandate import (
    Mandate,
    MandateBinding,
    MandateRecord,
    MandateState,
    MandateStatus,
    SignedMandate,
)
from trustrail.models.money import CURRENCY_DECIMALS, Currency, Money
from trustrail.models.primitives import (
    ZERO_ADDRESS,
    ZERO_HASH,
    Hex32,
    HexAddress,
    HexSignature,
    Timestamp,
    new_hex32,
)
from trustrail.models.registry import AgentRecord, AgentRole, MerchantRecord
from trustrail.models.review import ReviewHold, ReviewOutcome
from trustrail.models.verdict import (
    CheckKind,
    CheckResult,
    Decision,
    ReasonCode,
    Verdict,
)
from trustrail.models.verification import VerificationRequest

__all__ = [
    "CURRENCY_DECIMALS",
    "ZERO_ADDRESS",
    "ZERO_HASH",
    "AgentRecord",
    "AgentRole",
    "AuditEntry",
    "AuditEventType",
    "Charge",
    "CheckKind",
    "CheckResult",
    "Currency",
    "Decision",
    "EvaluationSubject",
    "EvaluatorFlags",
    "EvaluatorOutput",
    "Hex32",
    "HexAddress",
    "HexSignature",
    "Mandate",
    "MandateBinding",
    "MandateRecord",
    "MandateState",
    "MandateStatus",
    "MerchantRecord",
    "Money",
    "ReasonCode",
    "ReviewHold",
    "ReviewOutcome",
    "SignedEvaluatorOutput",
    "SignedMandate",
    "Timestamp",
    "Verdict",
    "VerificationRequest",
    "new_hex32",
]
