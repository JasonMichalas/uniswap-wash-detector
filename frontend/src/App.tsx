import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type OnConnect,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  compileGraph,
  fetchCatalog,
  fetchJob,
  fetchTemplate,
  fetchTemplates,
  runGraph,
  startLoad,
} from "./api";
import { BoxNodeView } from "./components/BoxNode";
import { CatalogProvider } from "./components/catalog";
import { Inspector } from "./components/Inspector";
import { Results } from "./components/Results";
import {
  connectionAllowed,
  fromApiGraph,
  isAddress,
  logicKey,
  rangeProblem,
  toApiGraph,
  type BoxNode,
} from "./flow";
import type { Catalog, GraphError, LoadResult, RunResult, TemplateSummary } from "./types";

const nodeTypes = { box: BoxNodeView };
const PRESETS = [7, 30, 60, 90];
const CATEGORIES: [string, string][] = [
  ["source", "Sources"],
  ["filter", "Filters"],
  ["detector", "Detectors"],
  ["aggregate", "Aggregates"],
  ["arrange", "Arrange"],
  ["output", "Outputs"],
];

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  useEffect(() => {
    fetchCatalog()
      .then(setCatalog)
      .catch((error: unknown) => {
        setBootError(error instanceof Error ? error.message : "The API did not respond.");
      });
  }, []);

  if (bootError) {
    return (
      <main className="boot">
        <h1>washdetect</h1>
        <p>The editor could not read the box catalog.</p>
        <p className="banner error">{bootError}</p>
        <p>Start the API from the repo root, then refresh:</p>
        <pre>washdetect serve</pre>
      </main>
    );
  }
  if (!catalog) {
    return (
      <main className="boot">
        <h1>washdetect</h1>
        <p>Loading the box catalog…</p>
      </main>
    );
  }
  return (
    <CatalogProvider catalog={catalog}>
      <ReactFlowProvider>
        <Editor catalog={catalog} />
      </ReactFlowProvider>
    </CatalogProvider>
  );
}

function Editor({ catalog }: { catalog: Catalog }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<BoxNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [graphName, setGraphName] = useState("untitled");
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [address, setAddress] = useState("");
  const [preset, setPreset] = useState<number | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<LoadResult | null>(null);
  const [query, setQuery] = useState("");
  const [sql, setSql] = useState("");
  const [errors, setErrors] = useState<GraphError[]>([]);
  const [checking, setChecking] = useState(false);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [tab, setTab] = useState<"query" | "sql" | "result">("query");
  const [banner, setBanner] = useState<string | null>(null);
  const idRef = useRef(1);
  const fileRef = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  nodesRef.current = nodes;
  edgesRef.current = edges;
  const { screenToFlowPosition, fitView, setCenter } = useReactFlow();

  const key = useMemo(() => logicKey(nodes, edges), [nodes, edges]);
  const latestKey = useRef(key);
  latestKey.current = key;
  const seenKey = useRef<string | null>(null);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    fetchTemplates()
      .then(setTemplates)
      .catch(() => setTemplates([]));
  }, []);

  useEffect(() => {
    if (seenKey.current === null) {
      seenKey.current = key;
      return;
    }
    if (seenKey.current === key) return;
    seenKey.current = key;
    setRun(null);
    setRunError(null);
    setSql("");
    setTab((current) => (current === "result" ? "query" : current));
  }, [key]);

  useEffect(() => {
    if (nodes.length === 0) {
      setQuery("");
      setErrors([]);
      setChecking(false);
      return;
    }
    const snapshot = key;
    const handle = window.setTimeout(() => {
      setChecking(true);
      compileGraph(toApiGraph(nodesRef.current, edgesRef.current, graphName))
        .then((body) => {
          if (!alive.current || latestKey.current !== snapshot) return;
          setQuery(body.query);
          setErrors(body.errors);
        })
        .catch((error: unknown) => {
          if (!alive.current || latestKey.current !== snapshot) return;
          setQuery("");
          setErrors([
            {
              message: error instanceof Error ? error.message : "Compile failed.",
              node_id: null,
              edge_id: null,
              code: "network",
            },
          ]);
        })
        .finally(() => {
          if (alive.current && latestKey.current === snapshot) setChecking(false);
        });
    }, 280);
    return () => window.clearTimeout(handle);
  }, [key, graphName]);

  const addBox = useCallback(
    (nodeType: string, params: Record<string, unknown>, label: string, position: { x: number; y: number }) => {
      const id = `b${idRef.current++}`;
      const next: BoxNode = {
        id,
        type: "box",
        position,
        data: { nodeType, params: { ...params }, label },
      };
      setNodes((current) => [...current, next]);
    },
    [setNodes],
  );

  const onConnect: OnConnect = useCallback(
    (connection: Connection) => {
      setEdges((current) => addEdge({ ...connection, id: `e${idRef.current++}` }, current));
    },
    [setEdges],
  );

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => connectionAllowed(connection, nodes, edges, catalog),
    [nodes, edges, catalog],
  );

  const shownNodes = nodes.map((node) => ({
    ...node,
    className: errors.some((error) => error.node_id === node.id) ? "errored" : "",
  }));
  const shownEdges = edges.map((edge) => ({
    ...edge,
    className: errors.some((error) => error.edge_id === edge.id) ? "bad-edge" : "",
  }));

  const addressOk = isAddress(address);
  const customProblem = preset === null && (startDate || endDate) ? rangeProblem(startDate, endDate) : null;
  const windowReady = preset !== null || (preset === null && startDate !== "" && endDate !== "" && customProblem === null);
  const canLoad = addressOk && windowReady && !loading;

  async function onLoad() {
    setLoadError(null);
    setBanner(null);
    setLoading(true);
    setProgress("Starting");
    setRun(null);
    setRunError(null);
    setSql("");
    setLoaded(null);
    try {
      const token = address.trim().toLowerCase();
      const body =
        preset !== null ? { token, days: preset } : { token, start: startDate, end: endDate };
      const { job_id: jobId } = await startLoad(body);
      for (;;) {
        const job = await fetchJob(jobId);
        if (!alive.current) return;
        setProgress(job.message);
        if (job.status === "done" && job.result) {
          setLoaded(job.result);
          setLoading(false);
          return;
        }
        if (job.status === "error") {
          setLoadError(job.error || job.message || "The load failed.");
          setLoading(false);
          return;
        }
        await sleep(700);
      }
    } catch (error) {
      if (!alive.current) return;
      setLoadError(error instanceof Error ? error.message : "The load failed.");
      setLoading(false);
    }
  }

  async function onRun() {
    if (!loaded) return;
    setRunning(true);
    setRunError(null);
    try {
      const result = await runGraph({
        token: loaded.token,
        start_block: loaded.start_block,
        end_block: loaded.end_block,
        start_time: loaded.start_time,
        end_time: loaded.end_time,
        graph: toApiGraph(nodes, edges, graphName),
      });
      setRun(result);
      setSql(result.sql);
      setQuery(result.query);
      setTab("result");
    } catch (error) {
      setRun(null);
      setRunError(error instanceof ApiError ? error.message : "The run failed.");
      setTab("result");
    } finally {
      setRunning(false);
    }
  }

  function onSave() {
    const graph = toApiGraph(nodes, edges, graphName || "untitled");
    const blob = new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${slug(graphName || "washdetect-graph")}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function onFile(file: File) {
    file
      .text()
      .then((text) => {
        const parsed = JSON.parse(text) as unknown;
        if (!parsed || typeof parsed !== "object" || !Array.isArray((parsed as { nodes?: unknown }).nodes)) {
          throw new Error("That file is not a washdetect graph.");
        }
        placeGraph(parsed as Parameters<typeof fromApiGraph>[0], (parsed as { name?: string }).name);
      })
      .catch((error: unknown) => {
        setBanner(error instanceof Error ? error.message : "Could not read that file.");
      });
  }

  function placeGraph(graph: Parameters<typeof fromApiGraph>[0], name?: string) {
    const placed = fromApiGraph(graph, catalog);
    const maxId = placed.nodes.reduce((max, node) => Math.max(max, numericSuffix(node.id)), 0);
    idRef.current = Math.max(idRef.current, maxId + 1);
    setNodes(placed.nodes);
    setEdges(placed.edges);
    setGraphName(name || graph.name || "untitled");
    setBanner(null);
    window.setTimeout(() => fitView({ padding: 0.2, duration: 200 }), 30);
  }

  async function openTemplate(id: string) {
    try {
      const graph = await fetchTemplate(id);
      placeGraph(graph, graph.name);
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Could not open that example.");
    }
  }

  function updateParams(id: string, params: Record<string, unknown>, label?: string) {
    setNodes((current) =>
      current.map((node) =>
        node.id === id
          ? { ...node, data: { ...node.data, params, label: label || node.data.label } }
          : node,
      ),
    );
  }

  function focusNode(id: string) {
    setNodes((current) => current.map((node) => ({ ...node, selected: node.id === id })));
    const node = nodes.find((item) => item.id === id);
    if (node) setCenter(node.position.x + 110, node.position.y + 50, { zoom: 1.1, duration: 200 });
  }

  const selected = nodes.find((node) => node.selected) ?? null;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>washdetect</h1>
          <p>Uniswap v2 and v3, Ethereum mainnet. Build the question. Nothing is scored until you press Run.</p>
        </div>
        <form
          className="load"
          onSubmit={(event) => {
            event.preventDefault();
            if (canLoad) void onLoad();
          }}
        >
          <label className="address">
            Token
            <input
              value={address}
              spellCheck={false}
              placeholder="0x… contract address"
              onChange={(event) => setAddress(event.target.value)}
            />
          </label>
          <fieldset>
            <legend>History window</legend>
            <div className="presets">
              {PRESETS.map((days) => (
                <button
                  key={days}
                  type="button"
                  aria-pressed={preset === days}
                  onClick={() => setPreset(days)}
                >
                  {days}d
                </button>
              ))}
            </div>
            <div className={`custom ${preset === null && (startDate || endDate) ? "active" : ""}`}>
              <label>
                Start
                <input
                  type="date"
                  value={startDate}
                  onChange={(event) => {
                    setPreset(null);
                    setStartDate(event.target.value);
                  }}
                />
              </label>
              <label>
                End
                <input
                  type="date"
                  value={endDate}
                  onChange={(event) => {
                    setPreset(null);
                    setEndDate(event.target.value);
                  }}
                />
              </label>
            </div>
          </fieldset>
          <button type="submit" className="primary" disabled={!canLoad}>
            {loading ? "Loading…" : "Load token"}
          </button>
        </form>
      </header>

      {(loadError || customProblem || (address && !addressOk) || progress) && (
        <div className="notices">
          {address && !addressOk && <p className="banner error">Enter a 20-byte token address, 0x plus 40 hex characters.</p>}
          {customProblem && preset === null && <p className="banner error">{customProblem}</p>}
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
          <span className="quiet">
            {run ? "Last result came from Run." : "No analysis has been run."}
          </span>
          <span className="quiet" title="Already-covered spans, summed across each pool and the transfer log. Extending the window fetches only the missing blocks.">
            fetched {loaded.blocks_fetched.toLocaleString()} · cached {loaded.blocks_cached.toLocaleString()}
          </span>
          {loaded.balances_truncated && <span className="quiet">Balances capped at the most active wallets.</span>}
        </section>
      )}

      <div className="workspace">
        <aside className="palette">
          <div className="palette-head">
            <h2>Boxes</h2>
            <details>
              <summary>Examples</summary>
              <ul>
                {templates.map((item) => (
                  <li key={item.id}>
                    <button type="button" onClick={() => void openTemplate(item.id)}>
                      <strong>{item.name}</strong>
                      <span>{item.description}</span>
                    </button>
                  </li>
                ))}
                {templates.length === 0 && <li className="muted">No examples found.</li>}
              </ul>
            </details>
          </div>
          {CATEGORIES.map(([category, title]) => (
            <section key={category}>
              <h3>{title}</h3>
              {catalog.palette
                .filter((item) => item.category === category)
                .map((item) => (
                  <button
                    key={`${item.node_type}-${item.label}`}
                    type="button"
                    className={`chip cat-${category}`}
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData("application/washdetect", JSON.stringify(item));
                      event.dataTransfer.effectAllowed = "move";
                    }}
                    onClick={() =>
                      addBox(item.node_type, item.data, item.label, {
                        x: 72 + (nodes.length % 4) * 40,
                        y: 48 + nodes.length * 24,
                      })
                    }
                  >
                    <strong>{item.label}</strong>
                    <span>{item.blurb}</span>
                  </button>
                ))}
            </section>
          ))}
        </aside>

        <div
          className="canvas-wrap"
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onDrop={(event) => {
            event.preventDefault();
            const raw = event.dataTransfer.getData("application/washdetect");
            if (!raw) return;
            const item = JSON.parse(raw) as { node_type: string; label: string; data: Record<string, unknown> };
            addBox(item.node_type, item.data, item.label, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
          }}
        >
          {nodes.length === 0 && (
            <div className="empty-canvas">
              <p>The canvas is empty on purpose.</p>
              <p>Loading a token only caches its pools, swaps, and transfers. Add boxes, wire them, then press Run.</p>
            </div>
          )}
          <ReactFlow
            nodes={shownNodes}
            edges={shownEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            nodeTypes={nodeTypes}
            deleteKeyCode={["Backspace", "Delete"]}
            proOptions={{ hideAttribution: false }}
            fitView
            colorMode="dark"
          >
            <Background gap={22} color="#2a2e24" />
            <Controls />
            <MiniMap pannable zoomable maskColor="rgba(12, 14, 10, 0.7)" />
          </ReactFlow>
          <div className="canvas-actions">
            <button type="button" onClick={onSave} disabled={nodes.length === 0}>
              Save JSON
            </button>
            <button type="button" onClick={() => fileRef.current?.click()}>
              Open JSON
            </button>
            <button
              type="button"
              onClick={() => {
                setNodes([]);
                setEdges([]);
                setGraphName("untitled");
              }}
              disabled={nodes.length === 0 && edges.length === 0}
            >
              Clear
            </button>
            <button type="button" className="primary" disabled={!loaded || running || nodes.length === 0 || errors.length > 0} onClick={() => void onRun()}>
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

        <Inspector
          catalog={catalog}
          node={selected}
          nodes={nodes}
          edges={edges}
          onChange={updateParams}
          onDelete={(id) => {
            setNodes((current) => current.filter((node) => node.id !== id));
            setEdges((current) => current.filter((edge) => edge.source !== id && edge.target !== id));
          }}
        />
      </div>

      {banner && <p className="banner error slim">{banner}</p>}
      {loaded === null && !loading && (
        <p className="hint bar">Pick a window, load a token, then build a graph. Run stays off until both exist.</p>
      )}

      <Results
        query={query}
        sql={sql}
        run={run}
        errors={errors}
        runError={runError}
        checking={checking}
        tab={tab}
        onTab={setTab}
        onFocusNode={focusNode}
      />
    </div>
  );
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function shortAddress(value: string): string {
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "washdetect-graph";
}

function numericSuffix(id: string): number {
  const match = /(\d+)$/.exec(id);
  return match ? Number(match[1]) : 0;
}
