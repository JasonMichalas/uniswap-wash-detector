import numpy as np
import pandas as pd
import pytest
from tests.test_weak import _frames

from washdetect.behaviour import BehaviourFeatures, with_tx_meta
from washdetect.funding import funding_features


def test_funding_features_count_shared_funders_and_ignore_hubs():
    swaps, meta = _frames()
    features = BehaviourFeatures(with_tx_meta(swaps, meta), pd.DataFrame(), {"0xpool"})
    t0 = int(pd.Timestamp("2026-10-19").timestamp())
    info = {w: {"funder": "0xboss", "tx": "0xd1", "ts": t0, "exit": "0xsink", "funder_txs": 5} for w in ("0xwash", "0xr0", "0xr2")}
    info["0xr4"] = {"funder": "0xcex", "tx": "0xd2", "ts": t0, "exit": "", "funder_txs": 50_000}
    info["0xr6"] = {"funder": None, "exit": None}
    out = funding_features(features.swaps, {"0xboss"}, info, {"0xpool": t0}).set_index("wallet")
    assert out.loc["0xwash"].log_funder_fanout == pytest.approx(np.log1p(2))
    assert out.loc["0xwash"].log_same_tx_funded == pytest.approx(np.log1p(2)) and out.loc["0xwash"].funder_is_lp == 1.0
    assert out.loc["0xr4"].funder_is_hub == 1.0 and out.loc["0xr4"].log_funder_fanout == 0.0
    assert np.isnan(out.loc["0xr6"].log_funder_fanout) and np.isnan(out.loc["0xr8"].log_funder_fanout)
    assert out.loc["0xwash"].log_exit_fanin == pytest.approx(np.log1p(2))
