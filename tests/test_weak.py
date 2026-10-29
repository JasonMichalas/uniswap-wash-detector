import numpy as np
import pandas as pd

from washdetect.baselines import size_tests, zero_net_sets
from washdetect.behaviour import WALLET_BEHAVIOUR, BehaviourFeatures, with_tx_meta
from washdetect.events import SwapEvent, TransferEvent
from washdetect.pipeline import tx_meta_row
from washdetect.store import Store
from washdetect.weak import LabelModel, quick_flips
from washdetect.weakmodel import TokenFrames, WeakWashModel


def swap(wallet, block, side, amount=100.0, usd=100.0, n=0):
    return SwapEvent(wallet=wallet, block=block, side=side, amount=amount, usd=usd, tx_hash=f"0x{wallet}{block}{n}", log_index=n)


def test_quick_flips_needs_three_close_pairs():
    rows = [swap("a", b, s, n=i) for i, (b, s) in enumerate([(10, "buy"), (20, "sell"), (100, "buy"), (110, "sell"), (200, "buy"), (210, "sell")])]
    assert len(quick_flips(rows)) == 6
    assert quick_flips(rows[:4]) == set()
    far = [swap("b", b, s, n=i) for i, (b, s) in enumerate([(10, "buy"), (60, "sell"), (100, "buy"), (150, "sell"), (200, "buy"), (250, "sell")])]
    assert quick_flips(far) == set()


def test_label_model_trusts_accurate_voter():
    rng = np.random.default_rng(0)
    truth = rng.random(2000) < 0.3
    good = np.where(truth, np.where(rng.random(2000) < 0.9, 1, -1), np.where(rng.random(2000) < 0.02, 1, -1))
    noisy = np.where(rng.random(2000) < 0.3, 1, -1)
    neg = np.where(~truth & (rng.random(2000) < 0.8), 0, -1)
    model = LabelModel().fit(np.c_[good, noisy, neg])
    p = model.predict_proba(np.c_[good, noisy, neg])
    assert ((p >= 0.5) == truth).mean() > 0.8
    assert model.predict_proba(np.array([[-1, 1, -1]]))[0] < model.predict_proba(np.array([[1, -1, -1]]))[0]


def test_zero_net_sets_joins_strongly_connected_wallets():
    swaps = [swap("a", 10, "buy", 100), swap("b", 20, "sell", 100.5)]
    transfers = [TransferEvent("a", "b", 5, 1, "t1", 0), TransferEvent("b", "a", 6, 1, "t2", 0)]
    assert len(zero_net_sets(swaps, transfers, set())) == 2
    assert zero_net_sets(swaps, transfers[:1], set()) == set()


def test_size_tests_spots_round_sizes():
    rng = np.random.default_rng(1)
    natural = size_tests(list(np.exp(rng.normal(5, 2, 500))))
    rounded = size_tests([1000.0] * 300 + list(np.exp(rng.normal(5, 2, 200))))
    assert rounded["round_ratio"] > 3 * natural["round_ratio"]
    assert natural["benford_mad"] < 0.02
    assert np.isnan(size_tests([1.0, 2.0])["benford_mad"])


def test_tx_meta_row_and_store_roundtrip():
    row = tx_meta_row("0xabc", {"to": "0x" + "1" * 40, "nonce": "0x10", "transactionIndex": "0x1", "gasPrice": "0x3b9aca00", "gas": "0x5208"})
    assert row == ("0xabc", "0x" + "1" * 40, 16, 1, 1.0, None, 21000)
    store = Store(":memory:")
    store.remember_tx_meta([row])
    store.remember_tx_meta([row])
    assert store.con.execute("SELECT count(*) FROM tx_meta").fetchone()[0] == 1


def _frames():
    ts = pd.Timestamp("2026-10-20")
    rows = []
    for i in range(40):
        wallet = "0xwash" if i % 2 else f"0xr{i}"
        rows.append({
            "tx_hash": f"0x{i:04x}", "log_index": 0, "block_number": 100 + i * 5, "block_timestamp": ts + pd.Timedelta(minutes=i),
            "pool_address": "0xpool", "trader": wallet, "side": "buy" if i % 4 in (0, 1) else "sell",
            "amount_token": 1000.0, "amount_quote": 0.5, "quote_token": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "usd_value": 1500.0,
        })
    swaps = pd.DataFrame(rows)
    meta = pd.DataFrame({"tx_hash": swaps.tx_hash, "tx_to": "0xrouter", "nonce": range(40), "tx_index": 1, "gas_price": 2.0, "priority_fee": 0.1, "gas_limit": 200000})
    return swaps, meta


def test_behaviour_features_and_weak_model(tmp_path):
    swaps, meta = _frames()
    features = BehaviourFeatures(with_tx_meta(swaps, meta), pd.DataFrame(), {"0xpool"})
    wallets = features.wallets()
    assert set(WALLET_BEHAVIOUR) <= set(wallets.columns)
    wash = wallets.set_index("wallet").loc["0xwash"]
    assert wash.round_amount_frac == 1.0 and wash.log_n_trades > 2
    frames = TokenFrames.build("t", swaps, pd.DataFrame(columns=["tx_hash", "log_index", "block_number", "block_timestamp", "from_address", "to_address", "amount"]), meta, {"0xpool"})
    frames.trades.loc[frames.trades.wallet == "0xwash", "R1"] = 1
    frames.wallets.loc[frames.wallets.wallet == "0xwash", "R1"] = 1
    model = WeakWashModel()
    model.train([frames, frames], [])
    model.save(tmp_path)
    loaded = WeakWashModel.load(tmp_path)
    assert np.allclose(loaded.trade.proba(frames.trades), model.trade.proba(frames.trades))
    assert loaded.manifest["train"] == ["t", "t"]
