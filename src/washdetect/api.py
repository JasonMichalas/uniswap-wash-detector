from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from washdetect.config import DEFAULT_DB
from washdetect.graph.catalog import catalog_payload
from washdetect.graph.compile import GraphCompileError, compile_graph
from washdetect.graph.validate import validate_graph
from washdetect.pipeline import load_token
from washdetect.querylang.engine import QueryEngine
from washdetect.querylang.errors import QueryError
from washdetect.rpc import Cancelled
from washdetect.snapshot import window_ready
from washdetect.store import Store
from washdetect.token import TokenData
from washdetect.tokensuggest import suggest_tokens

# Compile only prints the query. Run is the first time a box executes, and the browser never sends SQL.
app = FastAPI(title="Uniswap Wash Detector", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8743",
        "http://localhost:8743",
        "http://127.0.0.1:8742",
        "http://localhost:8742",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: Store | None = None
_jobs: dict[str, dict[str, Any]] = {}
_cancels: dict[str, threading.Event] = {}
_jobs_lock = threading.Lock()


def get_store() -> Store:
    global _store
    if _store is None:
        path = os.environ.get("WASHDETECT_DB", DEFAULT_DB)
        _store = Store(path)
    return _store


class NodeModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str
    position: dict[str, float] | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EdgeModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class GraphModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    nodes: list[NodeModel]
    edges: list[EdgeModel] = Field(default_factory=list)

    def plain(self) -> dict[str, Any]:
        return self.model_dump()


class LoadRequest(BaseModel):
    token: str
    days: int | None = None
    start: str | None = None
    end: str | None = None
    rediscover: bool = False
    include_forks: bool = False


class RunRequest(BaseModel):
    token: str
    start_block: int
    end_block: int
    start_time: str
    end_time: str
    graph: GraphModel


def _templates_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd() / "examples" / "graphs",
        here.parents[2] / "examples" / "graphs",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _dist_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    return catalog_payload()


@app.get("/api/templates")
def templates() -> dict[str, Any]:
    folder = _templates_dir()
    items = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            import json

            body = json.loads(path.read_text(encoding="utf-8"))
            items.append(
                {
                    "id": path.stem,
                    "name": body.get("name") or path.stem,
                    "description": body.get("description") or "",
                }
            )
    return {"templates": items}


@app.get("/api/templates/{template_id}")
def template(template_id: str) -> dict[str, Any]:
    if "/" in template_id or ".." in template_id:
        raise HTTPException(400, "Unknown template.")
    path = _templates_dir() / f"{template_id}.json"
    if not path.is_file():
        raise HTTPException(404, "Unknown template.")
    import json

    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/token/load")
def start_load(body: LoadRequest) -> dict[str, str]:
    job_id = uuid.uuid4().hex[:12]
    stopped = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "message": "Starting", "result": None, "error": None}
        _cancels[job_id] = stopped

    def work() -> None:
        def progress(message: str) -> None:
            with _jobs_lock:
                if job_id in _jobs and not stopped.is_set():
                    _jobs[job_id]["message"] = message

        try:
            report = load_token(
                body.token,
                days=body.days,
                start=body.start,
                end=body.end,
                store=get_store(),
                progress=progress,
                rediscover=body.rediscover,
                include_forks=body.include_forks,
                cancel=stopped.is_set,
            )
            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = report.as_dict()
                _jobs[job_id]["message"] = "Token data is cached. The canvas is empty until you build a graph."
        except Cancelled:
            with _jobs_lock:
                _jobs[job_id]["status"] = "cancelled"
                _jobs[job_id]["message"] = "Load cancelled. Blocks already read stay cached."
        except Exception as exc:
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = str(exc)
                _jobs[job_id]["message"] = str(exc)

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, bool]:
    with _jobs_lock:
        stopped = _cancels.get(job_id)
        found = _jobs.get(job_id)
        if not found:
            raise HTTPException(404, "Unknown job.")
        if stopped is not None:
            stopped.set()
        if found["status"] == "running":
            found["message"] = "Cancelling…"
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        found = _jobs.get(job_id)
        if not found:
            raise HTTPException(404, "Unknown job.")
        return {"id": job_id, **found}


@app.post("/api/graph/validate")
def validate(body: GraphModel) -> dict[str, Any]:
    errors = validate_graph(body.plain())
    return {"ok": not errors, "errors": [item.to_dict() for item in errors]}


@app.post("/api/graph/compile")
def compile_endpoint(body: GraphModel) -> dict[str, Any]:
    try:
        _program, text = compile_graph(body.plain())
    except GraphCompileError as exc:
        return {"ok": False, "errors": [item.to_dict() for item in exc.errors], "query": "", "sql": ""}
    return {"ok": True, "errors": [], "query": text, "sql": ""}


@app.post("/api/graph/run")
def run(body: RunRequest) -> dict[str, Any]:
    try:
        program, text = compile_graph(body.graph.plain())
    except GraphCompileError as exc:
        raise HTTPException(400, detail={"errors": [item.to_dict() for item in exc.errors]}) from exc
    store = get_store()
    problem = window_ready(store, body.token.lower(), body.start_block, body.end_block)
    if problem:
        raise HTTPException(400, detail=problem)
    try:
        start_ts = datetime.fromisoformat(body.start_time)
        end_ts = datetime.fromisoformat(body.end_time)
    except ValueError as exc:
        raise HTTPException(400, detail="start_time and end_time must be ISO timestamps.") from exc
    loaded = TokenData.load(store, body.token.lower(), body.start_block, body.end_block, start_ts, end_ts)
    try:
        result = QueryEngine().run(
            program,
            swaps=loaded.swaps,
            transfers=loaded.transfers,
            wallets=loaded.wallets,
            pools=loaded.pools,
            pool_addresses=loaded.pool_addresses,
            end_ts=end_ts,
            window_start=start_ts,
            window_end=end_ts,
        )
    except QueryError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    payload = result.as_dict()
    payload["query"] = text
    return payload


@app.get("/api/tokens/suggest")
def tokens_suggest(q: str = "") -> dict[str, Any]:
    return suggest_tokens(q)


def _mount_frontend() -> None:
    dist = _dist_dir()
    index = dist / "index.html"
    if not index.is_file():
        @app.get("/")
        def missing_frontend() -> HTMLResponse:
            return HTMLResponse(
                """
                <!doctype html>
                <meta charset="utf-8">
                <title>Uniswap Wash Detector</title>
                <body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto; line-height: 1.5">
                <h1>The editor has not been built</h1>
                <p>The API is running. Build the React Flow editor, then refresh:</p>
                <pre>cd frontend && npm install && npm run build</pre>
                </body>
                """,
                status_code=200,
            )
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index_page() -> FileResponse:
        return FileResponse(index)

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = dist / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


_mount_frontend()
