# 1. Title

`tx.origin` misuse in `StreamingNFT._claimVestedRewards()` permanently freezes unclaimed yield for smart contract wallet holders

---

# 2. Description

## Brief / Intro

`StreamingNFT._claimVestedRewards()` authorizes reward claims by checking `tx.origin == beneficiary`. Unlike `createStream()`, which has a paymaster bypass for this check, the claim path has no such exception. If a credential NFT is held by a smart contract (multisig, staking vault, fractionalization contract), `tx.origin` can never equal the contract address, so every call to `claimRewards()` reverts with `InvalidOrigin`. The cliff unlock and all linearly vested rewards for those token IDs become permanently unreachable.

---

## Vulnerability Details

`createStream()` has a paymaster bypass:

```solidity
bool isPayMaster_ = isPayMaster[tx.origin];
if (!isPayMaster_ && onbehalfOf != tx.origin) {  // paymaster can bypass
    revert InvalidOrigin(tx.origin);
}
```

`_claimVestedRewards()` does not:

```solidity
address beneficiary = credentialNFT.ownerOf(streamId);
if (tx.origin != beneficiary) {   // no paymaster exception
    revert InvalidOrigin(tx.origin);
}
```

Since `tx.origin` is always an EOA, this check unconditionally reverts for any NFT held by a contract. The asymmetry between the two functions suggests the paymaster exception was unintentionally omitted from the claim path.

The NFT Vault (`0x05113720A7AbC229124b8682DFDB2521E49608C4`) holds BandBear #966, which has an existing stream created via paymaster. Calling `claimRewards(966)` against the live contract reverts with `InvalidOrigin`. There is no admin function to redirect stream proceeds or recover funds to individual beneficiaries.

---

## Impact Details

The Immunefi impact table lists **"Permanent freezing of unclaimed yield"** as High severity. This report maps to that impact for the following reasons:

- The cliff end timestamp (`2026-02-06`) has passed. Affected holders cannot claim their cliff unlock or any vested rewards, now or in the future.
- The contract has no recovery mechanism: `withdraw()` transfers only to the owner, not to individual stream beneficiaries. No function exists to reassign or rescue a stream.
- The following contract holders are confirmed on-chain (Berachain mainnet, 2026-03-18):

| Holder | Type | NFTs affected | Est. locked (cliff+vested) |
|--------|------|---------------|----------------------------|
| `0x05113720A7AbC229124b8682DFDB2521E49608C4` | NFT Staking Vault | 8 BandBears + 15 BitBears | ~17,955 BERA |
| `0xcE71670294a9101406941F2cC943e6B042573736` | Gnosis Safe 4-of-7 | 3 BitBears | ~1,743 BERA |
| `0x97eda957ef9180afa10b233c0a2118727366dde6` | Fungify fBANDBEARS | 2 BandBears | ~2,310 BERA |
| `0xa60c62b55cf2094ff014978553be9535e936fd4a` | Fungify fBITBEARS | 2 BitBears | ~1,162 BERA |
| **Total (2 of 6 collections)** | | **30 NFTs** | **~23,170 BERA** |

The remaining four bear collections (Bong Bears, Bond Bears, Boo Bears, Baby Bears) have not been fully enumerated but contain the same vulnerable code.

---

# 3. Proof of Concept

Foundry mainnet fork test. The vulnerable path uses no `vm.prank` — `tx.origin` is the Forge default EOA, which can never equal `NFT_VAULT` (a contract address). Control test uses `vm.prank(eoaOwner, eoaOwner)` to confirm EOAs are unaffected.

```bash
forge test --match-contract StreamingNFT_PoC \
  --fork-url https://berachain-rpc.publicnode.com -vv
```

```
[PASS] test_ContractWalletCannotClaim()
  NFT_VAULT BandBear tokenId: 966
  claimable (wei): 244225222020523619557
  [PASS] claimRewards reverted: InvalidOrigin(tx.origin)

[PASS] test_EOAHolderCanClaim()
  [PASS] EOA claimed successfully

[PASS] test_FinancialImpact()
  Total locked (30 NFTs, 2 of 6 collections): 23174 BERA
```

https://gist.github.com/sane100400/907a4c634c1bd0f9d3002fc7f35b6159 (`poc.t.sol`, `poc.py`)

---

## References

- Vulnerable file: https://github.com/berachain/airdrop-contracts/blob/main/src/StreamingNFT.sol
- SWC-115: https://swcregistry.io/docs/SWC-115
