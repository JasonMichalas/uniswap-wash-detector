import sys

import duckdb
from oracle import DBS

from washdetect.weakmodel import frames_from_db

for n in sys.argv[1:]:
    tok = (
        duckdb.connect(DBS[n], read_only=True)
        .execute("select token from swaps limit 1")
        .fetchone()[0]
    )
    fr = frames_from_db(n, DBS[n], tok, cached_only=True)
    fr.trades.to_pickle(f"tr_{n}.pkl")
    print(n, len(fr.trades), flush=True)
