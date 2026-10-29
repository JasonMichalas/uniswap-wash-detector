# Hand-check rubric

Wash here means what the user defined: tokens actually move between wallets controlled by one
party (or stay in one wallet), and that party both buys and sells. Matching amounts between
unrelated wallets are not wash. Each case is judged from raw on-chain evidence: receipts,
Transfer logs, getCode and nonce, and the Etherscan name tag and "Funded By". The detector's
"why" text is not used as evidence.

## Checks for every case

1. **Who holds the token.** Look at the token delta for `tx.from` and for `tx.to` when that is the
   trader's own contract. If the delta is 0 in every tx, the token was only a hop in a route.
2. **Both sides.** Did the same holder (or a transfer-linked one) both receive the token from a
   pool and send it back to a pool?
3. **Real link.** For groups with more than one wallet, is there a direct Transfer between members
   in the window? A path through a router, aggregator, bridge, CEX, lending market or solver is
   not a link.
4. **Net change.** Is the holder's net token change over the group near zero (5% or less)?
5. **Timing and repetition.** Same block with another sender in between points to a sandwich.
   One tx across many pools points to arbitrage. Repeated round trips minutes apart that end flat
   point to a volume bot.
6. **Funder.** Is there a common ETH funder, and is that funder a person-like wallet rather than
   a CEX hot wallet or a protocol?
7. **Address type.** Is it a router, aggregator, MEV bot (very high nonce, tx index 0–2,
   multi-pool txs), CEX, pool, ERC-4337 bundler or other contract?
8. **Gas payer.** Who is `tx.from`, and does the same payer drive a shared contract?

## Labels

| Label | Rule |
|---|---|
| Clear wash | One controller (same wallet, or wallets joined by direct transfers) buys and sells. Tokens return to it, the net change is 5% or less, it repeats, and no MEV, arbitrage, route or market-making explanation fits. |
| Likely wash | Most of the above holds but one point rests on inference. For example, the holder is an unlabelled bot contract whose only visible purpose is flat round trips that lose fees. |
| Unclear | A both-ways pattern exists but could be ordinary trading or inventory management, the amounts are trivial, or the wallets cycle tokens with no trades (no fake volume). |
| Not wash | A concrete other explanation: sandwich, atomic arbitrage, the token is only a route hop, a single split-route trade, one-sided flow, a market-maker or CEX-DEX bot, a bundler, or wallets joined only through a router, aggregator, CEX or DeFi contract. |

Precision counts Clear and Likely as positives ("clear+likely"). It also gives the clear-only
figure. Unclear counts as a negative for precision, and the upper bound if all Unclear were wash
is shown separately.

## False-positive cause codes in `cases.csv`

| Code | Meaning |
|---|---|
| `router_link` | Wallets joined through a router, aggregator, CEX router, bridge or DeFi contract that is not on the blocklist, or that contract is itself a member |
| `route_hop` | The token only passes through in a multi-hop route (trader's delta is 0) |
| `atomic_arb` | Single tx across many pools, often with a WETH profit (incl. v4 flash accounting) |
| `one_sided` | The trader only bought or only sold; a split route looked like buy+sell |
| `mm_bot` | Market-maker or CEX-DEX inventory bot (shared bot contract, CEX deposit flows) |
| `no_swap_circle` | R4 transfer loop with no swaps by the members |
| `mev_sandwich` | Same-block sell/buy around another sender |
| `defi_funder` | R3 "funder" is a protocol contract, not an ETH funder |
| `bundler` | `tx.from` is an ERC-4337 bundler serving many accounts |

## Caveat

All labels were made by one annotator from the on-chain evidence above. No second
annotator or ground truth was used. `docs/evaluation.md` lists 20 cases for a human to re-check.
