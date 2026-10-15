"""R&D loop provider — 7 R&D loops inspired by the rdagents framework.

Loops: factor / model / quant / report / kaggle / ds / multitrace.
Each loop is a separate provider id but uses the same RDLoopProvider class
with a different `loop_factory` injected via closure.

Pattern aligned with selene_space_optimize.providers.rl_provider.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Callable


from selene_space_agentstudio.base import (
    AgentStudioKind,
    AgentStudioProviderConfig,
    AgentStudioReport,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Availability flags (lazy probe — populated by _probe_availability())
#
# 2026-10-07 性能修复：原先这里是 6 个模块级 `try: from rdagent.app... import X`
# 语句。实测 `import rdagent` 本身是 0.0s，但 `rdagent.app.qlib_rd_loop.quant`
# 这条子模块链要 **10.7s**（它拖进 qlib/scipy/plotly 等）。本模块又在
# `providers/__init__.py` 里被**导入即调用**，而后者被 local-api 的
# `app.api.v1.agentstudio` 导入 —— 于是每次后端启动，import 阶段白付 ~17s。
#
# 改成惰性：模块导入时不算可用性，只在真正需要时(subprocess 隔离)探一次。
# 探针跑在**子进程**里 —— `import` 一旦失败/成功后 sys.modules 会缓存，
# 但真正的问题是探测本身很贵，放主进程会阻塞事件循环；子进程隔离同时也
# 避免了"探测失败后 rdagent 半初始化状态留在 sys.modules"的污染。
# ---------------------------------------------------------------------------

_LOOP_IMPORT_PATHS: dict[str, str] = {
    "factor_loop": "rdagent.app.qlib_rd_loop.factor",
    "model_loop": "rdagent.app.qlib_rd_loop.model",
    "quant_loop": "rdagent.app.qlib_rd_loop.quant",
    "report_loop": "rdagent.app.qlib_rd_loop.factor_from_report",
    "kaggle_loop": "rdagent.app.kaggle.loop",
    "ds_loop": "rdagent.app.data_science.loop",
}

# 探测结果缓存：None = 未探测；dict = 已探测（值为 bool）
_availability_cache: dict[str, bool] | None = None


def _probe_all(timeout_s: float = 60.0) -> dict[str, bool]:
    """**一次**子进程里探完 7 条 import 链，返回 {loop_name: bool}。

    合并成单次子进程的理由：7 条路径共享同一条重依赖链（rdagent→qlib→…），
    实测整链 ~10s；逐个 spawn 是 7×10s。一次 import 全部再报告各自成败，
    总成本仍是一次链路加载。

    子进程隔离的理由：该 import 很贵，放主进程会卡住事件循环；且失败时
    不会把半初始化的 rdagent 留在 sys.modules。
    """
    import json
    import subprocess
    import sys as _sys

    probe_src = (
        "import json,sys\n"
        f"paths={_LOOP_IMPORT_PATHS!r}\n"
        "out={}\n"
        "for name,mod in paths.items():\n"
        "    try:\n"
        "        __import__(mod)\n"
        "        out[name]=True\n"
        "    except Exception:\n"
        "        out[name]=False\n"
        # rdagent 0.8.0 无 rdagent.app.multitrace 模块 —— multitrace = DS loop
        # 的 MCTS 变体（见模块 docstring），故与其镜像。
        "out['multitrace_loop']=out.get('ds_loop',False)\n"
        "print(json.dumps(out))\n"
    )
    try:
        proc = subprocess.run(
            [_sys.executable, "-c", probe_src],
            capture_output=True,
            timeout=timeout_s,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return {name: False for name in (*_LOOP_IMPORT_PATHS, "multitrace_loop")}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {name: False for name in (*_LOOP_IMPORT_PATHS, "multitrace_loop")}
    try:
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {name: False for name in (*_LOOP_IMPORT_PATHS, "multitrace_loop")}
    result = {name: bool(parsed.get(name, False)) for name in _LOOP_IMPORT_PATHS}
    result["multitrace_loop"] = result["ds_loop"]
    return result


def refresh_availability(*, force: bool = False) -> dict[str, bool]:
    """探测 7 个 loop 的可用性并缓存。首次调用付一次子进程探测成本。

    ``force=True`` 绕过缓存重测（测试/诊断用）。
    """
    global _availability_cache
    if _availability_cache is not None and not force:
        return _availability_cache
    _availability_cache = _probe_all()
    return _availability_cache


# 惰性模块属性：`from ... import FACTOR_LOOP_AVAILABLE` 等仍可用，
# 但**首次读取才触发探测**（PEP 562 模块 __getattr__）。
# 保留这些名字的理由：既有测试直接 import 它们（test_agentstudio.py 的
# test_loop_availability_dict_structure / test_multitrace_availability_matches_ds）。
_LAZY_FLAG_TO_LOOP = {
    "FACTOR_LOOP_AVAILABLE": "factor_loop",
    "MODEL_LOOP_AVAILABLE": "model_loop",
    "QUANT_LOOP_AVAILABLE": "quant_loop",
    "REPORT_LOOP_AVAILABLE": "report_loop",
    "KAGGLE_LOOP_AVAILABLE": "kaggle_loop",
    "DS_LOOP_AVAILABLE": "ds_loop",
    "MULTITRACE_LOOP_AVAILABLE": "multitrace_loop",
}


def __getattr__(name: str):  # noqa: D103
    loop = _LAZY_FLAG_TO_LOOP.get(name)
    if loop is not None:
        return _loop_available(loop)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _AvailabilityDict(dict):
    """LOOP_AVAILABILITY —— 首次访问才触发探测的惰性映射。

    兼容两处既有用法（测试直接 assert 其 keys/values）：
      · `LOOP_AVAILABILITY["quant_loop"]` / `.get(...)` → 触发一次探测
      · `set(LOOP_AVAILABILITY.keys())` / `.values()` / 迭代 → 同上
    字典语义完整保留，只是把"何时探测"从 import 时推迟到首次读取。
    """

    def _ensure(self) -> None:
        """首次访问时探测一次，并把结果灌进字典自身（此后走普通 dict 快路径）。

        用 `dict.__len__` 而非 `bool(self)`：后者会走本类重写的 `__len__`
        → 再回 `_ensure` → 无限递归（实测 RecursionError）。
        """
        if dict.__len__(self) == 0:
            dict.update(self, refresh_availability())

    def __getitem__(self, k):  # noqa: D105
        self._ensure()
        return super().__getitem__(k)

    def get(self, k, default=None):  # noqa: D102
        self._ensure()
        return super().get(k, default)

    def keys(self):  # noqa: D102
        self._ensure()
        return super().keys()

    def values(self):  # noqa: D102
        self._ensure()
        return super().values()

    def items(self):  # noqa: D102
        self._ensure()
        return super().items()

    def __iter__(self):  # noqa: D105
        self._ensure()
        return super().__iter__()

    def __len__(self):  # noqa: D105
        self._ensure()
        return super().__len__()

    def __contains__(self, k):  # noqa: D105
        self._ensure()
        return super().__contains__(k)


LOOP_AVAILABILITY: dict[str, bool] = _AvailabilityDict()


def _loop_available(loop_name: str) -> bool:
    """单个 loop 的可用性（惰性探测 + 缓存）。"""
    return refresh_availability().get(loop_name, False)


def rdagents_available() -> bool:
    """Check if rdagent (Microsoft RD-Agent framework) is importable.

    Package name on PyPI is ``rdagent`` (singular). Some historical code referred
    it ``rdagents`` (plural) — that import will always fail. Use the correct
    singular form here.
    """
    try:
        import rdagent  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# RDLoopProvider — single class, 7 loop factory closures
# ---------------------------------------------------------------------------


class RDLoopProvider:
    """Async R&D loop provider — one of 7 rdagents framework loops."""

    def __init__(
        self,
        cfg: AgentStudioProviderConfig,
        *,
        loop_name: str,
        loop_factory: Callable[..., Any] | None,
    ) -> None:
        self._cfg = cfg
        self._loop_name = loop_name
        self._loop_factory = loop_factory
        self._running = False
        self._loop: Any = None

    @property
    def config(self) -> AgentStudioProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._loop = None

    async def healthcheck(self) -> bool:
        return LOOP_AVAILABILITY.get(self._loop_name, False)

    async def compute(
        self,
        *,
        action: str,
        task: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> AgentStudioReport:
        if action not in {"build", "run", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.OTHER,
                title=f"{self._loop_name} Info",
                metrics={
                    "loop": self._loop_name,
                    "rdagents_available": rdagents_available(),
                    "loop_available": LOOP_AVAILABILITY.get(self._loop_name, False),
                },
                computed_at=datetime.now(UTC),
            )

        if not rdagents_available() or self._loop_factory is None:
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.RD_LOOP,
                title=f"{self._loop_name} {action} (unavailable)",
                metrics={
                    "error": "rdagents not installed",
                    "hint": "pip install rdagents",
                },
                computed_at=datetime.now(UTC),
                notes="R&D loop extras not installed",
            )

        if action == "build":
            return self._build_loop(task=task or "", params=params or {})
        return self._run_loop(task=task or "", params=params or {})

    def _build_loop(self, *, task: str, params: dict[str, Any]) -> AgentStudioReport:
        """Build a loop instance without running it."""
        try:
            workspace = str(params.get("workspace_dir", "./_agentstudio_workspace"))
            target_market = str(params.get("target_market", "US"))
            self._loop = self._loop_factory(  # type: ignore[misc]
                workspace_dir=workspace,
                task_description=task,
                target_market=target_market,
            )
            metrics = {
                "loop": self._loop_name,
                "built": True,
                "workspace": workspace,
                "target_market": target_market,
            }
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.RD_LOOP,
                title=f"{self._loop_name} built",
                metrics=metrics,
                artifacts={"loop_config": {"workspace": workspace, "task": task}},
                computed_at=datetime.now(UTC),
                notes=f"Built {self._loop_name} instance (not yet run)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s build failed: %s", self._loop_name, exc)
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.RD_LOOP,
                title=f"{self._loop_name} build (failed)",
                metrics={"error": str(exc)},
                computed_at=datetime.now(UTC),
            )

    def _run_loop(self, *, task: str, params: dict[str, Any]) -> AgentStudioReport:
        """Build + run a loop for `loop_n` iterations."""
        try:
            n_iter = int(params.get("loop_n", 1))
            workspace = str(params.get("workspace_dir", "./_agentstudio_workspace"))
            target_market = str(params.get("target_market", "US"))
            self._loop = self._loop_factory(  # type: ignore[misc]
                workspace_dir=workspace,
                task_description=task,
                target_market=target_market,
            )
            # rdagents loops expose .run(loop_n=N)
            result = self._loop.run(loop_n=n_iter)
            metrics = {
                "loop": self._loop_name,
                "iterations": n_iter,
                "workspace": workspace,
                "ran": True,
                "result_type": type(result).__name__,
            }
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.RD_LOOP,
                title=f"{self._loop_name} run ({n_iter} iter)",
                metrics=metrics,
                artifacts={"loop_config": {"workspace": workspace, "task": task}},
                computed_at=datetime.now(UTC),
                notes=f"Ran {self._loop_name} for {n_iter} iterations",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s run failed: %s", self._loop_name, exc)
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.RD_LOOP,
                title=f"{self._loop_name} run (failed)",
                metrics={"error": str(exc)},
                computed_at=datetime.now(UTC),
            )

    def list_actions(self) -> list[str]:
        return ["build", "run", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "build": f"Build {self._loop_name} instance (params: workspace_dir, target_market)",
            "run": f"Build + run {self._loop_name} for loop_n iterations (params: workspace_dir, target_market, loop_n)",
            "info": "Provider info",
        }.get(action, action)


# ---------------------------------------------------------------------------
# Factory closures — one per loop
# ---------------------------------------------------------------------------


def _make_factor_loop(workspace_dir: str, task_description: str, target_market: str = "US") -> Any:
    from rdagent.app.qlib_rd_loop.factor import FactorRDLoop  # noqa: PLC0415
    return FactorRDLoop(
        workspace_dir=workspace_dir,
        task_description=task_description,
        target_market=target_market,
    )


def _make_model_loop(workspace_dir: str, task_description: str, target_market: str = "US") -> Any:
    from rdagent.app.qlib_rd_loop.model import ModelRDLoop  # noqa: PLC0415
    return ModelRDLoop(
        workspace_dir=workspace_dir,
        task_description=task_description,
        target_market=target_market,
    )


def _make_quant_loop(workspace_dir: str, task_description: str, target_market: str = "US") -> Any:
    from rdagent.app.qlib_rd_loop.quant import QuantRDLoop  # noqa: PLC0415
    return QuantRDLoop(
        workspace_dir=workspace_dir,
        task_description=task_description,
        target_market=target_market,
    )


def _make_report_loop(workspace_dir: str, task_description: str, target_market: str = "US") -> Any:
    from rdagent.app.qlib_rd_loop.factor_from_report import FactorReportLoop  # noqa: PLC0415
    return FactorReportLoop(
        workspace_dir=workspace_dir,
        task_description=task_description,
        target_market=target_market,
    )


def _make_kaggle_loop(workspace_dir: str, task_description: str, target_market: str = "US") -> Any:
    from rdagent.app.kaggle.loop import KaggleRDLoop  # noqa: PLC0415
    return KaggleRDLoop(
        workspace_dir=workspace_dir,
        task_description=task_description,
    )


def _make_ds_loop(workspace_dir: str, task_description: str, target_market: str = "US") -> Any:
    from rdagent.app.data_science.loop import DataScienceRDLoop  # noqa: PLC0415
    return DataScienceRDLoop(
        workspace_dir=workspace_dir,
        task_description=task_description,
    )


def _make_multitrace_loop(workspace_dir: str, task_description: str, target_market: str = "US") -> Any:
    """Multitrace loop = DataScienceRDLoop configured for MCTS exploration.

    Mirrors rdagents framework build_multitrace_loop, which is a thin wrapper
    over build_ds_loop with MCTS defaults — there is no separate MultitraceRDLoop
    class in rdagent 0.8.0.
    """
    from rdagent.app.data_science.loop import DataScienceRDLoop  # noqa: PLC0415
    return DataScienceRDLoop(
        workspace_dir=workspace_dir,
        task_description=task_description,
    )


# loop_name → 工厂函数（静态表，不读可用性）。
# ⚠️ 这里**不能**再写成 `_make_factor_loop if FACTOR_LOOP_AVAILABLE else None`：
# 那会在模块导入时读那 7 个惰性常量 → 立刻触发一次完整探测 → 把修掉的
# 17 秒启动成本又加回来。可用性判断推迟到 factory_closure 实际被调用时
# （见 register_rd_loops / RDLoopProvider.compute 的 _loop_factory is None 分支）。
_LOOP_FACTORY_FNS: dict[str, Callable[..., Any]] = {
    "factor_loop": _make_factor_loop,
    "model_loop": _make_model_loop,
    "quant_loop": _make_quant_loop,
    "report_loop": _make_report_loop,
    "kaggle_loop": _make_kaggle_loop,
    "ds_loop": _make_ds_loop,
    "multitrace_loop": _make_multitrace_loop,
}


def _loop_factory_for(loop_name: str) -> Callable[..., Any] | None:
    """loop_name → 工厂函数；不可用时 None（惰性判断，首次调用才探测）。"""
    return _LOOP_FACTORY_FNS[loop_name] if _loop_available(loop_name) else None


def register_rd_loops() -> None:
    """Register all 7 R&D loops as separate providers.

    注册本身**不探测**（避免 import 时付 17s）；provider 被实际调用时才通过
    `_loop_factory_for` 判断可用性。
    """
    from selene_space_agentstudio.registry import register_provider

    for loop_name in _LOOP_FACTORY_FNS:
        def make_factory(ln: str) -> Callable[[AgentStudioProviderConfig], RDLoopProvider]:
            def factory_closure(cfg: AgentStudioProviderConfig) -> RDLoopProvider:
                cfg_with_name = cfg.model_copy(update={"provider": ln})
                return RDLoopProvider(
                    cfg_with_name, loop_name=ln, loop_factory=_loop_factory_for(ln)
                )
            return factory_closure

        register_provider(loop_name, make_factory(loop_name))
