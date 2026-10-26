import { useEffect, useRef, useState } from "react";
import { fetchSuggestions } from "../api";
import type { TokenSuggestion, TokenSuggestions } from "../types";

export function TokenPicker({
  query,
  onQuery,
  onPick,
}: {
  query: string;
  onQuery: (value: string) => void;
  onPick: (token: TokenSuggestion, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [base, setBase] = useState<TokenSuggestions | null>(null);
  const [matches, setMatches] = useState<TokenSuggestion[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const picked = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSuggestions()
      .then((body) => {
        if (!cancelled) setBase(body);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load suggestions. Paste a contract address.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const text = query.trim();
    if (!text || text === picked.current) {
      setMatches(null);
      setBusy(false);
      return;
    }
    let cancelled = false;
    setBusy(true);
    const handle = window.setTimeout(() => {
      fetchSuggestions(text)
        .then((body) => {
          if (cancelled) return;
          setMatches(body.results ?? []);
          setError(null);
        })
        .catch(() => {
          if (!cancelled) setError("Could not search just now. Try the list, or paste an address.");
        })
        .finally(() => {
          if (!cancelled) setBusy(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [query]);

  useEffect(() => {
    if (!open) return;
    function onDoc(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function choose(token: TokenSuggestion) {
    const label = token.symbol ? `${token.name} (${token.symbol})` : token.name;
    picked.current = label;
    onPick(token, label);
    setMatches(null);
    setOpen(false);
  }

  const searching = query.trim() !== "" && query !== picked.current;
  const showMatches = searching && matches !== null;

  return (
    <div className="token-combo" ref={rootRef}>
      <input
        value={query}
        spellCheck={false}
        placeholder="Search by name, or paste an address"
        role="combobox"
        aria-expanded={open}
        aria-controls="token-suggestions"
        aria-autocomplete="list"
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          picked.current = null;
          onQuery(event.target.value);
          setOpen(true);
        }}
      />
      {open && (
        <div className="token-menu" id="token-suggestions" role="listbox">
          {error && <p className="menu-note">{error}</p>}
          {!base && !error && <p className="menu-note">Loading tokens…</p>}
          {base?.source === "curated" && !showMatches && (
            <p className="menu-note">Saved list of well-known tokens. The live list did not load.</p>
          )}
          {showMatches ? (
            <TokenSection title="Matches" tokens={matches} onPick={choose} empty="No tokens match that. Paste a contract address if you have one." />
          ) : (
            <>
              {busy && <p className="menu-note">Searching…</p>}
              <TokenSection title="Trending" tokens={base?.trending ?? []} onPick={choose} />
              <TokenSection title="Top volume (24h)" tokens={base?.top_volume ?? []} onPick={choose} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function TokenSection({
  title,
  tokens,
  onPick,
  empty,
}: {
  title: string;
  tokens: TokenSuggestion[];
  onPick: (token: TokenSuggestion) => void;
  empty?: string;
}) {
  return (
    <section>
      <h3>{title}</h3>
      {tokens.length === 0 && empty && <p className="menu-note">{empty}</p>}
      {tokens.map((token) => (
        <button key={`${title}-${token.address}`} type="button" className="token-row" role="option" onClick={() => onPick(token)}>
          <Avatar token={token} />
          <span className="token-name">
            <strong>{token.name}</strong>
            {token.symbol && <span>{token.symbol}</span>}
          </span>
          <span className="token-volume">{formatVolume(token.volume_24h_usd)}</span>
        </button>
      ))}
    </section>
  );
}

function Avatar({ token }: { token: TokenSuggestion }) {
  const [failed, setFailed] = useState(false);
  const letter = (token.symbol || token.name || "?").slice(0, 1).toUpperCase();
  if (!token.logo_url || failed) {
    return (
      <span className="avatar" aria-hidden>
        {letter}
      </span>
    );
  }
  return <img className="avatar" alt="" src={token.logo_url} onError={() => setFailed(true)} />;
}

function formatVolume(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value <= 0) return "";
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${Math.round(value / 1_000)}K`;
  return `$${Math.round(value)}`;
}
