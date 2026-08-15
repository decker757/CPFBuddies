// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title MandateRegistry
 * @notice Onchain enforcement for agent spending mandates.
 *
 * @dev This contract is the authority, not a mirror of offchain state. Every constraint the
 *      offchain Verifier checks is checked again here, independently, so that a compromised
 *      backend cannot move money outside a mandate. That is the whole claim: you do not have to
 *      trust our services, only this contract.
 *
 *      Funds are never custodied here. The principal grants this contract an ERC-20 allowance
 *      and `spend` pulls directly from the principal to the merchant.
 *
 *      Two roles, deliberately separated:
 *        - REGISTRAR_ROLE  registers and revokes mandates (the Mandate Service, issuer key)
 *        - SETTLER_ROLE    executes spends            (the Settlement Worker, hot wallet)
 *      A compromised settler still cannot exceed the cap, spend past expiry, spend a revoked
 *      mandate, or spend twice.
 *
 *      On `basketHash`: the mandate is minted before a product is chosen, so the basket is not
 *      known at registration and cannot be enforced here. It is recorded in the `Spent` event
 *      for the audit trail only. Do not claim basket-level binding at approval time.
 */
contract MandateRegistry is AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");
    bytes32 public constant SETTLER_ROLE = keccak256("SETTLER_ROLE");

    /// @notice Settlement token. Immutable so a deployment can never silently change currency.
    IERC20 public immutable token;

    struct Mandate {
        address principal; // whose funds move; must have approved this contract
        address agent; // recorded for the audit trail
        address merchant; // address(0) means unbound; bound on first spend
        uint256 cap; // maximum spendable, in token base units
        uint64 expiresAt; // unix seconds, exclusive
        bytes32 mandateHash; // hash of the signed offchain credential
        bool revoked;
        bool consumed;
        bool exists;
    }

    mapping(bytes32 mandateId => Mandate) private _mandates;

    event MandateRegistered(
        bytes32 indexed mandateId,
        address indexed principal,
        address indexed agent,
        address merchant,
        uint256 cap,
        uint64 expiresAt,
        bytes32 mandateHash
    );

    event MandateRevoked(bytes32 indexed mandateId);

    event Spent(
        bytes32 indexed mandateId,
        address indexed merchant,
        uint256 amount,
        bytes32 basketHash
    );

    // Errors mirror the offchain reason codes one-for-one so a revert on Snowtrace reads the
    // same as a rejection in the decision dashboard.
    error MandateAlreadyExists(bytes32 mandateId);
    error MandateNotFound(bytes32 mandateId);
    error MandateIsRevoked(bytes32 mandateId);
    error MandateExpired(bytes32 mandateId, uint64 expiresAt, uint64 nowTs);
    error MandateAlreadyConsumed(bytes32 mandateId);
    error MerchantMismatch(bytes32 mandateId, address bound, address supplied);
    error AmountExceedsCap(bytes32 mandateId, uint256 cap, uint256 requested);
    error InvalidAmount();
    error InvalidAddress();
    error InvalidExpiry(uint64 expiresAt, uint64 nowTs);

    /**
     * @param settlementToken ERC-20 used for settlement (XSGD on mainnet, MockXSGD on testnet).
     * @param admin Address granted DEFAULT_ADMIN_ROLE, which manages the other two roles.
     */
    constructor(IERC20 settlementToken, address admin) {
        if (address(settlementToken) == address(0) || admin == address(0)) {
            revert InvalidAddress();
        }
        token = settlementToken;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    /**
     * @notice Record a mandate onchain at mint time.
     * @dev `merchant` may be address(0) when the product has not been chosen yet; the first
     *      `spend` then binds it permanently. Passing a concrete merchant here is stricter and
     *      preferred whenever it is already known.
     */
    function registerMandate(
        bytes32 mandateId,
        address principal,
        address agent,
        address merchant,
        uint256 cap,
        uint64 expiresAt,
        bytes32 mandateHash
    ) external onlyRole(REGISTRAR_ROLE) {
        if (_mandates[mandateId].exists) revert MandateAlreadyExists(mandateId);
        if (principal == address(0) || agent == address(0)) revert InvalidAddress();
        if (cap == 0) revert InvalidAmount();
        if (expiresAt <= block.timestamp) {
            revert InvalidExpiry(expiresAt, uint64(block.timestamp));
        }

        _mandates[mandateId] = Mandate({
            principal: principal,
            agent: agent,
            merchant: merchant,
            cap: cap,
            expiresAt: expiresAt,
            mandateHash: mandateHash,
            revoked: false,
            consumed: false,
            exists: true
        });

        emit MandateRegistered(
            mandateId, principal, agent, merchant, cap, expiresAt, mandateHash
        );
    }

    /**
     * @notice Kill switch. Irreversible by design: re-authorising is a new mandate, not an undo.
     */
    function revoke(bytes32 mandateId) external onlyRole(REGISTRAR_ROLE) {
        Mandate storage mandate = _mandates[mandateId];
        if (!mandate.exists) revert MandateNotFound(mandateId);
        if (mandate.revoked) revert MandateIsRevoked(mandateId);

        mandate.revoked = true;
        emit MandateRevoked(mandateId);
    }

    /**
     * @notice Execute a spend against a mandate. Reverts unless every constraint holds.
     * @dev Checks run in the same order as the offchain Verifier so the two agree on which
     *      reason fires first. State is written before the transfer
     *      (checks-effects-interactions).
     */
    function spend(
        bytes32 mandateId,
        address merchant,
        uint256 amount,
        bytes32 basketHash
    ) external onlyRole(SETTLER_ROLE) {
        Mandate storage mandate = _mandates[mandateId];

        if (!mandate.exists) revert MandateNotFound(mandateId);
        if (mandate.revoked) revert MandateIsRevoked(mandateId);
        if (block.timestamp >= mandate.expiresAt) {
            revert MandateExpired(mandateId, mandate.expiresAt, uint64(block.timestamp));
        }
        if (mandate.consumed) revert MandateAlreadyConsumed(mandateId);
        if (merchant == address(0)) revert InvalidAddress();
        if (mandate.merchant != address(0) && mandate.merchant != merchant) {
            revert MerchantMismatch(mandateId, mandate.merchant, merchant);
        }
        if (amount == 0) revert InvalidAmount();
        if (amount > mandate.cap) revert AmountExceedsCap(mandateId, mandate.cap, amount);

        mandate.consumed = true;
        if (mandate.merchant == address(0)) {
            mandate.merchant = merchant;
        }

        emit Spent(mandateId, merchant, amount, basketHash);

        token.safeTransferFrom(mandate.principal, merchant, amount);
    }

    /// @notice Full mandate record. Reverts if the mandate was never registered.
    function getMandate(bytes32 mandateId) external view returns (Mandate memory) {
        Mandate memory mandate = _mandates[mandateId];
        if (!mandate.exists) revert MandateNotFound(mandateId);
        return mandate;
    }

    /// @notice True when a spend would be permitted on amount grounds right now.
    function isSpendable(bytes32 mandateId) external view returns (bool) {
        Mandate memory mandate = _mandates[mandateId];
        return mandate.exists && !mandate.revoked && !mandate.consumed
            && block.timestamp < mandate.expiresAt;
    }
}
