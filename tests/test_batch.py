"""A batch records a failed token and keeps going. A finished token is not fetched again."""

from types import SimpleNamespace

from washdetect.batch import BatchScrape
from washdetect.rpc import Cancelled
from washdetect.store import Store


def _token(byte: str) -> str:
    return "0x" + byte * 20


def test_batch_records_a_failure_and_skips_finished_tokens(tmp_path):
    db = tmp_path / "batch.duckdb"
    scrape = BatchScrape(str(db), days=7, token_seconds=30, include_forks=False, out_dir=str(tmp_path / "out"))
    tokens = [_token("aa"), _token("bb"), _token("cc")]

    def load(token: str, cancel):
        assert cancel() is False
        if token == _token("bb"):
            raise RuntimeError("rpc down")
        if token == _token("cc"):
            raise Cancelled("time limit")
        return SimpleNamespace(symbol="AAA", pools=2, swaps=5, transfers=6, seconds=1.2)

    first = scrape.run(tokens, load=load)
    assert first["done"] == 1
    assert first["failed"] == 1
    assert first["partial"] == 1
    store = Store(db)
    rows = {row["token"]: row["status"] for row in store.batch_rows("days:7")}
    store.close()
    assert rows == {_token("aa"): "done", _token("bb"): "failed", _token("cc"): "partial"}

    again: list[str] = []

    def load_again(token: str, _cancel):
        again.append(token)
        return SimpleNamespace(symbol="T", pools=1, swaps=1, transfers=1, seconds=0.2)

    second = scrape.run(tokens, load=load_again)
    assert _token("aa") not in again
    assert set(again) == {_token("bb"), _token("cc")}
    assert second["skipped"] == 1
    assert second["done"] == 2
