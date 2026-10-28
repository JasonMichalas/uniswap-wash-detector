import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type OnConnect,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  cancelJob,
  compileGraph,
  fetchJob,
  fetchTemplate,
  fetchTemplates,
  runGraph,
  startLoad,
} from "../api";
import { describeProblem } from "../editor/boxCopy";
import { BOX_STRIDE_X, freePosition } from "../editor/place";
import { actionStatus, addressProblem, numericSuffix, sleep, slug, stepHint } from "../editor/ui";
import {
  connectionAllowed,
  fromApiGraph,
  isAddress,
  logicKey,
  rangeProblem,
  toApiGraph,
  type BoxNode,
} from "../flow";
import type { Catalog, GraphError, LoadResult, PaletteItem, RunResult, TemplateSummary, TokenSuggestion } from "../types";
import { BoxNodeView } from "./BoxNode";
import { ExampleMenu } from "./ExampleMenu";
import { GraphActions } from "./GraphActions";
import { Inspector } from "./Inspector";
import { LoadStatus } from "./LoadStatus";
import { PaletteToolbar } from "./PaletteToolbar";
import { Results, ResultsModal, resultRowCount, resultsWindowLabel } from "./Results";
import { TokenWindow } from "./TokenWindow";

const nodeTypes = { box: BoxNodeView };

export function Editor({ catalog }: { catalog: Catalog }) {
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
  const [forks, setForks] = useState(false);
  const jobRef = useRef<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadErrorDetail, setLoadErrorDetail] = useState<string | null>(null);
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
  const [panelHeight, setPanelHeight] = useState(420);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [resultsOpen, setResultsOpen] = useState(false);
  const idRef = useRef(1);
  const fileRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  nodesRef.current = nodes;
  edgesRef.current = edges;
  const { screenToFlowPosition, fitView, setCenter, getViewport } = useReactFlow();

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
    const preferred = rect
      ? screenToFlowPosition({ x: rect.left + 48, y: rect.top + 64 })
      : { x: 48, y: 48 };
    const zoom = getViewport().zoom || 1;
    const columns = rect ? Math.floor((rect.width - 64) / (BOX_STRIDE_X * zoom)) : 3;
    return freePosition(nodesRef.current, preferred, columns);
  }

  function placeChip(item: PaletteItem) {
    addBox(item.node_type, item.data, item.label, clickPosition());
  }

  async function onLoad() {
    setLoadError(null);
    setLoadErrorDetail(null);
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
      const { job_id: jobId } = await startLoad({ ...body, include_forks: forks });
      jobRef.current = jobId;
      for (;;) {
        const job = await fetchJob(jobId);
        if (!alive.current) return;
        setProgress(job.message);
        if (job.status === "done" && job.result) {
          setLoaded(job.result);
          setLoading(false);
          return;
        }
        if (job.status === "cancelled") {
          setLoadError(null);
          setLoadErrorDetail(null);
          setLoading(false);
          return;
        }
        if (job.status === "error") {
          setLoadError(job.error || "The free data servers could not finish this load.");
          setLoadErrorDetail(typeof job.error_detail === "string" ? job.error_detail : job.message || null);
          setLoading(false);
          return;
        }
        await sleep(700);
      }
    } catch (error) {
      if (!alive.current) return;
      setLoadError("The free data servers could not finish this load.");
      setLoadErrorDetail(error instanceof Error ? error.message : "The load failed.");
      setLoading(false);
    }
  }

  function onCancel() {
    const jobId = jobRef.current;
    if (jobId) void cancelJob(jobId).catch(() => undefined);
    setProgress("Cancelling…");
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
      setResultsOpen(true);
    } catch (error) {
      setRunError(error instanceof ApiError ? error.message : "The run failed.");
      setTab("result");
      setResultsOpen(true);
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
    if (node) setCenter(node.position.x + 140, node.position.y + 70, { zoom: 1.1, duration: 200 });
  }

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("washdetect-theme", next);
    } catch {
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
      <TokenWindow
        step={step}
        hint={hint}
        theme={theme}
        onToggleTheme={toggleTheme}
        tokenQuery={tokenQuery}
        onQuery={(value) => {
          setTokenQuery(value);
          setAddress(isAddress(value) ? value.trim().toLowerCase() : "");
        }}
        onPick={onPickToken}
        showChosen={addressOk && !isAddress(tokenQuery)}
        address={address}
        preset={preset}
        onPreset={setPreset}
        startDate={startDate}
        endDate={endDate}
        onStart={(value) => {
          setPreset(null);
          setStartDate(value);
        }}
        onEnd={(value) => {
          setPreset(null);
          setEndDate(value);
        }}
        canLoad={canLoad}
        loading={loading}
        progress={progress}
        forks={forks}
        onForks={setForks}
        onLoad={() => void onLoad()}
        onCancel={onCancel}
      />

      <LoadStatus
        typedAddressProblem={typedAddressProblem}
        customProblem={customProblem}
        showCustomProblem={preset === null}
        loadError={loadError}
        loadErrorDetail={loadErrorDetail}
        loading={loading}
        progress={progress}
        loaded={loaded}
        ran={run !== null}
      />

      <PaletteToolbar
        catalog={catalog}
        templates={templates}
        examplesOpen={examplesAt === "toolbar"}
        onToggleExamples={() => setExamplesAt((current) => (current === "toolbar" ? null : "toolbar"))}
        onOpenExample={(id) => void openTemplate(id)}
        onPlace={placeChip}
      />

      <GraphActions
        status={actionStatus(loaded, nodes.length, errors.length, running)}
        graphName={graphName}
        boxCount={nodes.length}
        edgeCount={edges.length}
        runReady={runReady}
        running={running}
        fileRef={fileRef}
        onSave={onSave}
        onClear={() => {
          setNodes([]);
          setEdges([]);
          setGraphName("untitled");
        }}
        onRun={() => void onRun()}
        onFile={onFile}
        resultRows={resultRowCount(run)}
        onShowResults={() => setResultsOpen(true)}
      />

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
            const preferred = screenToFlowPosition({ x: event.clientX, y: event.clientY });
            addBox(item.node_type, item.data, item.label, freePosition(nodesRef.current, preferred));
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
        problemText={(error) => {
          const node = nodes.find((item) => item.id === error.node_id);
          return describeProblem(error.message, node?.data.nodeType);
        }}
        runError={runError}
        checking={checking}
        tab={tab}
        onTab={setTab}
        onFocusNode={focusNode}
        height={panelHeight}
        collapsed={panelCollapsed}
        onCollapsed={setPanelCollapsed}
        onHeight={setPanelHeight}
        onOpenResults={() => setResultsOpen(true)}
      />
      <ResultsModal
        open={resultsOpen}
        onClose={() => setResultsOpen(false)}
        run={run}
        runError={runError}
        noSwaps={loaded !== null && loaded.swaps === 0}
        symbol={loaded?.symbol ?? ""}
        windowLabel={loaded ? resultsWindowLabel(loaded.start_time, loaded.end_time) : ""}
      />
    </div>
  );
}
