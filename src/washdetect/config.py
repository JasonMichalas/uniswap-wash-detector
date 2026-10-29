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
        "0x66a9893cc07d91d95644aedd05d03f95e1dba8af",  # Universal Router v2
        "0x4c82d1fbfe28c977cbb58d8c7ff8fcf9f70a2cca",  # Universal Router v2.1.1
        "0x23617e59a5925b2a4bf75d73ff6711cd0b29de85",  # Universal Router v2.1.2
        "0xb300000b72deaeb607a12d5f54773d1c19c7028d",  # Binance DEX Router
        "0x881d40237659c251811cec9c364ef91dc08d300c",  # MetaMask Swap Router
        "0x74de5d4fcbf63e00296fd95d33236b9794016631",  # MetaMask Swaps Spender
        "0xac4c6e212a361c968f1725b4d055b47e63f80b75",  # SushiSwap RedSnwapper
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
        "0x21a31ee1afc51d94c2efccaa2092ad1028285549",  # Binance 15
        "0xdfd5293d8e347dfe59e90efd55b2956a1343963d",  # Binance 16
        "0xcc282e2004428939ee5149a9e7872f0b4d5d5ec7",  # Kraken Hot Wallet 3
        "0x62425cd6bdcb6bfe51558ea465b063486b70dc9f",  # Bybit Wallet 55
    }
)

# Public aggregator contracts. A transfer through one is a route, not a wash hop.
AGGREGATORS = frozenset(
    {
        "0x1111111254eeb25477b68fb85ed929f73a960582",  # 1inch v5
        "0x111111125421ca6dc452d289314280a0f8842a65",  # 1inch v6
        "0xdef1c0ded9bec7f1a1670819833240f027b25eff",  # 0x Exchange Proxy
        "0x0000000000001ff3684f28c67538d4d072c22734",  # 0x Allowance Holder
        "0x6131b5fae19ea4f9d964eac0408e4408b66337b5",  # KyberSwap Meta Aggregation v2
        "0x6a000f20005980200259b80c5102003040001068",  # Velora Augustus v6.2
        "0x00700052c0608f670705380a4900e0a8080010cc",  # Velora fee vault
        "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",  # LI.FI Diamond
        "0x89c6340b1a1f4b25d36cd8b063d49045caf3f818",  # LI.FI Permit2 proxy
        "0x69460570c93f9de5e2edbc3052bf10125f0ca22d",  # Rango Diamond
        "0xb92fe925dc43a0ecde6c8b1a2709c170ec4fff4f",  # Relay Router v3
        "0xccc88a9d1b4ed6b0eaba998850414b24f1c315be",  # Relay approval proxy
        "0x9008d19f58aabd9ed0d60971565aa8510560ab41",  # CoW settlement
        "0xbeb0009aca35087ce7ccf11637e24dd1aad3bf2a",  # Bebop RFQ
        "0x337685fdab40d39bd02028545a4ffa7d287cc3e2",  # Mayan forwarder
        "0x663dc15d3c1ac63ff12e45ab68fea3f0a883c251",  # deBridge forwarder
        "0x2cff890f0378a11913b6129b2e97417a2c302680",  # NEAR Intents
        "0x719f8b330cca71cb6195d032a43194c7d3f9fb45",  # Euler swapper
    }
)

# Lending, vaults and account-abstraction contracts. They hold many users' tokens.
PROTOCOLS = frozenset(
    {
        "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",  # Aave Pool v3
        "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb",  # Morpho
        "0x38b0c12ab81976e9417d4ebfe2a34db6df22e6ad",  # Morpho Blue bundler
        "0x02912516d49de997db75b9d7858faae59209650b",  # Morpho vault bundler
        "0xba1333333333a1ba1108e8412f11850a5c319ba9",  # Balancer v3 vault
        "0xba12222222228d8ba445958a75a0704d566bf2c8",  # Balancer v2 vault
        "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789",  # EntryPoint 0.6
        "0x0000000071727de22e5e9d8baf0edac6f37da032",  # EntryPoint 0.7
        "0x4337084d9e255ff0702461cf8895ce9e3b5ff108",  # EntryPoint 0.8
    }
)

# Public MEV searchers. They touch many users in one block and are not a shared controller.
MEV_ACTORS = frozenset(
    {
        "0xae2fc483527b8ef99eb5d9b44875f005ba1fae13",  # jaredfromsubway.eth
    }
)



def hop_addresses() -> set[str]:
    """Shared contracts that move many users' tokens. A transfer through one never links two wallets."""
    return {*ROUTERS, *AGGREGATORS, *CEX_HOT, *MEV_ACTORS, *PROTOCOLS, POOL_MANAGER}


RULE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6")

# The first three accept multi-thousand-block eth_getLogs. dRPC's free plan rejects
# those and says "10000" while actually capping near 100 blocks; Blast caps at 10.
DEFAULT_RPCS = (
    "https://rpc.mevblocker.io",
    "https://gateway.tenderly.co/public/mainnet",
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.flashbots.net",
    "https://eth.drpc.org",
    "https://eth-mainnet.public.blastapi.io",
    "https://1rpc.io/eth",
)

MAX_WINDOW_DAYS = 90
DEFAULT_DB = "data/washdetect.duckdb"
BALANCE_CAP = 800

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
