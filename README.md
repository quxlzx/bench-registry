# bench-registry

One process, one port, one SQLite file: an **MCP server + REST API + zero-build web UI**
for logging training / benchmark / eval runs and querying leaderboards across machines.

Built for the pattern of "agent fleet runs benchmarks on several hosts, results
scattered across logs" — every agent (or curl) appends a run with free-form
metrics/config JSON, and anyone can ask *"what was the best tokens/sec on any
GPU for model X?"* and get a ranked answer.

## Reproduce in 3 commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py &          # UI on http://127.0.0.1:8100/ , MCP on /mcp
.venv/bin/python selftest.py          # raw JSON-RPC end-to-end test, no LLM needed
```

`selftest.py` prints `PASS` for initialize → tools/list → add → list →
leaderboard → get → delete and cleans up after itself. Linux smoke-tested.

## What you get

| Surface | Where |
|---|---|
| Web UI (runs table + metric leaderboard picker) | `GET /` |
| REST | `POST/GET /api/runs`, `GET/DELETE /api/runs/{id}`, `GET /api/leaderboard?metric=...`, `GET /api/summary` |
| MCP (streamable HTTP) | `/mcp` |

### MCP tools

- `bench_run_add(project, model, hardware, metrics, run_type, config, notes, status)` — log a run; `metrics`/`config` are free-form JSON objects
- `bench_run_list(project?, model?, hardware?, run_type?, limit?)` — newest first
- `bench_run_get(run_id)` / `bench_run_delete(run_id)`
- `bench_leaderboard(metric, project?, hardware?, model?, higher_is_better?, limit?)` — rank runs by any numeric metric
- `bench_summary()` — counts per project/model/hardware + every numeric metric key that exists

## Registering with an MCP client

Point any streamable-HTTP MCP client at `http://127.0.0.1:8100/mcp`.
For Hermes:

```bash
hermes config set mcp_servers.bench-registry.url http://127.0.0.1:8100/mcp
```

(takes effect in new sessions)

## Data model

Single `runs` table in `runs.db` (override with `BENCH_REGISTRY_DB`):
`id, created, project, run_type, model, hardware, status, config(json), metrics(json), notes`.
Port override: `BENCH_REGISTRY_PORT` (default 8100). No telemetry, no auth —
keep it on localhost/LAN like any other local tool.

## Why

Benchmark results across a fleet usually die in per-machine log files. This makes
them a first-class queryable dataset that every agent on the network can write to
and read from through its normal tool loop — and a human can eyeball in a browser.

## License

MIT
