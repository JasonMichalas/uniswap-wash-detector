import { PRESETS, shortAddress } from "../editor/ui";

function loadLabel(progress: string): string {
  const match = progress.match(/\((\d+)%\)/);
  if (match) return `Loading ${match[1]}%`;
  if (!progress) return "Loading…";
  return progress.length > 48 ? `${progress.slice(0, 48)}…` : progress;
}
import type { TokenSuggestion } from "../types";
import { TokenPicker } from "./TokenPicker";

export function TokenWindow({
  step,
  hint,
  theme,
  onToggleTheme,
  tokenQuery,
  onQuery,
  onPick,
  showChosen,
  address,
  preset,
  onPreset,
  startDate,
  endDate,
  onStart,
  onEnd,
  canLoad,
  loading,
  progress,
  forks,
  onForks,
  onLoad,
  onCancel,
}: {
  step: 1 | 2 | 3;
  hint: string;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  tokenQuery: string;
  onQuery: (value: string) => void;
  onPick: (token: TokenSuggestion, label: string) => void;
  showChosen: boolean;
  address: string;
  preset: number | null;
  onPreset: (days: number) => void;
  startDate: string;
  endDate: string;
  onStart: (value: string) => void;
  onEnd: (value: string) => void;
  canLoad: boolean;
  loading: boolean;
  progress: string;
  forks: boolean;
  onForks: (value: boolean) => void;
  onLoad: () => void;
  onCancel: () => void;
}) {
  return (
    <header className="top">
      <div className="brand-row">
        <h1>Uniswap Wash Detector</h1>
        <ol className="steps">
          <li className={step === 1 ? "current" : "done"}>
            <span className="n">1</span> Choose a token
          </li>
          <li className={step === 2 ? "current" : step > 2 ? "done" : ""}>
            <span className="n">2</span> Choose a time window
          </li>
          <li className={step === 3 ? "current" : ""}>
            <span className="n">3</span> Drag boxes and connect them, then press Run
          </li>
        </ol>
        <button type="button" className="text theme" aria-pressed={theme === "dark"} onClick={onToggleTheme}>
          {theme === "dark" ? "Light theme" : "Dark theme"}
        </button>
      </div>
      <p className="step-hint">{hint}</p>
      <form
        className="load"
        onSubmit={(event) => {
          event.preventDefault();
          if (canLoad) onLoad();
        }}
      >
        <label className="token-field">
          Token
          <TokenPicker query={tokenQuery} onQuery={onQuery} onPick={onPick} />
          {showChosen && <span className="token-chosen">Using {shortAddress(address)}</span>}
        </label>
        <fieldset>
          <legend>Time window</legend>
          <div className="presets">
            {PRESETS.map((days) => (
              <button key={days} type="button" aria-pressed={preset === days} onClick={() => onPreset(days)}>
                {days}d
              </button>
            ))}
          </div>
          <div className={`custom ${preset === null && (startDate || endDate) ? "active" : ""}`}>
            <label>
              Start
              <input type="date" value={startDate} onChange={(event) => onStart(event.target.value)} />
            </label>
            <label>
              End
              <input type="date" value={endDate} onChange={(event) => onEnd(event.target.value)} />
            </label>
          </div>
        </fieldset>
        <label className="forks">
          <input type="checkbox" checked={forks} disabled={loading} onChange={(event) => onForks(event.target.checked)} />
          Also SushiSwap and PancakeSwap
        </label>
        <button type="submit" className="primary" disabled={!canLoad} title={loading ? progress : undefined}>
          {loading ? loadLabel(progress) : "Load token"}
        </button>
        {loading && (
          <button type="button" className="text cancel" onClick={onCancel}>
            Cancel
          </button>
        )}
      </form>
    </header>
  );
}
