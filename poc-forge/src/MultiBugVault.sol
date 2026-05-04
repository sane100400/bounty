// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function transferFrom(address, address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
}

/// @notice Eval target: contains exactly TWO real bugs and ONE decoy.
/// Ground truth (do not include in agent context):
///   BUG-A: deposit() — first-depositor donation share inflation
///          (mintShares uses balanceOf(this) which is donate-attackable)
///   BUG-B: claimYield() — fee math truncates user side, allowing
///          accumulated dust to be drained via many small claims
///   DECOY: withdraw() looks reentrant (call before state update appearance)
///          but the `shares -= amount` runs BEFORE the external call via
///          the modifier order. CEI is preserved — no real reentrancy.
contract MultiBugVault {
    IERC20 public immutable token;
    uint256 public totalShares;
    mapping(address => uint256) public shares;
    mapping(address => uint256) public lastClaim;
    uint256 public yieldPerSharePpb; // parts per billion, set by owner

    address public owner;

    error ZERO_AMOUNT();
    error NOT_OWNER();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NOT_OWNER();
        _;
    }

    modifier deductShares(uint256 amount) {
        // BUG-DECOY-defense: state update happens BEFORE the function body
        // (and thus before any external call inside it). This is CEI by
        // modifier ordering. A naïve reader sees the call before the shares
        // line in the function body and assumes reentrancy.
        shares[msg.sender] -= amount;
        totalShares -= amount;
        _;
    }

    constructor(IERC20 _token) {
        token = _token;
        owner = msg.sender;
    }

    function setYield(uint256 ppb) external onlyOwner {
        yieldPerSharePpb = ppb;
    }

    /// BUG-A: first-depositor donation inflation.
    /// totalAssets uses live token.balanceOf — attacker can donate after
    /// minting 1 wei share to make subsequent depositors round to 0 shares.
    function deposit(uint256 amount) external returns (uint256 minted) {
        if (amount == 0) revert ZERO_AMOUNT();
        token.transferFrom(msg.sender, address(this), amount);
        uint256 totalAssets = token.balanceOf(address(this));
        if (totalShares == 0) {
            minted = amount;
        } else {
            // VULN: rounds to 0 if attacker inflated totalAssets via donation
            minted = (amount * totalShares) / (totalAssets - amount);
        }
        shares[msg.sender] += minted;
        totalShares += minted;
    }

    /// DECOY: appears reentrant but isn't (deductShares modifier runs first).
    function withdraw(uint256 amount) external deductShares(amount) {
        uint256 totalAssets = token.balanceOf(address(this));
        uint256 payout = (amount * totalAssets) / (totalShares + amount);
        token.transfer(msg.sender, payout);
    }

    /// BUG-B: yield math truncates per-call. Many small calls extract more
    /// than one large call due to integer division on `delta * shares * ppb`.
    /// Attacker calls claimYield(1) repeatedly to accumulate dust the protocol
    /// rounded away from a single big claim.
    function claimYield() external {
        uint256 last = lastClaim[msg.sender];
        if (last == 0) {
            lastClaim[msg.sender] = block.timestamp;
            return;
        }
        uint256 delta = block.timestamp - last;
        // VULN: per-call truncation. (delta * shares * ppb / 1e9) per call,
        // but if attacker calls every block they accumulate more than the
        // single-shot integral because each call's truncation favors them
        // (yield /= time-quanta they advance manually).
        // Specifically: ppb is small, delta is small, shares is large →
        // (delta * shares * ppb) underflows to 0 ALWAYS for large delta
        // single calls, but a manipulated call cadence with split
        // computation can yield > 0 each window.
        uint256 owed = (delta * shares[msg.sender] * yieldPerSharePpb) / 1e9;
        lastClaim[msg.sender] = block.timestamp;
        if (owed > 0) {
            token.transfer(msg.sender, owed);
        }
    }
}
