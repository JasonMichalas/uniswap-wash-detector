import { shortAddress } from "../editor/ui";
import type { LoadResult } from "../types";

export function LoadStatus({
  typedAddressProblem,
  customProblem,
  showCustomProblem,
  loadError,
  loading,
  progress,
  loaded,
  ran,
}: {
  typedAddressProblem: string | null;
  customProblem: string | null;
  showCustomProblem: boolean;
  loadError: string | null;
  loading: boolean;
  progress: string;
  loaded: LoadResult | null;
  ran: boolean;
}) {
  return (
    <>
      {(loadError || customProblem || typedAddressProblem || loading) && (
        <div className="notices">
          {typedAddressProblem && <p className="banner error">{typedAddressProblem}</p>}
          {showCustomProblem && customProblem && <p className="banner error">{customProblem}</p>}
          {loadError && <p className="banner error">{loadError}</p>}
          {loading && <p className="banner">{progress}</p>}
        </div>
      )}

      {loaded && (
        <section className="inventory">
          <strong>
            {loaded.symbol} · {shortAddress(loaded.token)}
          </strong>
          <span>
            {loaded.pools} pools ({loaded.v2_pools} v2, {loaded.v3_pools} v3)
          </span>
          <span>{loaded.swaps.toLocaleString()} swaps</span>
          <span>{loaded.transfers.toLocaleString()} transfers</span>
          <span>
            blocks {loaded.start_block.toLocaleString()}–{loaded.end_block.toLocaleString()}
          </span>
          <span>{loaded.seconds}s</span>
          {loaded.eth_usd !== null && <span>ETH ${loaded.eth_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>}
          <span className="quiet">{ran ? "Last result came from Run." : "No analysis has been run."}</span>
          <span
            className="quiet"
            title="Already-covered spans, summed across each pool and the transfer log. Extending the window fetches only the missing blocks."
          >
            fetched {loaded.blocks_fetched.toLocaleString()} · cached {loaded.blocks_cached.toLocaleString()}
          </span>
          {loaded.balances_truncated && <span className="quiet">Balances capped at the most active wallets.</span>}
        </section>
      )}
    </>
  );
}
