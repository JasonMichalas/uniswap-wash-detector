# Wash-trading rules

The detector flags a swap only when a named rule can follow the tokens. Each flagged swap stores the rule ids, a plain-English reason, and a path (wallets, transaction hashes, and block times). Wash volume counts only the token amount the rule followed. A buy of 10 that sends 1 onward, which is then sold, contributes the dollar value of 1 token, not of 10.

Liquidity adds and removes are not swaps. They land in `liquidity_events` and never become a wash leg. A pool address passed in as excluded cannot be a hop, so a transfer into or out of the pool is not a link between two traders.

Routers, aggregators (1inch v5, 1inch v6, 0x Exchange Proxy), public MEV searchers (`jaredfromsubway.eth`), and the published Binance, Coinbase, and Kraken hot wallets are hops only. A transfer through one of them does not join the two sides into a wash group.

## What the older detector did

`WashDetector` (Gan et al. 2024 settings: 200 blocks, net within 5%, groups of at most 16 wallets) did four things:

- A same-wallet round trip, using the smallest window whose buys and sells net.
- Linked pairs, triangles, and transfer cycles between wallets that traded.
- A flow group whose *whole* buy and *whole* sell net within 5%. A hub with degree above 25 was skipped so it could not glue strangers together.
- A flag of `(confidence, cycle id)` on the swap. There was no rule id, no sentence, and no path.

That matcher misses a partial lot. Buy 10, send 1, sell 1 does not net, so it was not wash. It also had no common-funder rule, no same-transaction rule separate from the round trip, and no way to ask for a day or a week between the buy and the sell without raising the one global window used by every test.

The 200-block default is unchanged, so those older tests still describe the same groups. A new Find wash groups box starts at 1 day and 24 wallets, and the panel can move that window.

## R1 — Same wallet buys and sells

One wallet buys and later sells about the same amount. The delay can be zero: the same block, or the same transaction. The two trades must fall inside the configured longest gap. A buy of 1000 and a sell of 500 do not match. That partial case is R2 or R6, not R1.

## R2 — Tokens passed along, then sold

Buys create lots. Transfers move those lots first-in, first-out, skipping any hop on the blocklist. A sell matches when it is within the tolerance of the lots that actually arrived, not of the original buy.

- Buy 10, send 1 to B, B sells 1. Washed amount is 1.
- A chain of 20 hops is a match when the wallet cap allows it (the cap is “Most wallets in a chain”).
- Fan-in: several buyers send to one wallet that sells their sum, including hours or a day later when the gap allows it.
- Fan-out: one buy split across several sellers.
- Many buyers to many sellers.
- A sell of 10 against an inflow of 4 does not match. The amounts are not the same lot.

The longest gap is the box setting “Longest time between buy and sell”: about 1 hour (300 blocks), 6 hours (1,800), 1 day (7,200, the new-box default), 3 days (21,600), 7 days (50,400), or the whole loaded window (100,000 blocks). Ethereum blocks are about 12 seconds. The detector default used by the older tests stays 200 blocks.

## R3 — Wallets funded or paid together

Only wallets that traded this token are clustered. The link is a token transfer already in the loaded window, which is cached and needs no API key:

- The same external wallet sent this token to two or more traders (a shared funder).
- Two or more traders sent this token to the same external wallet (shared proceeds).

Funding transfers are clustered when they fall inside the same gap, which is the “same funding block” case when they share a block. The traders’ own buys and sells must still cancel, and the cluster cannot be larger than the wallet cap. A hub that only funds buyers, with no matching sells, is not a group.

Native ETH traces are not in the log cache. Two wallets funded only with ETH, with no token transfer from that funder and no shared proceeds in this token, are not linked. Calling a trace API inside `detect()` would break the keyless cache.

## R4 — A circle of wallets

The lot returns to a wallet already on its path, and a later sell matches that lot. The swap is labelled R4. A plain forward chain stays R2.

## R5 — Many trades, nothing left

One wallet has at least two buys and two sells, the amounts cancel, and the trades sit inside the gap. A single buy and a single sell is R1, not churn.

## R6 — Buy and sell in one transaction

The same `tx.from` buys and sells this token in one transaction hash. The washed amount is the overlap. A buy of 8 and a sell of 8 in that transaction is fully washed. A buy of 10 and a sell of 1 in that transaction washes 1. The same wallet in two different transactions is not R6.

## What is not wash

Two unrelated wallets, one buying 2 and one selling 2 within about 10 minutes, with no transfer, no shared funder, and no shared proceeds, are not a group.

## Where a result shows the reason

The flagged-swap table and the group table have `rules`, `why`, and `path`. The results panel titles those columns Rules, Why, and Path. Find wash groups has a “Which kinds of wash trading” checklist using the plain names above. Clearing a kind drops that rule for that box.
