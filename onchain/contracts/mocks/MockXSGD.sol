// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * @title MockXSGD
 * @notice Stand-in for XSGD on networks where the real token is not deployed.
 *
 * @dev XSGD is live on Avalanche C-Chain mainnet but has no Fuji deployment, so testnet runs
 *      need a local ERC-20. Decimals are a constructor argument rather than a constant: the
 *      mock must mirror whatever the real token reports, and hardcoding the wrong value is a
 *      silent 100x error. Read `decimals()` off mainnet XSGD and pass it here.
 *
 *      Test and testnet only. Never deployed to mainnet.
 */
contract MockXSGD is ERC20 {
    uint8 private immutable _decimals;

    constructor(uint8 decimals_) ERC20("Mock XSGD", "XSGD") {
        _decimals = decimals_;
    }

    function decimals() public view override returns (uint8) {
        return _decimals;
    }

    /// @notice Unrestricted mint. Acceptable because this contract never reaches mainnet.
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
