import { shortAddress } from "../editor/ui";
import type { LoadResult } from "../types";

export function LoadStatus({
  typedAddressProblem,
  customProblem,
  showCustomProblem,
  loadError,
  loadErrorDetail,
  loading,
  progress,
  stalled,
  onRetry,
  onCancel,
  loaded,
  ran,
}: {
  typedAddressProblem: string | null;
  customProblem: string | null;
  showCustomProblem: boolean;
  loadError: string | null;
  loadErrorDetail: string | null;
  loading: boolean;
  progress: string;
  stalled: boolean;
  onRetry: () => void;
  onCancel: () => void;
  loaded: LoadResult | null;
  ran: boolean;
}) {
  return (
    <>
      {(loadError || customProblem || typedAddressProblem || loading) && (
        <div className="notices">
          {typedAddressProblem && <p className="banner error">{typedAddressProblem}</p>}
          {showCustomProblem && customProblem && <p className="banner error">{customProblem}</p>}
          {loadError && (
            <p className="banner error">
              {loadError}
              {loadErrorDetail && (
                <details className="error-details">
                  <summary>Details</summary>
                  <p>{loadErrorDetail}</p>
                </details>
              )}
            </p>
          )}
          {loading && <p className="banner load-progress">{progress || "Starting"}</p>}
          {loading && stalled && (
            <p className="banner warn stalled">
              No progress for a minute. The free data servers may be slow. Retry keeps everything already saved.
              <button type="button" onClick={onRetry}>
                Retry
              </button>
              <button type="button" className="text cancel" onClick={onCancel}>
                Cancel
              </button>
            </p>
          )}
        </div>
      )}

      {loaded && (
        <section className="inventory">
          <strong>
            {loaded.symbol} · {shortAddress(loaded.token)}
          </strong>
          <span>
            {loaded.pools} pools ({loaded.v2_pools} v2, {loaded.v3_pools} v3, {loaded.v4_pools} v4)
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
            title="Blocks read from the chain this load, summed across swap and transfer requests. This is not a log count."
          >
            blocks scanned {loaded.blocks_fetched.toLocaleString()} · cached {loaded.blocks_cached.toLocaleString()}
            {loaded.swap_logs > 0 ? ` · ${loaded.swap_logs.toLocaleString()} swap logs` : ""}
          </span>
          {loaded.notes.slice(0, 3).map((note) => (
            <p key={note} className={loaded.swaps === 0 ? "banner warn" : "quiet"}>
              {note}
              {note.startsWith("One free data server") && loaded.note_details && loaded.note_details.length > 0 && (
                <details className="error-details">
                  <summary>Details</summary>
                  <ul>
                    {loaded.note_details.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </details>
              )}
            </p>
          ))}
          {loaded.balances_truncated && <span className="quiet">Balances capped at the most active wallets.</span>}
        </section>
      )}
    </>
  );
}
