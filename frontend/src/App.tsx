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
import { TokenPicker } from "./components/TokenPicker";
import {
  connectionAllowed,
  fromApiGraph,
  isAddress,
  logicKey,
  rangeProblem,
  toApiGraph,
  type BoxNode,
} from "./flow";
import type { Catalog, GraphError, LoadResult, PaletteItem, RunResult, TemplateSummary, TokenSuggestion } from "./types";

const nodeTypes = { box: BoxNodeView };
const PRESETS = [7, 30, 60, 90];
const GROUPS: { id: string; title: string; categories: string[] }[] = [
  { id: "data", title: "Data", categories: ["source"] },
  { id: "filter", title: "Filter", categories: ["filter"] },
  { id: "detect", title: "Detect", categories: ["detector"] },
  { id: "combine", title: "Combine", categories: ["aggregate", "arrange"] },
  { id: "show", title: "Show", categories: ["output"] },
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
        <h1>Uniswap Wash Detector</h1>
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
        <h1>Uniswap Wash Detector</h1>
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
  const [tokenQuery, setTokenQuery] = useState("");
  const [address, setAddress] = useState("");
  const [preset, setPreset] = useState<number | null>(7);
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
  const [theme, setTheme] = useState<"light" | "dark">(
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  const [examplesAt, setExamplesAt] = useState<"toolbar" | "canvas" | null>(null);
  const [panelHeight, setPanelHeight] = useState(180);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const idRef = useRef(1);
  const fileRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
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
  const typedAddressProblem = addressProblem(tokenQuery);
  const customProblem = preset === null && (startDate || endDate) ? rangeProblem(startDate, endDate) : null;
  const windowReady = preset !== null || (preset === null && startDate !== "" && endDate !== "" && customProblem === null);
  const canLoad = addressOk && windowReady && !loading;
  const step: 1 | 2 | 3 = !addressOk ? 1 : loaded ? 3 : 2;

  function clickPosition(): { x: number; y: number } {
    const rect = wrapRef.current?.getBoundingClientRect();
    const count = nodesRef.current.length;
    if (!rect) return { x: 80 + count * 24, y: 80 };
    return screenToFlowPosition({
      x: rect.left + Math.min(rect.width * 0.35, 280) + (count % 3) * 28,
      y: rect.top + 72 + count * 18,
    });
  }

  function placeChip(item: PaletteItem) {
    addBox(item.node_type, item.data, item.label, clickPosition());
  }

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
      const body = preset !== null ? { token, days: preset } : { token, start: startDate, end: endDate };
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
      setPanelCollapsed(false);
    } catch (error) {
      setRun(null);
      setRunError(error instanceof ApiError ? error.message : "The run failed.");
      setTab("result");
      setPanelCollapsed(false);
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
    setExamplesAt(null);
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
        node.id === id ? { ...node, data: { ...node.data, params, label: label || node.data.label } } : node,
      ),
    );
  }

  function focusNode(id: string) {
    setNodes((current) => current.map((node) => ({ ...node, selected: node.id === id })));
    const node = nodes.find((item) => item.id === id);
    if (node) setCenter(node.position.x + 110, node.position.y + 50, { zoom: 1.1, duration: 200 });
  }

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("washdetect-theme", next);
    } catch {
      /* private mode */
    }
  }

  function onPickToken(token: TokenSuggestion, label: string) {
    setTokenQuery(label);
    setAddress(token.address);
  }

  const selected = nodes.find((node) => node.selected) ?? null;
  const runReady = Boolean(loaded) && !running && nodes.length > 0 && errors.length === 0;
  const hint = stepHint(step, loading, preset, nodes.length, errors.length);

  return (
    <div className="app">
      <header className="top">
        <div className="brand-row">
          <div className="brand">
            <h1>Uniswap Wash Detector</h1>
            <p>Uniswap on Ethereum. You build the question, then press Run.</p>
          </div>
          <button type="button" className="text theme" aria-pressed={theme === "dark"} onClick={toggleTheme}>
            {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
        </div>
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
        <p className="step-hint">{hint}</p>
        <form
          className="load"
          onSubmit={(event) => {
            event.preventDefault();
            if (canLoad) void onLoad();
          }}
        >
          <label className="token-field">
            Token
            <TokenPicker query={tokenQuery} onQuery={(value) => {
              setTokenQuery(value);
              setAddress(isAddress(value) ? value.trim().toLowerCase() : "");
            }} onPick={onPickToken} />
            {addressOk && !isAddress(tokenQuery) && <span className="token-chosen">Using {shortAddress(address)}</span>}
          </label>
          <fieldset>
            <legend>Time window</legend>
            <div className="presets">
              {PRESETS.map((days) => (
                <button key={days} type="button" aria-pressed={preset === days} onClick={() => setPreset(days)}>
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

      {(loadError || customProblem || typedAddressProblem || loading) && (
        <div className="notices">
          {typedAddressProblem && <p className="banner error">{typedAddressProblem}</p>}
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
          <span className="quiet">{run ? "Last result came from Run." : "No analysis has been run."}</span>
          <span
            className="quiet"
            title="Already-covered spans, summed across each pool and the transfer log. Extending the window fetches only the missing blocks."
          >
            fetched {loaded.blocks_fetched.toLocaleString()} · cached {loaded.blocks_cached.toLocaleString()}
          </span>
          {loaded.balances_truncated && <span className="quiet">Balances capped at the most active wallets.</span>}
        </section>
      )}

      <div className="toolbar">
        {GROUPS.map((group) => (
          <div className="tool-group" key={group.id}>
            <span className={`tool-label cat-${group.id}`}>
              <CategoryIcon name={group.id} />
              {group.title}
            </span>
            <div className="tool-chips">
              {catalog.palette
                .filter((item) => group.categories.includes(item.category))
                .map((item) => (
                  <button
                    key={`${item.node_type}-${item.label}`}
                    type="button"
                    className={`chip cat-${group.id}`}
                    title={item.blurb}
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData("application/washdetect", JSON.stringify(item));
                      event.dataTransfer.effectAllowed = "move";
                    }}
                    onClick={() => placeChip(item)}
                  >
                    {item.label}
                  </button>
                ))}
            </div>
          </div>
        ))}
        <ExampleMenu
          templates={templates}
          open={examplesAt === "toolbar"}
          onToggle={() => setExamplesAt((current) => (current === "toolbar" ? null : "toolbar"))}
          onOpen={(id) => void openTemplate(id)}
        />
      </div>

      <div className="action-bar">
        <p className="action-status">{actionStatus(loaded, nodes.length, errors.length, running)}</p>
        <div className="actions">
          <strong className="graph-title">{graphName === "untitled" ? "New question" : graphName}</strong>
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
          <button type="button" className="primary" disabled={!runReady} onClick={() => void onRun()}>
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

      <div className="workspace">
        <div
          className="canvas-wrap"
          ref={wrapRef}
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
              <p>Drag a Data box here to start</p>
              <ExampleMenu
                templates={templates}
                open={examplesAt === "canvas"}
                label="Or open an example"
                onToggle={() => setExamplesAt((current) => (current === "canvas" ? null : "canvas"))}
                onOpen={(id) => void openTemplate(id)}
              />
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
            colorMode={theme}
          >
            <Background gap={22} color={theme === "dark" ? "#3a372f" : "#e4ddd0"} />
            <Controls />
            <MiniMap
              pannable
              zoomable
              style={{ width: 120, height: 80 }}
              maskColor={theme === "dark" ? "rgba(28, 27, 25, 0.72)" : "rgba(246, 244, 239, 0.78)"}
            />
          </ReactFlow>
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
        height={panelHeight}
        collapsed={panelCollapsed}
        onCollapsed={setPanelCollapsed}
        onHeight={setPanelHeight}
      />
    </div>
  );
}

function ExampleMenu({
  templates,
  open,
  onToggle,
  onOpen,
  label = "Examples",
}: {
  templates: TemplateSummary[];
  open: boolean;
  onToggle: () => void;
  onOpen: (id: string) => void;
  label?: string;
}) {
  return (
    <div className="examples">
      <button type="button" aria-expanded={open} onClick={onToggle}>
        {label}
      </button>
      {open && (
        <ul>
          {templates.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => onOpen(item.id)}>
                <strong>{item.name}</strong>
                <span>{item.description}</span>
              </button>
            </li>
          ))}
          {templates.length === 0 && <li className="muted">No examples found.</li>}
        </ul>
      )}
    </div>
  );
}

function CategoryIcon({ name }: { name: string }) {
  const common = {
    width: 14,
    height: 14,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    "aria-hidden": true as const,
  };
  if (name === "data") {
    return (
      <svg {...common}>
        <path d="M2.5 4.5v7c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5v-7" />
        <path d="M2.5 4.5c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5-2.5-2.5-5.5-2.5-5.5 1.1-5.5 2.5z" />
        <path d="M2.5 8c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5" />
      </svg>
    );
  }
  if (name === "filter") {
    return (
      <svg {...common}>
        <path d="M2 2.5h12l-4.2 5.2V13l-3.6 1.5V7.7L2 2.5z" />
      </svg>
    );
  }
  if (name === "detect") {
    return (
      <svg {...common}>
        <circle cx="7" cy="7" r="4.2" />
        <path d="M10.2 10.2L14 14" />
      </svg>
    );
  }
  if (name === "combine") {
    return (
      <svg {...common}>
        <rect x="2" y="2" width="8" height="8" rx="1.2" />
        <rect x="6" y="6" width="8" height="8" rx="1.2" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M2.5 13.5V8M8 13.5V3M13.5 13.5V6" />
    </svg>
  );
}

function stepHint(step: 1 | 2 | 3, loading: boolean, preset: number | null, boxes: number, problems: number): string {
  if (loading) return "Fetching this token's swaps and transfers. Nothing is calculated yet.";
  if (step === 1) return "Search by name, or pick a token from the list. You do not need a contract address.";
  if (step === 2) {
    return preset === 7
      ? "Seven days is already selected. Press Load token. Nothing is calculated yet."
      : "Press Load token. Nothing is calculated yet.";
  }
  if (boxes === 0) return "Drag a Data box onto the canvas, connect the boxes, then press Run.";
  if (problems > 0) return "Fix the highlighted box, then press Run.";
  return "Connect the boxes, then press Run.";
}

function actionStatus(loaded: LoadResult | null, boxes: number, problems: number, running: boolean): string {
  if (running) return "Running your question.";
  if (!loaded) return "Run stays off until a token is loaded.";
  if (boxes === 0) return "Add a box, then press Run.";
  if (problems > 0) return "Run stays off while a box has a problem.";
  return "Ready to run.";
}

function addressProblem(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed.toLowerCase().startsWith("0x")) return null;
  const body = trimmed.slice(2);
  if (body.length === 0) return null;
  const hex = /^[0-9a-fA-F]+$/.test(body);
  if (!hex || body.length > 40) {
    return "That address needs 0x and 40 hex characters. Or pick a token from the list.";
  }
  return null;
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
