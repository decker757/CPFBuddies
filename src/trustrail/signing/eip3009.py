"""EIP-3009 `TransferWithAuthorization`: paying a merchant that speaks x402.

This is how the StraitsX card rail moves XSGD. It is a different mechanism from
`MandateRegistry.spend`, and the difference is the single most important thing
to understand about that rail:

- **MandateRegistry** holds an allowance. Every spend calls `spend()`, which
  re-checks the cap, the merchant, the expiry and one-time consumption *in
  Solidity* before it moves anything. A compromised settlement worker gets
  nowhere.
- **EIP-3009** is a signed authorisation to move `value` tokens from `from` to
  `to`. There is no contract in the middle and nothing on chain that knows what
  a mandate is. Whoever holds the signing key can authorise any transfer up to
  the wallet's balance.

So on this rail the mandate is enforced by our Verifier and by nothing else,
which is exactly the degraded enforcement CLAUDE.md calls a coverage gradient.
The mitigation is operational rather than cryptographic: the key that signs
these authorisations belongs to a wallet funded to one mandate's cap, so the
blast radius of a compromise is whatever is in that wallet rather than the
buyer's whole balance. `nonce` is random per authorisation, and the token
contract rejects a replayed one.

Never point this at a wallet holding more than you are willing to lose to a
compromised worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trustrail.models.primitives import Hex32, HexAddress, new_hex32, to_bytes
from trustrail.ports import Signer
from trustrail.signing.crypto import hash_bytes
from trustrail.signing.eip712 import (
    EIP712_PREFIX,
    Eip712Domain,
    encode_address,
    encode_bytes32,
    encode_uint,
    keccak_words,
    type_hash,
)

#: The struct the token contract recomputes. Field order and types are fixed by
#: EIP-3009; `from` is a Python keyword, which is why the dataclass spells it
#: `from_address` and the encoder puts it back in the right slot by position.
TRANSFER_WITH_AUTHORIZATION_TYPE = (
    "TransferWithAuthorization(address from,address to,uint256 value,"
    "uint256 validAfter,uint256 validBefore,bytes32 nonce)"
)

#: How long an authorisation stays valid by default. Short on purpose: it is a
#: bearer instrument until it expires, and the 402 challenge that prompted it
#: allows 300 seconds.
DEFAULT_VALIDITY = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class TransferAuthorization:
    """One signed permission to move tokens, valid for a window."""

    from_address: HexAddress
    to: HexAddress
    value: int
    valid_after: int
    valid_before: int
    nonce: Hex32

    @classmethod
    def create(
        cls,
        *,
        from_address: str,
        to: str,
        value: int,
        now: datetime,
        validity: timedelta = DEFAULT_VALIDITY,
    ) -> TransferAuthorization:
        """A fresh authorisation with a random nonce and a bounded window.

        `valid_after` is 0 rather than `now`: a node whose clock runs a second
        behind ours would otherwise reject a perfectly good authorisation, and
        the useful bound is the one at the other end.
        """
        return cls(
            from_address=from_address.lower(),
            to=to.lower(),
            value=value,
            valid_after=0,
            valid_before=int((now + validity).timestamp()),
            nonce=new_hex32(),
        )

    def digest(self, domain: Eip712Domain) -> str:
        """The EIP-712 digest the token contract will recompute and verify.

        The domain is the *token's*, not ours — name and version come from the
        402 challenge and `verifying_contract` is the token address. Signing
        under our own domain would produce a signature the token rejects.
        """
        struct_hash = keccak_words(
            type_hash(TRANSFER_WITH_AUTHORIZATION_TYPE),
            encode_address(self.from_address),
            encode_address(self.to),
            encode_uint(self.value),
            encode_uint(self.valid_after),
            encode_uint(self.valid_before),
            encode_bytes32(self.nonce),
        )
        return hash_bytes(EIP712_PREFIX + domain.separator() + struct_hash)


def sign_authorization(
    authorization: TransferAuthorization, domain: Eip712Domain, signer: Signer
) -> str:
    """Sign an authorisation, returning `0x` + r || s || v.

    Refuses to sign for a wallet other than the signer's own. The token checks
    this too and would simply reject the signature, but failing here says why
    rather than surfacing an opaque rejection from a third party's API.
    """
    if authorization.from_address.lower() != signer.address.lower():
        raise ValueError(
            f"authorisation moves funds from {authorization.from_address} but the "
            f"signer is {signer.address}; a wallet can only authorise its own tokens"
        )
    return signer.sign(to_bytes(authorization.digest(domain)))
