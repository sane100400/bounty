// SPDX-License-Identifier: MIT
pragma solidity ^0.8.13;

import {Test} from "forge-std/Test.sol";

/// @notice ReX-style class-level safety invariants used as universal oracles.
/// @dev Each function here corresponds to one entry in hypothesis.post_vuln_state.class_invariant.
///      The harness Verifier picks one and asserts it after the attack flow runs.
///      Keeping these generic means most PoCs need ZERO custom oracle logic.
abstract contract ClassInvariants is Test {
    function assertAttackerEthIncreased(address attacker, uint256 before_, uint256 minDelta) internal {
        uint256 after_ = attacker.balance;
        require(after_ > before_, "InvariantOK: attacker ETH did NOT increase");
        assertGt(after_ - before_, minDelta, "InvariantOK: ETH delta below profit threshold");
    }

    function assertAttackerTokenIncreased(address attacker, address token, uint256 before_, uint256 minDelta) internal {
        (bool ok, bytes memory data) = token.staticcall(abi.encodeWithSignature("balanceOf(address)", attacker));
        require(ok, "balanceOf call reverted");
        uint256 after_ = abi.decode(data, (uint256));
        require(after_ > before_, "InvariantOK: attacker token did NOT increase");
        assertGt(after_ - before_, minDelta, "InvariantOK: token delta below threshold");
    }

    function assertVictimDrained(address victim, address token, uint256 before_, uint256 maxRemainingBps) internal {
        (bool ok, bytes memory data) = token.staticcall(abi.encodeWithSignature("balanceOf(address)", victim));
        require(ok, "balanceOf call reverted");
        uint256 after_ = abi.decode(data, (uint256));
        require(after_ < before_, "InvariantOK: victim balance did NOT decrease");
        // drained = remaining is below maxRemainingBps of original (e.g. 1000 = 10%)
        assertLt(after_ * 10000, before_ * maxRemainingBps, "InvariantOK: victim not sufficiently drained");
    }

    function assertSharePriceCollapsed(uint256 ppsBefore, uint256 ppsAfter, uint256 maxRetainedBps) internal {
        require(ppsAfter < ppsBefore, "InvariantOK: pps did NOT decrease");
        assertLt(ppsAfter * 10000, ppsBefore * maxRetainedBps, "InvariantOK: pps drop insufficient");
    }

    function assertSharePriceInflated(uint256 ppsBefore, uint256 ppsAfter, uint256 minFactor) internal {
        require(ppsAfter > ppsBefore, "InvariantOK: pps did NOT increase");
        assertGt(ppsAfter, ppsBefore * minFactor, "InvariantOK: pps inflation below threshold");
    }

    function assertSupplyInflated(address token, uint256 before_, uint256 minFactor) internal {
        (bool ok, bytes memory data) = token.staticcall(abi.encodeWithSignature("totalSupply()"));
        require(ok, "totalSupply call reverted");
        uint256 after_ = abi.decode(data, (uint256));
        assertGt(after_, before_ * minFactor, "InvariantOK: supply inflation below threshold");
    }

    function assertPermissionAcquired(address target, bytes32 role, address attacker) internal {
        (bool ok, bytes memory data) = target.staticcall(abi.encodeWithSignature("hasRole(bytes32,address)", role, attacker));
        require(ok, "hasRole call reverted");
        bool has = abi.decode(data, (bool));
        assertTrue(has, "InvariantOK: attacker did NOT acquire role");
    }

    function assertFunctionCallable(address target, bytes calldata payload) internal {
        (bool ok,) = target.call(payload);
        assertTrue(ok, "InvariantOK: function was correctly NOT callable");
    }
}
