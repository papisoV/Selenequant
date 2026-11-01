"""AC1.4 实测:默认档(env 未设)构造 AlphaEngine 后,进程 torch 线程数应自动落到 4,
训练墙钟与 CPU 时间应落在验收阈值内(基线未限:30.57s / 194.28s)。

与 _bench_runtime_cap.py 的区别:这里**不手动**调 torch.set_num_threads,
上限必须由引擎自身(config.TORCH_NUM_THREADS → engine.__init__)施加。
测的正是被改的那行代码。

用法:python scripts/_bench_engine_self_cap.py
"""

import glob
import os
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# 与 API 进程一致(activate.bat 组装的 PYTHONPATH)
for _p in [os.path.join(_ROOT, "services", "local-api")] + sorted(
    glob.glob(os.path.join(_ROOT, "packages", "*"))
):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

# 刻意不设 OMP/MKL 环境变量 —— 复现生产现状。
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


def main():
    threads_at_import = torch.get_num_threads()
    print(f"torch.get_num_threads() at import (env unset) = {threads_at_import}")

    cfg = AlphaCoreConfig(TRAIN_STEPS=STEPS, BATCH_SIZE=BATCH, MAX_FORMULA_LEN=12)
    print(f"config.TORCH_NUM_THREADS = {cfg.TORCH_NUM_THREADS}")

    loader = build_loader()
    engine = AlphaEngine(data_loader=loader, config=cfg, use_lord_regularization=False)

    threads_after_ctor = torch.get_num_threads()
    print(f"torch.get_num_threads() after AlphaEngine() = {threads_after_ctor}")

    t0 = time.perf_counter()
    c0 = time.process_time()
    engine.train(None, 0, None, 10)
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0

    print(
        f"\nAC1.4 measured: wall={wall:.2f}s cpu={cpu:.2f}s "
        f"cores_equiv={cpu / wall:.2f} cpu/step={cpu / STEPS:.2f}s"
    )
    print("baseline (uncapped): wall=30.57s cpu=194.28s")
    print(
        f"threshold:  wall<=25s {'PASS' if wall <= 25 else 'FAIL'}"
        f"  cpu<=30s {'PASS' if cpu <= 30 else 'FAIL'}"
    )
    print(
        f"cap applied by engine: "
        f"{'PASS' if threads_after_ctor == cfg.TORCH_NUM_THREADS else 'FAIL'}"
        f" (expected {cfg.TORCH_NUM_THREADS}, got {threads_after_ctor})"
    )

    ok = wall <= 25 and cpu <= 30 and threads_after_ctor == cfg.TORCH_NUM_THREADS
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
