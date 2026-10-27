from __future__ import annotations

from dataclasses import dataclass

V2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
V3_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"

QUOTE_TOKENS = (WETH, USDC, USDT, DAI)
STABLECOINS = frozenset({USDC, USDT, DAI})

WETH_USDC_POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"  # token0 is USDC

V3_FEES = (100, 500, 3000, 10000)

MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
ZERO_ADDRESS = "0x" + "00" * 20
BURN_ADDRESS = "0x" + "00" * 19 + "dead"

# tx.from is the trader. These show up as Swap.sender, but linking through
# them would glue every user of a router into one wallet.
ROUTERS = frozenset(
    {
        "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
        "0xe592427a0aece92de3edee1f18e0157c05861564",
        "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
        "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b",
        "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",
        "0x000000000022d473030f116ddee9f6b43ac78ba3",
        ZERO_ADDRESS,
        BURN_ADDRESS,
    }
)

# llamarpc answers with a browser challenge, and Ankr's public URL needs a key.
DEFAULT_RPCS = (
    "https://eth.drpc.org",
    "https://rpc.mevblocker.io",
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.flashbots.net",
    "https://eth-mainnet.public.blastapi.io",
    "https://1rpc.io/eth",
)

UNISWAP_V3_SUBGRAPH = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
UNISWAP_V2_SUBGRAPH = "A3Np3RQbaBA6oKJgiwDJeo5T3zrYfGHPWFYayMwtNDum"
GRAPH_GATEWAY = "https://gateway.thegraph.com/api"

MAX_WINDOW_DAYS = 90
DEFAULT_DB = "data/washdetect.duckdb"
BALANCE_CAP = 800

APPROX_BLOCK_TIME = 12  # hint only; the loader binary-searches timestamps


@dataclass(frozen=True)
class HeuristicConfig:
    """Gan et al. 2024: 2–10 swaps in 200 blocks, net within 5%. Token transfers stand in for ETH hops eth_getLogs cannot see."""

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
    max_group_size: int = 16  # larger components are skipped; a trimmed subset would invent a match
    max_flow_groups: int = 500
    max_flow_seeds: int = 20_000
