// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";

/// Smoke test for the halmos verify gate. Property is trivially true,
/// proves halmos can run end-to-end inside our verify.py pipeline.
contract HalmosProperty_smoke_001 is Test {
    function check_addition_commutes(uint128 a, uint128 b) external pure {
        assert(uint256(a) + uint256(b) == uint256(b) + uint256(a));
    }
}
