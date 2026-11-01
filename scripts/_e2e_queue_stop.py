"""End-to-end proof: a session stopped while QUEUED must stay stopped.

Ceiling is pinned to 1 (GET/POST /alpha/resources) so the 2nd session cannot
get a pool slot until the 1st releases it. We stop the queued victim, then stop
the running session to free the slot -- which is exactly the moment the buggy
code would resurrect the victim and burn CPU the user asked it to stop.
"""

import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
BASE = "http://127.0.0.1:8090/api/v1/alpha"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read().decode())


def snap(batch_id):
    d = req("GET", f"/train/batch/{batch_id}/status")["data"]
    return {s["session_id"]: s for s in d["sessions"]}, d["status"]


print("policy:", req("GET", "/resources")["data"])

start = req(
    "POST",
    "/train/batch",
    {
        "symbols": ["BTC/USDT", "ETH/USDT"],
        "intervals": ["1h"],
        "train_steps": 400,
        "batch_size": 128,
        "max_concurrency": 8,
    },
)["data"]
batch_id, ids = start["batch_id"], start["session_ids"]
print("batch:", batch_id, "cap:", start["max_concurrency"], "sessions:", ids)

# wait until one holds the pool and the other is still queued
deadline = time.time() + 120
while time.time() < deadline:
    st, _ = snap(batch_id)
    rows = [(i, st.get(i, {}).get("status"), st.get(i, {}).get("step")) for i in ids]
    print("  poll:", rows)
    if any(r[1] == "running" for r in rows) and any(r[1] == "queued" for r in rows):
        break
    time.sleep(3)

st, _ = snap(batch_id)
queued = [i for i in ids if st.get(i, {}).get("status") == "queued"]
running = [i for i in ids if st.get(i, {}).get("status") == "running"]
print("running:", running, "queued:", queued)
if not queued or not running:
    print("RESULT: INCONCLUSIVE -- did not observe running+queued pair")
    sys.exit(2)

victim, holder = queued[0], running[0]
print("victim(queued):", victim, "| holder(running):", holder)

req("POST", f"/train/{victim}/stop")
print("-> stopped victim; freeing the pool by stopping the holder")
req("POST", f"/train/{holder}/stop")

# From here the victim acquires a pool slot. Buggy code flips it to running.
advanced = []
deadline = time.time() + 150
victim_final: dict = {}
holder_final: dict = {}
while time.time() < deadline:
    st, bstat = snap(batch_id)
    v, h = st.get(victim, {}), st.get(holder, {})
    victim_final, holder_final = v, h
    if v.get("step", 0) > 0:
        advanced.append((time.time(), v.get("status"), v.get("step")))
    if v.get("status") in ("stopped", "completed", "failed") and h.get("status") in (
        "stopped",
        "completed",
        "failed",
    ):
        break
    time.sleep(4)

print("\n--- verdict ---")
print("victim status:", victim_final.get("status"), "step:", victim_final.get("step"))
print("holder status:", holder_final.get("status"), "step:", holder_final.get("step"))
print("victim advanced while queued-stopped:", bool(advanced), advanced[:3])
ok = (
    victim_final.get("status") == "stopped"
    and victim_final.get("step", 0) == 0
    and not advanced
)
print("QUEUED-STOP PRESERVED:", ok)
sys.exit(0 if ok else 1)
