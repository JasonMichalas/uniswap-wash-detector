"""Uniswap Wash Detector: wash-trading detection for Uniswap v2 and v3 on Ethereum mainnet.

ECM3401 dissertation (University of Exeter). The package loads pool data from
public RPC endpoints, stores it in DuckDB, and lets a researcher build
analyses as a node graph. The graph compiles to a small query language, which
compiles to read-only SQL. The detector flags round-trips and connected flow
groups (one-to-many, many-to-one, many-to-many, rings). A shipped Isolation
Forest and calibrated XGBoost score wallets and swaps; TreeSHAP explains a
wallet. Loading a token does not run either.
"""

__version__ = "0.1.0"
