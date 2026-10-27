import type { RefObject } from "react";

export function GraphActions({
  status,
  graphName,
  boxCount,
  edgeCount,
  runReady,
  running,
  fileRef,
  onSave,
  onClear,
  onRun,
  onFile,
}: {
  status: string;
  graphName: string;
  boxCount: number;
  edgeCount: number;
  runReady: boolean;
  running: boolean;
  fileRef: RefObject<HTMLInputElement>;
  onSave: () => void;
  onClear: () => void;
  onRun: () => void;
  onFile: (file: File) => void;
}) {
  return (
    <div className="action-bar">
      <p className="action-status">{status}</p>
      <div className="actions">
        <strong className="graph-title">{graphName === "untitled" ? "New question" : graphName}</strong>
        <button type="button" onClick={onSave} disabled={boxCount === 0}>
          Save JSON
        </button>
        <button type="button" onClick={() => fileRef.current?.click()}>
          Open JSON
        </button>
        <button type="button" onClick={onClear} disabled={boxCount === 0 && edgeCount === 0}>
          Clear
        </button>
        <button type="button" className="primary" disabled={!runReady} onClick={onRun}>
          {running ? "Running…" : "Run"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) onFile(file);
          }}
        />
      </div>
    </div>
  );
}
