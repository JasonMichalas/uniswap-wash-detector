# Evaluation: wash models

Milestone 2 of the Uniswap Wash Detector (ECM3401, University of Exeter). There is no public ground truth, so the supervised numbers below are recall and precision on wash groups planted into real pool history, with train and test split by token.

## Dataset

Window: the last 2 day(s) ending when each token was fetched, Ethereum mainnet, public RPC, no API key. Fetch s is the cold public-RPC scrape when a successful row exists in fetch-log.txt beside the database; otherwise it is the time spent loading that token during this training run.

| Symbol | Token | Real swaps | Real transfers | Fetch s | Split |
|---|---|---:|---:|---:|---|
| UNI | `0x1f9840a85d5af5bf1d1762f925bdaddc4201f984` | 5652 | 30205 | 299.0 | calib |
| 1INCH | `0x111111111117dc0aa78b770fa6a738034120c302` | 67 | 16796 | 609.8 | test |
| CRV | `0xd533a949740bb3306d119cc777fa900ba034cd52` | 778 | 25201 | 753.6 | test |
| GRT | `0xc944e90c64b2c07662a292be6244bdf05cda44a7` | 357 | 1435 | 71.2 | test |
| MKR | `0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2` | 157 | 673 | 57.1 | test |
| PEPE | `0x6982508145454ce325ddbe47a25d4ec3d2311933` | 2002 | 13393 | 151.2 | test |
| RPL | `0xd33526068d116ce69f19a9ee46f0bd304f21a51f` | 653 | 1685 | 154.1 | test |
| SHIB | `0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce` | 371 | 6373 | 82.7 | test |
| AAVE | `0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9` | 2610 | 11074 | 140.9 | train |
| APE | `0x4d224452801aced8b2f0aebe155379bb5d594381` | 116 | 1936 | 448.8 | train |
| BAL | `0xba100000625a3754423978a60c9317c58a424e3d` | 11 | 315 | 297.1 | train |
| BITCOIN | `0x72e4f9f808c49a2a61de9c5896298920dc4eeea9` | 485 | 922 | 105.9 | train |
| COMP | `0xc00e94cb662c3520282e6f5717214004a7f26888` | 1335 | 4677 | 118.4 | train |
| CULT | `0xf0f9d895aca5c8678f706fb8216fa22957685a13` | 18 | 56 | 222.4 | train |
| ENS | `0xc18360217d8f7ab5e7c516566761ea12ce7f9d72` | 127 | 822 | 112.0 | train |
| LADYS | `0x12970e6868f88f6557b76120662c1b3e50a646bf` | 204 | 407 | 29.5 | train |
| LDO | `0x5a98fcbea516cf06857215779fd812ca3bef1b32` | 1235 | 3442 | 112.8 | train |
| LINK | `0x514910771af9ca656af840dff83e8264ecf986ca` | 3113 | 15551 | 251.4 | train |
| Mog | `0xaaee1a9723aadb7afa2810263653a34ba2c21c7a` | 631 | 1131 | 80.6 | train |
| SNX | `0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f` | 120 | 806 | 89.8 | train |
| SUSHI | `0x6b3595068778dd592e39a122f4f5a5cf09c90fe2` | 181 | 1217 | 64.0 | train |
| TURBO | `0xa35923162c49cf95e6bf26623385eb431ad920d3` | 126 | 613 | 124.8 | train |

Train tokens: 14. Calibration tokens: 1. Test tokens: 7.

Planted groups (kept separate from the chain cache, addresses start with `0x57`) cover round trips, rings, one-to-many, many-to-one including a 2+2→4.01 fan-in, many-to-many with an intermediate shuffle, and a wallet that both buys and sells. Group sizes run from 1 to more than 10 wallets.

## Features

XGBoost and Isolation Forest see behavioural columns only: swap counts, log amounts, net position over the whole window, inter-swap timing and burstiness, pool count, counterparty degree, shared-funder degree, and the fraction of bought tokens sent onward (or sold tokens that arrived by transfer). They do not see `round_trip_count`, `wash_volume_usd`, `heuristic_label`, `heuristic_confidence`, `pattern_type`, or the group's buyer and seller counts. Those are labels or rule outputs. Wallet columns: n_swaps, n_buys, n_sells, buy_fraction, log_buy_amount, log_sell_amount, log_volume_usd, net_position_change, trade_interval_cv, median_inter_swap_blocks, burst_fraction, size_cv, n_pools, counterparty_degree, funding_source_degree, n_in_counterparties, n_out_counterparties, in_transfer_ratio, out_transfer_ratio, median_blocks_buy_to_out, both_sides. Swap columns add the swap's own size and the same wallet context. A planted row's label is 1 even when the rule misses it; a real row's label is the rule's weak label.

## Headline results (test tokens, planted swaps)

Positive class: a swap we inserted. Threshold 0.5 on the calibrated probability. PR-AUC is average precision over that ranking.

| | Precision | Recall | F1 | PR-AUC | Support |
|---|---:|---:|---:|---:|---:|
| Swap model | 92.3% | 100.0% | 96.0% | 1.000 | 644 / 5029 |
| Wallet model | 94.1% | 100.0% | 96.9% | 1.000 | 602 / 1872 |

A real wash swap that the model scores above 0.5 is a false positive on this planted-only task. Agreement with the rule, on real (non-planted) test swaps, is the right place to read that:

Rule agreement on real test swaps: precision 92.6%, recall 27.6%, F1 42.6% (the rule's hard label is the reference).

![Precision-recall on planted test swaps](figures/pr_curve.png)

## Recall by pattern

Swap-level recall: fraction of planted swaps of that pattern with probability ≥ 0.5.

| pattern_type | n | recall |
| --- | ---: | ---: |
| many_to_many | 245 | 100.0% |
| many_to_one | 175 | 100.0% |
| one_to_many | 154 | 100.0% |
| ring | 56 | 100.0% |
| round_trip | 14 | 100.0% |

![Recall by pattern](figures/recall_by_pattern.png)

## Recall by buyers × sellers

Same swap-level recall, split by the planted group's `n_buyers` and `n_sellers`.

| n_buyers | n_sellers | n | recall |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 70 | 100.0% |
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
| many_to_one | 6 | 1 | 7 | 7 | 100.0% |
| many_to_one | 10 | 1 | 11 | 7 | 100.0% |
| one_to_many | 1 | 2 | 3 | 7 | 100.0% |
| one_to_many | 1 | 3 | 4 | 7 | 100.0% |
| one_to_many | 1 | 5 | 6 | 7 | 100.0% |
| one_to_many | 1 | 8 | 9 | 7 | 100.0% |
| ring | 1 | 1 | 3 | 7 | 100.0% |
| ring | 1 | 1 | 4 | 7 | 100.0% |
| ring | 1 | 1 | 5 | 7 | 100.0% |
| ring | 1 | 1 | 8 | 7 | 100.0% |
| round_trip | 1 | 1 | 1 | 7 | 100.0% |

## Wash percent: rule vs model

Rule % is the Gan-style hard label on the real swaps only (plants removed). ML % is `100 * sum(p_i v_i) / sum(v_i)` on those same swaps, with a percentile bootstrap 95% interval. Test-token rows are the ones the model did not train on.

| Symbol | Split | Rule % | ML % | ML 95% CI | Real swaps |
|---|---|---:|---:|---|---:|
| ENS | train | 0.3 | 5.7 | [4.8, 6.6] | 127 |
| UNI | calib | 4.1 | 4.4 | [4.1, 4.7] | 5652 |
| LINK | train | 6.4 | 7.0 | [6.0, 8.1] | 3113 |
| AAVE | train | 12.8 | 12.9 | [6.3, 21.2] | 2610 |
| MKR | test | 0.4 | 2.1 | [1.8, 2.4] | 157 |
| LDO | train | 10.9 | 11.4 | [9.9, 12.7] | 1235 |
| CRV | test | 4.3 | 8.1 | [6.9, 9.6] | 778 |
| COMP | train | 3.9 | 3.6 | [2.6, 4.6] | 1335 |
| SUSHI | train | 0.3 | 2.7 | [2.0, 3.5] | 181 |
| 1INCH | test | 0.0 | 1.9 | [1.4, 2.5] | 67 |
| GRT | test | 6.7 | 5.6 | [2.2, 14.7] | 357 |
| BAL | train | 0.0 | 0.7 | [0.4, 1.1] | 11 |
| SNX | train | 0.1 | 2.4 | [1.9, 3.2] | 120 |
| RPL | test | 0.0 | 1.3 | [1.2, 1.5] | 653 |
| APE | train | 0.0 | 1.7 | [1.2, 2.3] | 116 |
| TURBO | train | 0.0 | 0.5 | [0.4, 0.7] | 126 |
| PEPE | test | 1.5 | 2.8 | [2.5, 3.2] | 2002 |
| SHIB | test | 2.4 | 7.5 | [6.0, 9.5] | 371 |
| LADYS | train | 10.4 | 11.3 | [8.1, 16.1] | 204 |
| Mog | train | 2.5 | 3.1 | [2.7, 3.5] | 631 |
| BITCOIN | train | 0.4 | 2.0 | [1.5, 2.6] | 485 |
| CULT | train | 0.0 | 1.9 | [0.7, 3.1] | 18 |

![Rule vs ML wash percent](figures/wash_percent_comparison.png)

## Limitations

- The plants are drawn from one generator. High recall means the model recognised that generator on a new token, not that it has seen every real wash ring.
- Real wash in a test token lowers planted-task precision when the model is right about it. Read the rule-agreement line next to the headline.
- The rule and the model share the same block window and tolerance family. They can agree because the behaviour is obvious (a buyer who forwards the tokens), not because the model was shown the rule's output column.
- USD uses one end-of-window ETH price for WETH legs. A 2-day public-RPC window is not the token's life. `held_days` and quiet wallets outside the window are invisible.
- Isolation Forest is fit on non-planted training rows. It is an anomaly rank, not a calibrated probability.
- SHAP explains the raw XGBoost margin. Platt scaling is monotone, so the sign of a reason is the same after calibration, but the probability itself is not a SHAP unit.
