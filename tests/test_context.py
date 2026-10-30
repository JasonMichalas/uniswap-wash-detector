import numpy as np
import pandas as pd
import pytest
from tests.test_weak import _frames

from washdetect.behaviour import BehaviourFeatures, with_tx_meta
from washdetect.context import context_features


def test_context_features_twins_rhythm_lp_and_burn():
    swaps, meta = _frames()
    meta.loc[swaps.trader == "0xwash", "gas_limit"] = 123457
    meta.loc[swaps.trader == "0xr0", "gas_limit"] = 123457
    features = BehaviourFeatures(with_tx_meta(swaps, meta), pd.DataFrame(), {"0xpool"})
    liquidity = pd.DataFrame({"provider": ["0xwash"]})
    out = context_features(features.swaps, features.transfers, features.pools, liquidity, {"0xpool": 3000}, 3000.0).set_index("wallet")
    wash = out.loc["0xwash"]
    assert wash.is_lp == 1.0 and out.loc["0xr2"].is_lp == 0.0
    assert wash.log_gas_twins == pytest.approx(np.log1p(1)) and wash.gas_twin_frac == 1.0
    assert wash.burstiness == pytest.approx(-1.0) and wash.tx_per_active_block == 1.0
    assert wash.fee_burn == pytest.approx(0.003 + 20 * 2 * 123457 / 1e9 * 3000 / 30000)
