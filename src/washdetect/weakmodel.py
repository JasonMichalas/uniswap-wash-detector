"""XGBoost trained on behaviour features against label-model targets, with Platt scaling on held-out tokens."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from washdetect.behaviour import TRADE_OWN, WALLET_BEHAVIOUR, BehaviourFeatures, with_tx_meta
from washdetect.context import CONTEXT_FEATURES, context_features
from washdetect.dataset import events_from_frames
from washdetect.funding import FUNDING_FEATURES, funding_features
from washdetect.models import ARTIFACT_DIR, ModelNotTrained, PlattCalibrator, fit_platt
from washdetect.weak import VOTERS, LabelModel, Votes

WEAK_DIR = ARTIFACT_DIR / "weak"
WALLET_SETS = {
    "base": list(WALLET_BEHAVIOUR),
    "batch1": [*WALLET_BEHAVIOUR, *CONTEXT_FEATURES],
    "batch2": [*WALLET_BEHAVIOUR, *CONTEXT_FEATURES, *FUNDING_FEATURES],
}
PARAMS = dict(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.05,
    min_child_weight=5,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=2.0,
    objective="binary:logistic",
    random_state=0,
    n_jobs=2,
    verbosity=0,
)


def wallet_table(
    features: BehaviourFeatures,
    liquidity: pd.DataFrame | None = None,
    pool_fees: dict[str, float] | None = None,
    funding: tuple[dict, dict] | None = None,
) -> pd.DataFrame:
    """Behaviour, context and (when looked up) funding columns, one row per trader."""
    wallets = features.wallets()
    context = context_features(features.swaps, features.transfers, features.pools, liquidity, pool_fees, features.eth_usd())
    wallets = wallets.merge(context, on="wallet", how="left")
    providers = set()
    if liquidity is not None and "provider" in liquidity.columns:
        providers = set(liquidity["provider"].dropna().astype(str).str.lower())
    if funding is not None:
        wallets = wallets.merge(funding_features(features.swaps, providers, *funding), on="wallet", how="left")
    return wallets.reindex(columns=["wallet", *WALLET_SETS["batch2"]])


def pool_fees(pools: pd.DataFrame | list[dict] | None) -> dict[str, float]:
    frame = pd.DataFrame(pools if pools is not None else [])
    if frame.empty or "fee" not in frame.columns:
        return {}
    return dict(zip(frame["pool_address"].astype(str).str.lower(), pd.to_numeric(frame["fee"], errors="coerce"), strict=False))


@dataclass
class TokenFrames:
    name: str
    wallets: pd.DataFrame  # behaviour features plus votes
    trades: pd.DataFrame

    @classmethod
    def build(
        cls,
        name: str,
        swaps: pd.DataFrame,
        transfers: pd.DataFrame,
        meta: pd.DataFrame,
        pools: set[str],
        liquidity: pd.DataFrame | None = None,
        fees: dict[str, float] | None = None,
        funding: tuple[dict, dict] | None = None,
    ) -> TokenFrames:
        swaps = with_tx_meta(swaps, meta)
        swaps = swaps.assign(trader=swaps["trader"].str.lower(), tx_hash=swaps["tx_hash"].str.lower())
        features = BehaviourFeatures(swaps, transfers, pools)
        wallets = wallet_table(features, liquidity, fees, funding)
        trades = features.trades(wallets)
        events, transfer_events = events_from_frames(swaps, transfers)
        for e in events:
            e.pool = e.pool.lower()
        votes = Votes(events, transfer_events, pools).trade_votes()
        trades = trades.merge(votes.drop(columns="wallet"), on=["tx_hash", "log_index"], how="inner")
        wallets = wallets.merge(Votes.wallet_votes(votes), on="wallet", how="inner")
        return cls(name, wallets.assign(token=name), trades.assign(token=name))


class Booster:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns
        self.model = None
        self.calibrator = PlattCalibrator()
        self.threshold = 0.5

    def matrix(self, frame: pd.DataFrame) -> np.ndarray:
        x = frame.reindex(columns=self.columns).apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        return np.clip(np.where(np.isfinite(x), x, np.nan), -1e9, 1e9)

    def fit(self, frame: pd.DataFrame, soft: np.ndarray) -> None:
        from xgboost import XGBClassifier

        x = self.matrix(frame)
        # Soft labels: each row appears once as wash and once as not, weighted by the label model.
        xx = np.vstack([x, x])
        yy = np.r_[np.ones(len(x)), np.zeros(len(x))]
        ww = np.r_[soft, 1 - soft]
        pos = soft.sum()
        self.model = XGBClassifier(**PARAMS, scale_pos_weight=max(1.0, (len(soft) - pos) / max(pos, 1.0)))
        self.model.fit(xx, yy, sample_weight=ww)

    def raw(self, frame: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise ModelNotTrained("The weak-label model is not trained.")
        return self.model.predict_proba(self.matrix(frame))[:, 1]

    def proba(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.calibrator.transform(self.raw(frame)))

    def calibrate(self, frame: pd.DataFrame, labels: np.ndarray) -> None:
        """Platt scaling, then the cut-off with the best F1 against the weak labels, both on held-out tokens."""
        from sklearn.metrics import precision_recall_curve

        self.calibrator = fit_platt(self.raw(frame), labels)
        precision, recall, cuts = precision_recall_curve(labels, self.proba(frame))
        f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
        self.threshold = float(cuts[f1.argmax()]) if len(cuts) else 0.5


class WeakWashModel:
    """Wallet and swap models. Rules only make the training targets; the models never see rule outputs."""

    def __init__(self, features: str = "batch2") -> None:
        self.wallet = Booster(list(WALLET_SETS[features]))
        self.trade = Booster([*TRADE_OWN, *WALLET_SETS[features]])
        self.wallet_labels = LabelModel()
        self.trade_labels = LabelModel()
        self.manifest: dict = {}

    def train(self, train: list[TokenFrames], calib: list[TokenFrames]) -> None:
        wallets = pd.concat([t.wallets for t in train], ignore_index=True)
        trades = pd.concat([t.trades for t in train], ignore_index=True)
        self.wallet_labels.fit(wallets[VOTERS])
        self.trade_labels.fit(trades[VOTERS])
        self.wallet.fit(wallets, self.wallet_labels.predict_proba(wallets[VOTERS]))
        self.trade.fit(trades, self.trade_labels.predict_proba(trades[VOTERS]))
        if calib:
            cw = pd.concat([t.wallets for t in calib], ignore_index=True)
            ct = pd.concat([t.trades for t in calib], ignore_index=True)
            self.wallet.calibrate(cw, self.wallet_labels.predict_proba(cw[VOTERS]) >= 0.5)
            self.trade.calibrate(ct, self.trade_labels.predict_proba(ct[VOTERS]) >= 0.5)
        self.manifest = {
            "train": [t.name for t in train],
            "calib": [t.name for t in calib],
            "wallet_rows": len(wallets),
            "trade_rows": len(trades),
            "wallet_prior": self.wallet_labels.prior,
            "trade_prior": self.trade_labels.prior,
            "wallet_threshold": self.wallet.threshold,
            "trade_threshold": self.trade.threshold,
        }

    def save(self, directory: str | Path = WEAK_DIR) -> Path:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        self.wallet.model.save_model(path / "wallet.json")
        self.trade.model.save_model(path / "trade.json")
        payload = {
            "wallet_calibrator": self.wallet.calibrator.to_dict(),
            "trade_calibrator": self.trade.calibrator.to_dict(),
            "wallet_columns": self.wallet.columns,
            "trade_columns": self.trade.columns,
            "voters": VOTERS,
            "wallet_label_model": {"prior": self.wallet_labels.prior, "table": self.wallet_labels.table.tolist()},
            "trade_label_model": {"prior": self.trade_labels.prior, "table": self.trade_labels.table.tolist()},
            "manifest": self.manifest,
        }
        (path / "weak.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, directory: str | Path = WEAK_DIR) -> WeakWashModel:
        from xgboost import XGBClassifier

        path = Path(directory)
        if not (path / "weak.json").is_file():
            raise ModelNotTrained(f"No weak-label model at {path}.")
        payload = json.loads((path / "weak.json").read_text(encoding="utf-8"))
        model = cls()
        for name in ("wallet", "trade"):
            booster = getattr(model, name)
            booster.model = XGBClassifier()
            booster.model.load_model(path / f"{name}.json")
            booster.columns = list(payload[f"{name}_columns"])
            booster.calibrator = PlattCalibrator.from_dict(payload[f"{name}_calibrator"])
            labels = getattr(model, f"{name}_labels")
            labels.prior = payload[f"{name}_label_model"]["prior"]
            labels.table = np.asarray(payload[f"{name}_label_model"]["table"])
        model.manifest = payload["manifest"]
        model.wallet.threshold = model.manifest.get("wallet_threshold", 0.5)
        model.trade.threshold = model.manifest.get("trade_threshold", 0.5)
        return model


def split_tokens(names: list[str], n_calib: int = 2, seed: int = 0) -> tuple[list[str], list[str]]:
    ordered = sorted(names)
    random.Random(seed).shuffle(ordered)
    return sorted(ordered[n_calib:]), sorted(ordered[:n_calib])


def frames_from_db(name: str, db_path: str, token: str, budget: float | None = None) -> TokenFrames:
    """Funding lookups wait up to `budget` seconds (forever when None); what is not back yet stays missing."""
    from washdetect.funding import lookup
    from washdetect.store import Store

    store = Store(db_path)
    token = token.lower()
    lo, hi = store.con.execute(
        "SELECT min(block_number), max(block_number) FROM swaps WHERE token = ?", [token]
    ).fetchone()
    listed = store.pools_for(token)
    pools = {str(p["pool_address"]).lower() for p in listed}
    swaps = store.read_swaps(token, lo, hi)
    traders = sorted(swaps["trader"].dropna().str.lower().unique())
    used = sorted(swaps["pool_address"].str.lower().unique())
    return TokenFrames.build(
        name,
        swaps,
        store.read_transfers(token, lo, hi),
        store.read_tx_meta(token, lo, hi),
        pools,
        store.read_liquidity(token, lo, hi),
        pool_fees(listed),
        lookup(traders, used, budget),
    )
