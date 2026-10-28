from __future__ import annotations

from dataclasses import dataclass

V2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
V3_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
V2_FACTORY_BLOCK = 10_000_835
V3_FACTORY_BLOCK = 12_369_621

# Uniswap v4 is one PoolManager. Pool ids come from Initialize, not from a per-pool contract.
POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
V4_START_BLOCK = 21_688_329

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
WBTC = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
USDE = "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"
USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"

# getPair/getPool bases. WBTC, USDe, and USDS catch pairs the four quotes miss.
POOL_BASES = (WETH, USDC, USDT, DAI, WBTC, USDE, USDS)
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

# Public hot wallets. They are hops, not traders: a transfer through one of them
# does not make two users the same wash group. Addresses are the well-known
# deposit wallets published by the exchanges.
CEX_HOT = frozenset(
    {
        "0x28c6c06298d514db089934071355e5743bf21d60",  # Binance 14
        "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8",  # Binance 7
        "0x71660c4005ba85c37ccec55d0c4493e66fe775d3",  # Coinbase
        "0x2910543af39aba0cd09dbb2d50200b3e800a63d2",  # Kraken
    }
)

# Public aggregator contracts. A transfer through one is a route, not a wash hop.
AGGREGATORS = frozenset(
    {
        "0x1111111254eeb25477b68fb85ed929f73a960582",  # 1inch v5
        "0x111111125421ca6dc452d289314280a0f8842a65",  # 1inch v6
        "0xdef1c0ded9bec7f1a1670819833240f027b25eff",  # 0x Exchange Proxy
    }
)

# Public MEV searchers. They touch many users in one block and are not a shared controller.
MEV_ACTORS = frozenset(
    {
        "0xae2fc483527b8ef99eb5d9b44875f005ba1fae13",  # jaredfromsubway.eth
    }
)

RULE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6")

# The first three accept multi-thousand-block eth_getLogs. dRPC's free plan rejects
# those and says "10000" while actually capping near 100 blocks; Blast caps at 10.
DEFAULT_RPCS = (
    "https://rpc.mevblocker.io",
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.flashbots.net",
    "https://eth.drpc.org",
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

# Same Swap/PairCreated/PoolCreated ABIs as Uniswap. Off unless a load asks for forks.
FORK_VENUES = (
    ("sushiswap", "v2", "0xc0aee478e3658e2610c5f7a4a2e1777ce9e4f2ac", 10_794_229),
    ("sushiswap", "v3", "0xbaceb8ec6b9355dfc0269c18bac9d6e2bdc29c4f", 16_955_547),
    ("pancakeswap", "v3", "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865", 16_950_586),
)


def protocol_family(protocol: str) -> str:
    text = protocol.lower()
    if "v4" in text:
        return "v4"
    if text == "v2" or text.endswith("-v2"):
        return "v2"
    if text == "v3" or text.endswith("-v3"):
        return "v3"
    return text


def dex_name(protocol: str) -> str:
    if protocol in {"v2", "v3", "v4"}:
        return "uniswap"
    return protocol.split("-", 1)[0] or "uniswap"


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
    enabled_rules: tuple[str, ...] = RULE_IDS
