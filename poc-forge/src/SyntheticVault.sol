// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Synthetic vault with the classic ERC4626 first-depositor donation
///         vulnerability — used as a positive control for the harness pipeline.
/// @dev Real bug pattern: deposit() computes shares as
///         shares = amount * totalShares / totalAssets
///      Attacker mints 1 wei of shares as first depositor, then donates
///      tokens directly to the vault → totalAssets balloons, but
///      totalShares stays at 1 → next victim deposit rounds to 0 shares
///      → victim's tokens absorbed by attacker's single share.
contract SyntheticVault {
    address public immutable token;
    uint256 public totalShares;
    mapping(address => uint256) public sharesOf;

    constructor(address _token) {
        token = _token;
    }

    function totalAssets() public view returns (uint256 bal) {
        (bool ok, bytes memory data) = token.staticcall(
            abi.encodeWithSignature("balanceOf(address)", address(this))
        );
        require(ok, "balanceOf failed");
        bal = abi.decode(data, (uint256));
    }

    function deposit(uint256 amount) external returns (uint256 shares) {
        // VULNERABLE: rounds shares against attacker-controlled totalAssets
        if (totalShares == 0) {
            shares = amount;
        } else {
            shares = (amount * totalShares) / totalAssets();
        }
        require(shares > 0, "ZERO_SHARES");
        // pull tokens
        (bool ok,) = token.call(
            abi.encodeWithSignature("transferFrom(address,address,uint256)",
                msg.sender, address(this), amount)
        );
        require(ok, "transferFrom failed");
        sharesOf[msg.sender] += shares;
        totalShares += shares;
    }

    function redeem(uint256 shares) external returns (uint256 amount) {
        require(sharesOf[msg.sender] >= shares, "INSUFFICIENT_SHARES");
        amount = (shares * totalAssets()) / totalShares;
        sharesOf[msg.sender] -= shares;
        totalShares -= shares;
        (bool ok,) = token.call(
            abi.encodeWithSignature("transfer(address,uint256)", msg.sender, amount)
        );
        require(ok, "transfer failed");
    }
}

contract MockToken {
    string public name = "MockToken";
    string public symbol = "MTK";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        totalSupply += amount;
        balanceOf[to] += amount;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        if (allowance[from][msg.sender] != type(uint256).max) {
            allowance[from][msg.sender] -= amount;
        }
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}
