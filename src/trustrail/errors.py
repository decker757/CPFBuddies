"""Errors raised by the Mandate Service and its stores.

Note what is *not* here: there is no "verification failed" exception. A rejected
charge is a `Verdict` with a reason code, not an exception — the dashboard, the
audit log and the approval UI all need to render the rejection, and an exception
would throw that away.
"""

from __future__ import annotations


class TrustRailError(Exception):
    """Base class for everything this package raises deliberately."""


class MandateNotFound(TrustRailError):
    pass


class MandateAlreadyExists(TrustRailError):
    pass


class NonceAlreadyClaimed(TrustRailError):
    """A nonce was reused across two mandates. Breaks one-time consumption."""


class MandateStatusConflict(TrustRailError):
    """A conditional write lost a race, or the transition was illegal.

    This is what makes one-time consumption real: two settlement workers racing
    to consume the same mandate means exactly one wins and the other sees this.
    """


class IllegalBinding(TrustRailError):
    """A `bind` tried to do something other than fill in an empty field.

    Binding may only move merchant and basket from empty to set. It can never
    raise the cap or extend expiry — approving a REVIEW must not become a way to
    widen the mandate the human originally agreed to.
    """
