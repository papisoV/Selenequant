"""AC1.4 同进程 A/B:未限档(16) vs 引擎默认档(4)。

之前的基线 30.57s/194.28s 是另一时段测的,不能直接拿来比。
这里两臂在同一进程、同一合成数据、交替顺序跑,消掉机器负载漂移。

⚠️ torch.set_num_threads 是**进程级且粘滞**的 —— 所以两臂都必须**显式**传
TORCH_NUM_THREADS,不能靠"传 None 走默认"来代表未限档(那只会拿到新的默认 4)。
"未限档"臂显式传机器默认线程数,复现改动前的行为。

用法:python scripts/_bench_ab_threads.py [rounds]
"""

import glob
import os
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in [os.path.join(_ROOT, "services", "local-api")] + sorted(
    glob.glob(os.path.join(_ROOT, "packages", "*"))
):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

import torch  # noqa: E402

from selene_space_alpha.config import AlphaCoreConfig  # noqa: E402
from selene_space_alpha.data_loader import CryptoDataLoader  # noqa: E402
from selene_space_alpha.mining.engine import AlphaEngine  # noqa: E402


def build_loader():
    loader = CryptoDataLoader()
    loader.load_data = lambda *a, **k: None
    loader._create_synthetic_data(n_tokens=300)
    return loader


STEPS = 10
BATCH = 256
MACHINE_DEFAULT = torch.get_num_threads()
CAPPED = 4


def run_once(label, threads):
    cfg = AlphaCoreConfig(
        TRAIN_STEPS=STEPS,
        BATCH_SIZE=BATCH,
        MAX_FORMULA_LEN=12,
        TORCH_NUM_THREADS=threads,
    )
    loader = build_loader()
    engine = AlphaEngine(data_loader=loader, config=cfg, use_lord_regularization=False)
    applied = torch.get_num_threads()

    t0 = time.perf_counter()
    c0 = time.process_time()
    engine.train(None, 0, None, 10)
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    print(
        f"{label:22s} threads={applied:2d} wall={wall:6.2f}s cpu={cpu:7.2f}s "
        f"cores={cpu / wall:5.2f}",
        flush=True,
    )
    return wall, cpu


def main(rounds=2):
    print(
        f"machine default threads = {MACHINE_DEFAULT}, steps={STEPS}, batch={BATCH}\n"
    )
    results = {"uncapped": [], "capped": []}
    for i in range(rounds):
        # 交替先后,避免"谁先跑谁吃亏"的系统偏差
        order = [("uncapped", MACHINE_DEFAULT), ("capped", CAPPED)]
        if i % 2 == 1:
            order.reverse()
        for label, threads in order:
            wall, cpu = run_once(f"r{i + 1} {label}", threads)
            results[label].append((wall, cpu))
        print(flush=True)

    def avg(rows):
        return sum(r[0] for r in rows) / len(rows), sum(r[1] for r in rows) / len(rows)

    uw, uc = avg(results["uncapped"])
    cw, cc = avg(results["capped"])
    print(f"--- averages over {rounds} round(s) ---")
    print(f"uncapped ({MACHINE_DEFAULT} threads): wall={uw:.2f}s cpu={uc:.2f}s")
    print(f"capped   ({CAPPED} threads):       wall={cw:.2f}s cpu={cc:.2f}s")
    print(f"wall ratio capped/uncapped = {cw / uw:.2f}x")
    print(f"cpu  ratio capped/uncapped = {cc / uc:.2f}x")


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2))
