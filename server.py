#!/usr/bin/env python3
"""bench-registry: one process, one port, one SQLite file.

MCP tools + REST API + a zero-build web UI for logging training/bench runs
(config, hardware, metrics) and querying leaderboards across machines.

Run:  python server.py            (serves MCP at /mcp, UI at /)
Env:  BENCH_REGISTRY_PORT (default 8100), BENCH_REGISTRY_DB (default ./runs.db)
"""
import json
import os
import sqlite3
from pathlib import Path

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

DB = Path(os.environ.get("BENCH_REGISTRY_DB", Path(__file__).parent / "runs.db"))
PORT = int(os.environ.get("BENCH_REGISTRY_PORT", "8100"))

mcp = FastMCP("bench-registry")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT DEFAULT (datetime('now','localtime')),
                project TEXT NOT NULL,
                run_type TEXT NOT NULL DEFAULT 'bench',
                model TEXT NOT NULL,
                hardware TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                config TEXT NOT NULL DEFAULT '{}',
                metrics TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT ''
            )"""
        )
        con.commit()


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("config", "metrics"):
        try:
            d[k] = json.loads(d[k])
        except (TypeError, ValueError):
            pass
    return d


def _add(project, model, hardware, metrics, run_type="bench",
         config=None, notes="", status="ok") -> int:
    with db() as con:
        cur = con.execute(
            "INSERT INTO runs(project,run_type,model,hardware,status,config,metrics,notes)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (project, run_type, model, hardware, status,
             json.dumps(config or {}), json.dumps(metrics or {}), notes),
        )
        con.commit()
        return cur.lastrowid


def _list(project=None, model=None, hardware=None, run_type=None, limit=20) -> list:
    q, args = "SELECT * FROM runs", []
    conds = [f"{col}=?" for col, val in (("project", project), ("model", model),
                                         ("hardware", hardware), ("run_type", run_type))
             if val]
    args.extend(v for v in (project, model, hardware, run_type) if v)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    with db() as con:
        return [_row(r) for r in con.execute(q, args)]


def _leaderboard(metric, project=None, hardware=None, model=None,
                 higher_is_better=True, limit=10) -> list:
    rows = _list(project, model, hardware, None, 500)
    scored = []
    for r in rows:
        v = r["metrics"].get(metric) if isinstance(r["metrics"], dict) else None
        if isinstance(v, (int, float)):
            scored.append({
                "run_id": r["id"], "created": r["created"], "project": r["project"],
                "run_type": r["run_type"], "model": r["model"],
                "hardware": r["hardware"], "status": r["status"],
                metric: float(v),
            })
    scored.sort(key=lambda x: x[metric], reverse=bool(higher_is_better))
    return scored[: max(1, min(int(limit), 100))]


def _summary() -> dict:
    with db() as con:
        out = {"total_runs": con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]}
        for key, col in (("projects", "project"), ("models", "model"),
                         ("hardware", "hardware"), ("run_types", "run_type")):
            out[key] = {r[col]: r["c"] for r in con.execute(
                f"SELECT {col}, COUNT(*) c FROM runs GROUP BY {col} ORDER BY c DESC")}
    metrics = set()
    for r in _list(None, None, None, None, 500):
        if isinstance(r["metrics"], dict):
            metrics.update(k for k, v in r["metrics"].items()
                           if isinstance(v, (int, float)))
    out["numeric_metrics"] = sorted(metrics)
    return out


# ---------------------------------------------------------------- MCP tools

@mcp.tool(name="bench_run_add")
def bench_run_add(project: str, model: str, hardware: str, metrics: dict,
                  run_type: str = "bench", config: dict | None = None,
                  notes: str = "", status: str = "ok") -> str:
    """Log a training/bench/eval run. metrics/config are free-form JSON objects
    (e.g. metrics={"tokens_per_sec": 42.1, "ppl": 8.3}). Returns the new run id."""
    return json.dumps({"run_id": _add(project, model, hardware, metrics,
                                      run_type, config, notes, status)})


@mcp.tool(name="bench_run_list")
def bench_run_list(project: str | None = None, model: str | None = None,
                   hardware: str | None = None, run_type: str | None = None,
                   limit: int = 20) -> str:
    """List recent runs, newest first, with optional filters."""
    return json.dumps(_list(project, model, hardware, run_type, limit))


@mcp.tool(name="bench_run_get")
def bench_run_get(run_id: int) -> str:
    """Fetch one run (config, metrics, notes) by id."""
    with db() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return json.dumps(_row(r) if r else {"error": f"run {run_id} not found"})


@mcp.tool(name="bench_run_delete")
def bench_run_delete(run_id: int) -> str:
    """Delete one run by id."""
    with db() as con:
        cur = con.execute("DELETE FROM runs WHERE id=?", (run_id,))
        con.commit()
    return json.dumps({"deleted": cur.rowcount})


@mcp.tool(name="bench_leaderboard")
def bench_leaderboard(metric: str, project: str | None = None,
                      hardware: str | None = None, model: str | None = None,
                      higher_is_better: bool = True, limit: int = 10) -> str:
    """Rank runs by a numeric metric across the fleet. Use bench_summary() to see
    which metric keys exist."""
    return json.dumps(_leaderboard(metric, project, hardware, model,
                                   higher_is_better, limit))


@mcp.tool(name="bench_summary")
def bench_summary() -> str:
    """Counts per project/model/hardware/run_type plus all numeric metric keys."""
    return json.dumps(_summary())


# ------------------------------------------------------------- REST + UI

@mcp.custom_route("/api/runs", methods=["GET", "POST"])
async def api_runs(request: Request):
    if request.method == "POST":
        b = await request.json()
        for k in ("project", "model", "hardware", "metrics"):
            if k not in b:
                return JSONResponse({"error": f"missing field: {k}"}, status_code=400)
        return JSONResponse({"run_id": _add(
            b["project"], b["model"], b["hardware"], b["metrics"],
            b.get("run_type", "bench"), b.get("config"),
            b.get("notes", ""), b.get("status", "ok"))})
    p = request.query_params
    return JSONResponse(_list(p.get("project"), p.get("model"),
                              p.get("hardware"), p.get("run_type"),
                              int(p.get("limit", 50))))


@mcp.custom_route("/api/runs/{run_id}", methods=["GET", "DELETE"])
async def api_run(request: Request):
    rid = int(request.path_params["run_id"])
    if request.method == "DELETE":
        with db() as con:
            cur = con.execute("DELETE FROM runs WHERE id=?", (rid,))
            con.commit()
        return JSONResponse({"deleted": cur.rowcount})
    with db() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    return JSONResponse(_row(r) if r else {"error": "not found"},
                        status_code=200 if r else 404)


@mcp.custom_route("/api/leaderboard", methods=["GET"])
async def api_leaderboard(request: Request):
    p = request.query_params
    metric = p.get("metric")
    if not metric:
        return JSONResponse({"error": "metric= required"}, status_code=400)
    return JSONResponse(_leaderboard(
        metric, p.get("project"), p.get("hardware"), p.get("model"),
        p.get("higher_is_better", "true").lower() != "false",
        int(p.get("limit", 10))))


@mcp.custom_route("/api/summary", methods=["GET"])
async def api_summary(request: Request):
    return JSONResponse(_summary())


UI = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>bench-registry</title>
<style>
 body{background:#0f1115;color:#e6e6e6;font:14px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:1000px;padding:0 1rem}
 h1{font-size:1.3rem} h1 span{color:#7aa2f7} .muted{color:#888;font-size:.85rem}
 table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
 th,td{padding:.35rem .6rem;border-bottom:1px solid #232833;text-align:left}
 th{color:#888;font-weight:600} td.num{color:#9ece6a;font-variant-numeric:tabular-nums}
 select,input{background:#161a22;color:#e6e6e6;border:1px solid #2a3040;border-radius:6px;padding:.3rem .5rem}
 .bad{color:#f7768e} .ok{color:#9ece6a}
</style></head><body>
<h1>bench-<span>registry</span> <span class="muted" id="counts"></span></h1>
<label>Leaderboard metric: <select id="metric"></select>
 <select id="hib"><option value="true">higher is better</option>
 <option value="false">lower is better</option></select></label>
<table id="lb"><tr><th>#</th><th>run</th><th>project</th><th>model</th><th>hardware</th><th>value</th></tr></table>
<h3>Recent runs</h3>
<table id="runs"><tr><th>id</th><th>created</th><th>project</th><th>type</th><th>model</th><th>hardware</th><th>status</th><th>metrics</th></tr></table>
<script>
async function j(u){const r=await fetch(u);return r.json()}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function refresh(){
 const s=await j('/api/summary');
 document.getElementById('counts').textContent=`${s.total_runs} runs`;
 const sel=document.getElementById('metric');
 if(!sel.options.length){for(const m of s.numeric_metrics){const o=document.createElement('option');o.value=o.text=m;sel.appendChild(o)}}
 const runs=await j('/api/runs?limit=25');
 document.querySelector('#runs').innerHTML='<tr><th>id</th><th>created</th><th>project</th><th>type</th><th>model</th><th>hardware</th><th>status</th><th>metrics</th></tr>'+
  runs.map(r=>`<tr><td>${r.id}</td><td>${esc(r.created)}</td><td>${esc(r.project)}</td><td>${esc(r.run_type)}</td><td>${esc(r.model)}</td><td>${esc(r.hardware)}</td><td class="${r.status==='ok'?'ok':'bad'}">${esc(r.status)}</td><td>${esc(Object.entries(r.metrics).map(([k,v])=>k+'='+v).join(' '))}</td></tr>`).join('');
 if(sel.options.length){
  const lb=await j(`/api/leaderboard?metric=${encodeURIComponent(sel.value)}&higher_is_better=${document.getElementById('hib').value}&limit=10`);
  document.querySelector('#lb').innerHTML='<tr><th>#</th><th>run</th><th>project</th><th>model</th><th>hardware</th><th>'+esc(sel.value)+'</th></tr>'+
   lb.map((r,i)=>`<tr><td>${i+1}</td><td>#${r.run_id} ${esc(r.created)}</td><td>${esc(r.project)}</td><td>${esc(r.model)}</td><td>${esc(r.hardware)}</td><td class="num">${r[sel.value]}</td></tr>`).join('');
 }
}
document.getElementById('metric').onchange=refresh;
document.getElementById('hib').onchange=refresh;
refresh();setInterval(refresh,15000);
</script></body></html>"""


@mcp.custom_route("/", methods=["GET"])
async def ui(request: Request):
    return HTMLResponse(UI)


if __name__ == "__main__":
    init_db()
    mcp.run(transport="streamable-http", host="0.0.0.0", port=PORT, path="/mcp")
