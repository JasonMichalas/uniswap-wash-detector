# Evaluation: wash models

Milestone 2 of the Uniswap Wash Detector (ECM3401, University of Exeter). There is no public ground truth, so the supervised numbers below are recall and precision on wash groups planted into real pool history, with train and test split by token.

## Dataset

Window: the cached scrape already in the database, blocks 26,264,761–26,279,564 (2026-10-24 20:00 through 2026-10-26 21:30 UTC), Ethereum mainnet, public RPC, no API key. That is the same 22 tokens and about 2 days as the previous run. `washdetect batch --top 40 --days 2` was still indexing Uniswap factory logs for its first new token when this model was fit, so those tokens are not in this table. Fetch s is the cold public-RPC scrape recorded beside the original database. A transfer through the Uniswap v4 PoolManager is not a hop between wallets.

| Symbol | Token | Real swaps | Real transfers | Fetch s | Split |
|---|---|---:|---:|---:|---|
| UNI | `0x1f9840a85d5af5bf1d1762f925bdaddc4201f984` | 5882 | 31457 | 299.0 | calib |
| 1INCH | `0x111111111117dc0aa78b770fa6a738034120c302` | 67 | 17199 | 609.8 | test |
| CRV | `0xd533a949740bb3306d119cc777fa900ba034cd52` | 788 | 25894 | 753.6 | test |
| GRT | `0xc944e90c64b2c07662a292be6244bdf05cda44a7` | 361 | 1461 | 71.2 | test |
| MKR | `0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2` | 157 | 678 | 57.1 | test |
| PEPE | `0x6982508145454ce325ddbe47a25d4ec3d2311933` | 2018 | 13565 | 151.2 | test |
| RPL | `0xd33526068d116ce69f19a9ee46f0bd304f21a51f` | 653 | 1686 | 154.1 | test |
| SHIB | `0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce` | 377 | 6432 | 82.7 | test |
| AAVE | `0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9` | 2657 | 11281 | 140.9 | train |
| APE | `0x4d224452801aced8b2f0aebe155379bb5d594381` | 116 | 1949 | 448.8 | train |
| BAL | `0xba100000625a3754423978a60c9317c58a424e3d` | 11 | 315 | 297.1 | train |
| BITCOIN | `0x72e4f9f808c49a2a61de9c5896298920dc4eeea9` | 486 | 924 | 105.9 | train |
| COMP | `0xc00e94cb662c3520282e6f5717214004a7f26888` | 1339 | 4695 | 118.4 | train |
| CULT | `0xf0f9d895aca5c8678f706fb8216fa22957685a13` | 18 | 56 | 222.4 | train |
| ENS | `0xc18360217d8f7ab5e7c516566761ea12ce7f9d72` | 131 | 855 | 112.0 | train |
| LADYS | `0x12970e6868f88f6557b76120662c1b3e50a646bf` | 210 | 415 | 29.5 | train |
| LDO | `0x5a98fcbea516cf06857215779fd812ca3bef1b32` | 1253 | 3498 | 112.8 | train |
| LINK | `0x514910771af9ca656af840dff83e8264ecf986ca` | 3173 | 15957 | 251.4 | train |
| Mog | `0xaaee1a9723aadb7afa2810263653a34ba2c21c7a` | 632 | 1132 | 80.6 | train |
| SNX | `0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f` | 122 | 810 | 89.8 | train |
| SUSHI | `0x6b3595068778dd592e39a122f4f5a5cf09c90fe2` | 181 | 1236 | 64.0 | train |
| TURBO | `0xa35923162c49cf95e6bf26623385eb431ad920d3` | 126 | 616 | 124.8 | train |

Train tokens: 14. Calibration tokens: 1. Test tokens: 7.

Planted groups (kept separate from the chain cache, addresses start with `0x57`) cover every rule: a same-wallet round trip (R1), a partial lot and fan-in / fan-out / many-to-many flows (R2), a 16-wallet chain and a 21-wallet chain, a fan-in whose sell is 3,000 blocks later, a shared funder (R3), rings (R4), a wallet that churns flat (R5), and a buy and a sell in one transaction (R6). A negative decoy, two strangers buying 2 and selling 2 about 40 blocks apart, is inserted and labelled not-wash. Group sizes run from 1 to 21 wallets.

## Features

XGBoost and Isolation Forest see behavioural columns only: swap counts, log amounts, net position over the whole window, inter-swap timing and burstiness, pool count, counterparty degree, shared-funder degree, and the fraction of bought tokens sent onward (or sold tokens that arrived by transfer). They do not see `round_trip_count`, `wash_volume_usd`, `heuristic_label`, `heuristic_confidence`, `pattern_type`, or the group's buyer and seller counts. Those are labels or rule outputs. Wallet columns: n_swaps, n_buys, n_sells, buy_fraction, log_buy_amount, log_sell_amount, log_volume_usd, net_position_change, trade_interval_cv, median_inter_swap_blocks, burst_fraction, size_cv, n_pools, counterparty_degree, funding_source_degree, n_in_counterparties, n_out_counterparties, in_transfer_ratio, out_transfer_ratio, median_blocks_buy_to_out, both_sides. Swap columns add the swap's own size and the same wallet context. A planted row's label is 1 even when the rule misses it; a real row's label is the rule's weak label.

## Headline results (test tokens, planted swaps)

Positive class: a swap we inserted. Threshold 0.5 on the calibrated probability. PR-AUC is average precision over that ranking.

| | Precision | Recall | F1 | PR-AUC | Support |
|---|---:|---:|---:|---:|---:|
| Swap model | 91.9% | 100.0% | 95.8% | 1.000 | 784 / 5219 |
| Wallet model | 93.7% | 100.0% | 96.7% | 1.000 | 714 / 2003 |

A real wash swap that the model scores above 0.5 is a false positive on this planted-only task. Agreement with the rule, on real (non-planted) test swaps, is the right place to read that:

Rule agreement on real test swaps: precision 95.7%, recall 29.2%, F1 44.7% (the rule's hard label is the reference).

![Precision-recall on planted test swaps](figures/pr_curve.png)

## Recall by pattern

Swap-level recall: fraction of planted swaps of that pattern with probability ≥ 0.5.

| pattern_type | n | recall |
| --- | ---: | ---: |
| many_to_many | 245 | 100.0% |
| many_to_one | 217 | 100.0% |
| one_to_many | 154 | 100.0% |
| ring | 112 | 100.0% |
| round_trip | 56 | 100.0% |

![Recall by pattern](figures/recall_by_pattern.png)

## Recall by rule

Model recall is the fraction of planted test swaps of that rule with probability ≥ 0.5. Detector recall is the fraction of planted groups, on every assembled token, whose swaps the rule labelled with that rule id. A 21-wallet chain and the 3,000-block fan-in are scored with a 8,000-block gap and a 32-wallet cap. The other plants use the 200-block, 16-wallet default.

### Model, planted test swaps

| rule | n | recall |
| --- | ---: | ---: |
| R1 | 14 | 100.0% |
| R2 | 658 | 100.0% |
| R3 | 14 | 100.0% |
| R4 | 56 | 100.0% |
| R5 | 28 | 100.0% |
| R6 | 14 | 100.0% |

### Detector, planted groups

| rule | n | recall |
| --- | ---: | ---: |
| R1 | 22 | 100.0% |
| R2 | 374 | 100.0% |
| R3 | 22 | 100.0% |
| R4 | 88 | 100.0% |
| R5 | 22 | 100.0% |
| R6 | 22 | 100.0% |

The previous published run, before these extra plants, was 22 tokens over 2 days: swap model precision 92.3%, recall 100%, F1 96.0%, PR-AUC 1.000 (644 / 5029); wallet model precision 94.1%, recall 100%, F1 96.9%, PR-AUC 1.000 (602 / 1872); rule agreement on real test swaps precision 92.6%, recall 27.6%, F1 42.6%.

## Recall by buyers × sellers

Same swap-level recall, split by the planted group's `n_buyers` and `n_sellers`.

| n_buyers | n_sellers | n | recall |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 168 | 100.0% |
| 1 | 2 | 21 | 100.0% |
| 1 | 3 | 28 | 100.0% |
| 1 | 5 | 42 | 100.0% |
| 1 | 8 | 63 | 100.0% |
| 2 | 1 | 21 | 100.0% |
| 2 | 2 | 28 | 100.0% |
| 2 | 3 | 35 | 100.0% |
| 3 | 1 | 28 | 100.0% |
| 3 | 4 | 49 | 100.0% |
| 4 | 4 | 56 | 100.0% |
| 5 | 1 | 42 | 100.0% |
| 5 | 6 | 77 | 100.0% |
| 6 | 1 | 49 | 100.0% |
| 10 | 1 | 77 | 100.0% |

Group-level recall: a planted group counts as caught when at least half of its swaps score ≥ 0.5.

| pattern_type | n_buyers | n_sellers | n_wallets | n_groups | recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| many_to_many | 2 | 2 | 6 | 7 | 100.0% |
| many_to_many | 2 | 3 | 4 | 7 | 100.0% |
| many_to_many | 3 | 4 | 9 | 7 | 100.0% |
| many_to_many | 4 | 4 | 10 | 7 | 100.0% |
| many_to_many | 5 | 6 | 13 | 7 | 100.0% |
| many_to_one | 2 | 1 | 3 | 7 | 100.0% |
| many_to_one | 3 | 1 | 4 | 7 | 100.0% |
| many_to_one | 5 | 1 | 6 | 7 | 100.0% |
| many_to_one | 6 | 1 | 7 | 7 | 100.0% |
| many_to_one | 10 | 1 | 11 | 7 | 100.0% |
| one_to_many | 1 | 2 | 3 | 7 | 100.0% |
| one_to_many | 1 | 3 | 4 | 7 | 100.0% |
| one_to_many | 1 | 5 | 6 | 7 | 100.0% |
| one_to_many | 1 | 8 | 9 | 7 | 100.0% |
| ring | 1 | 1 | 2 | 14 | 100.0% |
| ring | 1 | 1 | 3 | 7 | 100.0% |
| ring | 1 | 1 | 4 | 7 | 100.0% |
| ring | 1 | 1 | 5 | 7 | 100.0% |
| ring | 1 | 1 | 8 | 7 | 100.0% |
| ring | 1 | 1 | 16 | 7 | 100.0% |
| ring | 1 | 1 | 21 | 7 | 100.0% |
| round_trip | 1 | 1 | 1 | 21 | 100.0% |

## Wash percent: rule vs model

Rule % is the Gan-style hard label on the real swaps only (plants removed). ML % is `100 * sum(p_i v_i) / sum(v_i)` on those same swaps, with a percentile bootstrap 95% interval. Test-token rows are the ones the model did not train on.

| Symbol | Split | Rule % | ML % | ML 95% CI | Real swaps |
|---|---|---:|---:|---|---:|
| ENS | train | 0.3 | 5.6 | [4.8, 6.4] | 131 |
| UNI | calib | 6.1 | 5.3 | [4.8, 5.9] | 5845 |
| LINK | train | 7.2 | 8.1 | [6.9, 9.8] | 3173 |
| AAVE | train | 13.1 | 13.9 | [6.8, 22.0] | 2657 |
| MKR | test | 0.4 | 2.4 | [2.0, 2.8] | 157 |
| LDO | train | 13.1 | 12.8 | [11.0, 15.2] | 1253 |
| CRV | test | 5.2 | 9.0 | [7.8, 10.3] | 788 |
| COMP | train | 4.3 | 4.1 | [3.3, 5.1] | 1339 |
| SUSHI | train | 0.3 | 2.6 | [2.0, 3.4] | 181 |
| 1INCH | test | 0.0 | 1.6 | [1.4, 2.2] | 67 |
| GRT | test | 8.0 | 7.1 | [2.9, 18.3] | 361 |
| BAL | train | 0.0 | 1.2 | [0.6, 2.1] | 11 |
| SNX | train | 0.1 | 2.6 | [1.9, 3.4] | 122 |
| RPL | test | 0.8 | 4.4 | [3.8, 5.1] | 653 |
| APE | train | 0.0 | 1.7 | [1.3, 2.2] | 116 |
| TURBO | train | 0.0 | 0.7 | [0.5, 0.9] | 126 |
| PEPE | test | 1.5 | 3.4 | [3.0, 3.9] | 2018 |
| SHIB | test | 2.4 | 7.9 | [6.1, 10.0] | 377 |
| LADYS | train | 18.8 | 16.7 | [13.2, 22.0] | 210 |
| Mog | train | 2.5 | 3.6 | [3.2, 4.0] | 632 |
| BITCOIN | train | 0.4 | 2.1 | [1.7, 2.7] | 486 |
| CULT | train | 0.0 | 7.0 | [1.1, 13.0] | 18 |

![Rule vs ML wash percent](figures/wash_percent_comparison.png)

## Limitations

- The plants are drawn from one generator. High recall means the model recognised that generator on a new token, not that it has seen every real wash ring.
- Real wash in a test token lowers planted-task precision when the model is right about it. Read the rule-agreement line next to the headline.
- The rule and the model share the same block window and tolerance family. They can agree because the behaviour is obvious (a buyer who forwards the tokens), not because the model was shown the rule's output column.
- USD uses one end-of-window ETH price for WETH legs. A 2-day public-RPC window is not the token's life. `held_days` and quiet wallets outside the window are invisible.
- Isolation Forest is fit on non-planted training rows. It is an anomaly rank, not a calibrated probability.
- SHAP explains the raw XGBoost margin. Platt scaling is monotone, so the sign of a reason is the same after calibration, but the probability itself is not a SHAP unit.
