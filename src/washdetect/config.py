"""Addresses, heuristic parameters, and RPC defaults.

Everything the detector treats as "not a trader" lives here so a student can
extend the router list without hunting through the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

# Uniswap factories on Ethereum mainnet.
V2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
V3_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"

# Quote assets used by on-demand pool discovery. getPair / getPool against
# these covers the pools that carry almost all retail volume. Exotic quote
# tokens are a later extension (factory-log scan).
QUOTE_TOKENS = (WETH, USDC, USDT, DAI)
STABLECOINS = frozenset({USDC, USDT, DAI})

# Canonical ETH/USD source: Uniswap v3 USDC/WETH 0.05% pool. token0 is USDC.
WETH_USDC_POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"

V3_FEES = (100, 500, 3000, 10000)

MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
ZERO_ADDRESS = "0x" + "00" * 20
BURN_ADDRESS = "0x" + "00" * 19 + "dead"

# Routers and other contracts that show up as Swap.sender / Transfer parties
# but are not the economic trader. tx.from is the trader we keep; these
# addresses are excluded from wallet-linking so every user of the router is
# not glued into one giant component.
ROUTERS = frozenset(
    {
        "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",  # UniswapV2Router02
        "0xe592427a0aece92de3edee1f18e0157c05861564",  # SwapRouter
        "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",  # SwapRouter02
        "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b",  # Universal Router (old)
        "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",  # Universal Router
        "0x000000000022d473030f116ddee9f6b43ac78ba3",  # Permit2
        ZERO_ADDRESS,
        BURN_ADDRESS,
    }
)

# Public Ethereum mainnet endpoints. No API key. The client rotates through
# this list when one of them rate-limits or rejects a log range.
DEFAULT_RPCS = (
    "https://eth.drpc.org",
    "https://ethereum-rpc.publicnode.com",
    "https://1rpc.io/eth",
    "https://rpc.ankr.com/eth",
    "https://eth.llamarpc.com",
    "https://rpc.mevblocker.io",
)

# The Graph decentralized network. Used only when GRAPH_API_KEY is set.
# IDs are the ones published in Uniswap's subgraph docs.
UNISWAP_V3_SUBGRAPH = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
UNISWAP_V2_SUBGRAPH = "A3Np3RQbaBA6oKJgiwDJeo5T3zrYfGHPWFYayMwtNDum"
GRAPH_GATEWAY = "https://gateway.thegraph.com/api"

MAX_WINDOW_DAYS = 90
DEFAULT_DB = "data/washdetect.duckdb"
BALANCE_CAP = 800

# Blocks per day on post-merge Ethereum is about 7200 (12s). Used only as a
# hint; the loader binary-searches real timestamps.
APPROX_BLOCK_TIME = 12


@dataclass(frozen=True)
class HeuristicConfig:
    """Parameters of the rule-based wash labeler.

    They follow Gan et al. 2024 (ACM TOIT): a wash sequence is 2 to 10 swaps
    inside 200 blocks whose net token-position change is at most 5 percent.
    Wallets are linked when a token transfer (or a shared funder) connects
    them within two hops, which stands in for the paper's ETH-transfer hops.
    Public eth_getLogs cannot see native ETH transfers; see the README.
    """

    max_block_gap: int = 200
    min_swaps: int = 2
    max_swaps: int = 10
    net_ratio: float = 0.05
    max_cycle_len: int = 5
    hub_degree: int = 25
    max_edges_per_node: int = 20
    max_dfs_visits: int = 20_000
    max_cycles: int = 500
    max_links: int = 2_000
    max_triangles: int = 400
    # Flow groups: a connected set of wallets whose pooled buys and sells
    # net out. Covers one-to-many, many-to-one, many-to-many, and rings,
    # not only pairwise round-trips. Capped so a busy component cannot
    # glue the whole token into one group.
    max_group_size: int = 16
    max_flow_groups: int = 500
    max_flow_seeds: int = 20_000
