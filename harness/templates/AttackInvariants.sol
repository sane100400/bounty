// SPDX-License-Identifier: MIT
pragma solidity ^0.8.13;

import {Test} from "forge-std/Test.sol";

/// @notice Trace2Inv-derived attack-side invariants.
/// @dev Trace2Inv (arXiv 2404.14580) gives 23 DEFENSIVE invariant templates
///      that protocols insert to PREVENT exploits. We invert them: an attack
///      PoC succeeds iff the corresponding defensive invariant would have been
///      VIOLATED. This library provides assertions that fire when a violation
///      is observed, complementing the ClassInvariants outcome oracles.
///
///      Categories follow the Trace2Inv taxonomy:
///        AC = Access Control (EOA, SO, SM, OO, OM)
///        TL = Time Lock (SB, OB, LU)
///        GC = Gas Control (GS, GC) — usually omitted in PoCs
///        RE = Re-entrancy (RE)
///        OS = Oracle Slippage (OR, OD)
///        SS = Special Storage (TSU, TBU)
///        MF = Money Flow (TIU, TIRU, TOU, TORU)
///        DF = Data Flow (MU, CVU, DFU, DFL)
///
///      Use these alongside the outcome-side oracles in ClassInvariants.sol
///      when the hypothesis is framed as "this defensive check is missing or weak".
abstract contract AttackInvariants is Test {
    // ------------------------------------------------------------------
    // Oracle Slippage (OR / OD inverted)
    // ------------------------------------------------------------------
    function assertOracleRangeViolated(uint256 newPrice, uint256 lowerBound, uint256 upperBound) internal {
        bool inRange = newPrice >= lowerBound && newPrice <= upperBound;
        assertFalse(inRange, "InvariantOK: oracle stayed in [LB,UB] — manipulation did not succeed");
    }

    function assertOracleDeviationExceeded(uint256 oldPrice, uint256 newPrice, uint256 maxDevBps) internal {
        require(oldPrice > 0, "oldPrice must be non-zero");
        uint256 absDelta = newPrice > oldPrice ? newPrice - oldPrice : oldPrice - newPrice;
        uint256 devBps = (absDelta * 10000) / oldPrice;
        assertGt(devBps, maxDevBps, "InvariantOK: deviation within tolerance — no manipulation observed");
    }

    // ------------------------------------------------------------------
    // Money Flow (TIU / TIRU / TOU / TORU inverted)
    // ------------------------------------------------------------------
    function assertTokenInExcessive(uint256 tokenIn, uint256 expectedMax) internal {
        assertGt(tokenIn, expectedMax, "InvariantOK: token-in stayed under TIU bound");
    }

    function assertTokenInRatioExcessive(uint256 tokenIn, uint256 vaultBalance, uint256 maxRatioBps) internal {
        require(vaultBalance > 0, "vaultBalance zero");
        uint256 ratioBps = (tokenIn * 10000) / vaultBalance;
        assertGt(ratioBps, maxRatioBps, "InvariantOK: token-in ratio under bound");
    }

    function assertTokenOutExcessive(uint256 tokenOut, uint256 expectedMax) internal {
        assertGt(tokenOut, expectedMax, "InvariantOK: token-out under bound");
    }

    function assertTokenOutRatioExcessive(uint256 tokenOut, uint256 vaultBalance, uint256 maxRatioBps) internal {
        require(vaultBalance > 0, "vaultBalance zero");
        uint256 ratioBps = (tokenOut * 10000) / vaultBalance;
        assertGt(ratioBps, maxRatioBps, "InvariantOK: token-out ratio under bound");
    }

    // ------------------------------------------------------------------
    // Special Storage (TSU / TBU inverted)
    // ------------------------------------------------------------------
    function assertSupplyExceededBound(address token, uint256 maxSupply) internal {
        (bool ok, bytes memory data) = token.staticcall(abi.encodeWithSignature("totalSupply()"));
        require(ok, "totalSupply call reverted");
        uint256 supply = abi.decode(data, (uint256));
        assertGt(supply, maxSupply, "InvariantOK: supply under TSU bound");
    }

    function assertBorrowExceededBound(address market, uint256 maxBorrow) internal {
        (bool ok, bytes memory data) = market.staticcall(abi.encodeWithSignature("totalBorrow()"));
        require(ok, "totalBorrow call reverted");
        uint256 totalBorrow = abi.decode(data, (uint256));
        assertGt(totalBorrow, maxBorrow, "InvariantOK: borrow under TBU bound");
    }

    // ------------------------------------------------------------------
    // Time Lock / Same Block (SB / OB inverted — flash loan signature)
    // ------------------------------------------------------------------
    function assertSameBlockEntry(uint256 entryBlock) internal view {
        require(block.number == entryBlock, "InvariantOK: exploit spans multiple blocks (no flash-loan signature)");
    }

    // ------------------------------------------------------------------
    // Re-entrancy (RE inverted)
    // ------------------------------------------------------------------
    function assertReentrancyOccurred(uint256 callDepthObserved) internal {
        assertGt(callDepthObserved, 1, "InvariantOK: no nested re-entry observed");
    }

    // ------------------------------------------------------------------
    // Access Control (EOA / SO / SM inverted — privileged action by non-priv)
    // ------------------------------------------------------------------
    function assertActionExecutedByNonOwner(address target, address actor, bytes32 ownerSlot) internal {
        bytes32 ownerRaw = vm.load(target, ownerSlot);
        address owner = address(uint160(uint256(ownerRaw)));
        require(actor != owner && actor != address(0), "actor==owner — no priv escalation observed");
        assertTrue(actor != owner, "InvariantOK: action was performed by owner (no escalation)");
    }

    // ------------------------------------------------------------------
    // Data Flow (MU / DFU / DFL inverted)
    // ------------------------------------------------------------------
    function assertMappingValueOutOfBound(uint256 actualValue, uint256 expectedUpperBound) internal {
        assertGt(actualValue, expectedUpperBound, "InvariantOK: mapping value under MU bound");
    }

    function assertDataFlowOutOfBound(uint256 actualValue, uint256 lower, uint256 upper) internal {
        bool inRange = actualValue >= lower && actualValue <= upper;
        assertFalse(inRange, "InvariantOK: data stayed in [DFL, DFU] bounds");
    }
}
