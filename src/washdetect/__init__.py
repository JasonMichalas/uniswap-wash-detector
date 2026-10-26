"""Wash-trading detection for Uniswap v2 and v3 on Ethereum mainnet.

Milestone 1 of an ECM3401 dissertation (University of Exeter). The package
loads pool data from public RPC endpoints, stores it in DuckDB, and lets a
researcher build analyses as a node graph. The graph compiles to a small
query language, which compiles to read-only SQL. A Gan et al. (2024) style
heuristic flags round-trips and wallet rings. Isolation Forest and XGBoost
are stubbed for a later milestone.
"""

__version__ = "0.1.0"
