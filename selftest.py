#!/usr/bin/env python3
"""Self-test for bench-registry over raw JSON-RPC (no LLM in the loop).

Starts nothing -- point it at a running server:  python selftest.py [base_url]
Checks: initialize -> tools/list -> add -> list -> leaderboard -> get -> delete.
Exits 0 and prints PASS lines when everything works.
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
URL = BASE.rstrip("/") + "/mcp"
HEADERS = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
_session = {}


def rpc(payload, method_hint=True):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={**HEADERS, **_session}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            _session["Mcp-Session-Id"] = sid
        body = resp.read().decode()
    if not body.strip():
        return None  # 202 for notifications
    if "\ndata: " in body or body.startswith("data: "):  # SSE framing
        for line in body.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return None
    return json.loads(body)


def call_tool(name, args):
    r = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": name, "arguments": args}})
    txt = r["result"]["content"][0]["text"]
    return json.loads(txt)


def main():
    init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "selftest", "version": "0"}}})
    assert init["result"]["serverInfo"]["name"] == "bench-registry", init
    print("PASS initialize:", init["result"]["serverInfo"])

    rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, False)

    tools = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                 "params": {}})["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    print("PASS tools/list:", names)
    assert names == ["bench_leaderboard", "bench_run_add", "bench_run_delete",
                     "bench_run_get", "bench_run_list", "bench_summary"], names

    added = call_tool("bench_run_add", {
        "project": "selftest", "model": "toy-1m", "hardware": "cpu",
        "run_type": "bench", "metrics": {"tok_per_sec": 1.5},
        "config": {"ctx": 512}, "notes": "created by selftest"})
    rid = added["run_id"]
    print("PASS bench_run_add: run_id", rid)

    rows = call_tool("bench_run_list", {"project": "selftest"})
    assert any(r["id"] == rid for r in rows), rows
    print("PASS bench_run_list:", len(rows), "rows")

    lb = call_tool("bench_leaderboard", {"metric": "tok_per_sec", "limit": 5})
    assert any(r["run_id"] == rid for r in lb), lb
    print("PASS bench_leaderboard: top =", lb[0] if lb else None)

    got = call_tool("bench_run_get", {"run_id": rid})
    assert got["metrics"] == {"tok_per_sec": 1.5}, got
    print("PASS bench_run_get")

    assert call_tool("bench_run_delete", {"run_id": rid})["deleted"] == 1
    print("PASS bench_run_delete (cleanup)")

    print("ALL PASS")


if __name__ == "__main__":
    main()
