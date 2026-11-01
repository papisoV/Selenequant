"""Does the resource policy actually cut CPU? A/B on the live backend.

Phase A: cap=4, torch=4 -> 4 sessions.
Phase B: cap=1, torch=1 -> 1 session.
Samples the backend process CPU (cores) during each phase.
"""

import json
import subprocess
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
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


pid = subprocess.check_output(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-NetTCPConnection -LocalPort 8090 -State Listen).OwningProcess | Select-Object -First 1",
    ],
    text=True,
).strip()
print("backend pid:", pid)

import psutil

proc = psutil.Process(int(pid))
proc.cpu_percent(interval=None)
cores = psutil.cpu_count()
print("cpu_count:", cores)


def sample(seconds, label):
    samples = []
    t_end = time.time() + seconds
    while time.time() < t_end:
        # psutil Process.cpu_percent is already % of ONE core (can exceed 100
        # for multithreaded), so /100 yields the core count directly.
        samples.append(proc.cpu_percent(interval=2.0) / 100.0)
    samples = sorted(samples)
    mean = sum(samples) / len(samples)
    print(f"  {label}: mean={mean:.2f} cores  max={samples[-1]:.2f}  n={len(samples)}")
    return mean


def wait_idle(limit=240):
    t = time.time() + limit
    while time.time() < t:
        d = req("GET", "/resources")["data"]
        if d["active_sessions"] == 0 and d["pending_sessions"] == 0:
            return True
        time.sleep(4)
    return False


def run_phase(cap, threads, symbols, secs, label):
    req("POST", "/resources", {"max_concurrency": cap, "torch_num_threads": threads})
    start = req(
        "POST",
        "/train/batch",
        {
            "symbols": symbols,
            "intervals": ["1h"],
            "train_steps": 3000,
            "batch_size": 256,
            "max_concurrency": 32,
        },
    )["data"]
    print(f"{label}: cap={start['max_concurrency']} sessions={start['session_ids']}")
    time.sleep(25)  # let sessions reach steady state
    mean = sample(secs, label)
    for sid in start["session_ids"]:
        try:
            req("POST", f"/train/{sid}/stop")
        except Exception:
            pass
    wait_idle()
    return mean


syms4 = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
a = run_phase(4, 4, syms4, 60, "A_high(cap4/torch4)")
b = run_phase(1, 1, ["BTC/USDT"], 60, "B_low(cap1/torch1)")

print("\n--- verdict ---")
print(f"A (cap=4,torch=4, 4 sessions): {a:.2f} cores")
print(f"B (cap=1,torch=1, 1 session):  {b:.2f} cores")
print(f"ratio A/B: {a/b:.2f}x")
print("POLICY CUTS CPU:", a > b * 1.5)
