"""PA 心跳循环(SELENE_PA_HEARTBEAT)——每日无人值守诊断。

验收锚(计划 §3 T1,逐条对应):
  ① 门控默认关,关闭 = 零行为
  ② 逐品种严格串行(不 gather)
  ③ 成本预算门(按尝试计,非按成功计)
  ④ klines 新鲜度前置(未同步品种跳过 + 记日志,不中止整轮)
  ⑤ demo 短路(不往审计表写罐头记录)
  ⑥ 单品种失败不中止整轮

所有测试 hermetic:``build_trading_input`` 在 ``app.core.pa_heartbeat`` 模块上
打补丁(心跳端顶层 import 后即绑定),klines.db 门指向 tmp 实体文件,LLM 由
``_FakeService`` 承接 —— 不发任何真实请求。
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.core.pa_heartbeat as hb
from app.core import trading_input

# services/local-api/tests/x.py → parents[3] = 仓库根
_MAIN_PY = (
    Path(__file__).resolve().parents[3] / "services" / "local-api" / "app" / "main.py"
)

# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class _FakeSettings:
    def __init__(
        self, favorites: list[str] | None = None, *, raise_on_read: bool = False
    ) -> None:
        self._favorites = favorites or []
        self._raise = raise_on_read

    def get_favorite_symbols(self) -> list[str]:
        if self._raise:
            raise RuntimeError("settings backend down")
        return list(self._favorites)


class _FakeService:
    """``analyze_trading`` 替身:记录尝试顺序 + 并发度 + 可注入失败品种。

    每次调用先 ``await asyncio.sleep(0)`` 让出事件循环:若调用方用 gather
    并发,``max_active`` 会 > 1,串行约束即被捕获。
    """

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.usage: dict[str, dict[str, int]] = {}
        self._fail_on = fail_on or set()

    async def analyze_trading(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
        trading_stance: str | None = None,
        htf_summary_data: dict[str, Any] | None = None,
    ) -> Any:
        symbol = input_data["symbol"]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            self.calls.append(symbol)
            if symbol in self._fail_on:
                raise RuntimeError("llm exploded")
            usage = self.usage.get(
                symbol,
                {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            )
            return SimpleNamespace(usage_total=usage)
        finally:
            self.active -= 1


class _NoUsageService:
    """``usage_total`` 缺失的畸形 record(验证日志取值有兜底)。"""

    def __init__(self) -> None:
        self.calls = 0

    async def analyze_trading(self, input_data: dict[str, Any], **kwargs: Any) -> Any:
        self.calls += 1
        return SimpleNamespace()


class _FakeInputBuilder:
    """``build_trading_input`` 替身:记录取数契约 + 可注入"未同步"品种
    + 可注入"末根 bar 时间"(新鲜度守卫的输入)。"""

    def __init__(
        self,
        *,
        missing: set[str] | None = None,
        stale: dict[str, float] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._missing = missing or set()
        # symbol -> snapshot.fetched_at (epoch ms)。缺省 = "现在",不触发守卫。
        self._stale = stale or {}

    def __call__(
        self,
        klines_db: Any,
        symbol: str,
        interval: str,
        *,
        bars: int,
        market_type: str = "futures",
        htf_interval: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        self.calls.append(
            {
                "symbol": symbol,
                "interval": interval,
                "bars": bars,
                "market_type": market_type,
                "htf_interval": htf_interval,
            }
        )
        if symbol in self._missing:
            # 真契约:薄序列/未同步 → RuntimeError("insufficient bars…")
            raise RuntimeError(f"insufficient bars in {klines_db}: 0")
        input_data = {
            "symbol": symbol,
            "interval": interval,
            "bars_total": bars,
            # 真契约:snapshot.fetched_at = 末根 bar 的 ts(epoch ms)
            "snapshot": {
                "requested_bars": bars,
                "bars_total": bars,
                "fetched_at": self._stale.get(symbol, int(time.time() * 1000)),
            },
        }
        htf = {"interval": htf_interval} if htf_interval else None
        return input_data, htf

    @property
    def symbols(self) -> list[str]:
        return [c["symbol"] for c in self.calls]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离环境变量,避免宿主 shell 串味。"""
    for name in ("SELENE_PA_HEARTBEAT", "SELENE_PA_DEMO_MODE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def klines_db(tmp_path: Any) -> Any:
    db = tmp_path / "klines.db"
    db.touch()
    return db


def _scheduler(
    favorites: list[str] | None = None,
    *,
    service: Any = None,
    settings: Any = None,
    settings_loader: Any = None,
    service_loader: Any = None,
    klines_db: Any = None,
    config: hb.PaHeartbeatConfig | None = None,
    sync_loader: Any = None,
) -> hb.PaHeartbeatScheduler:
    return hb.PaHeartbeatScheduler(
        settings_loader=(
            settings_loader
            if settings_loader is not None
            else (
                lambda: settings if settings is not None else _FakeSettings(favorites)
            )
        ),
        service_loader=(
            service_loader
            if service_loader is not None
            else (lambda: service if service is not None else _FakeService())
        ),
        config=config,
        klines_db=klines_db,
        sync_loader=sync_loader,
    )


# ---------------------------------------------------------------------------
# ① 门控:默认关,关闭 = 零行为
# ---------------------------------------------------------------------------


def test_gate_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SELENE_PA_HEARTBEAT", raising=False)
    assert hb.heartbeat_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_gate_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SELENE_PA_HEARTBEAT", value)
    assert hb.heartbeat_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_gate_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SELENE_PA_HEARTBEAT", value)
    assert hb.heartbeat_enabled() is False


def test_disabled_scheduler_never_started_has_no_task(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """门控关 → main.py 不构造/不 start;此处锚定"零行为"的可观察形态。"""
    monkeypatch.delenv("SELENE_PA_HEARTBEAT", raising=False)
    assert hb.heartbeat_enabled() is False
    sched = _scheduler(["BTC/USDT"], klines_db=klines_db)
    assert sched.status()["last_round"] is None
    assert sched.status()["attempts_today"] == 0


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SELENE_PA_HEARTBEAT_HOUR_UTC",
        "SELENE_PA_HEARTBEAT_DAILY_MAX",
        "SELENE_PA_HEARTBEAT_INTERVAL",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = hb.PaHeartbeatConfig.from_env()
    assert cfg.hour_utc == hb._DEFAULT_HOUR_UTC
    assert cfg.daily_max == hb._DEFAULT_DAILY_MAX
    assert cfg.interval == hb._DEFAULT_INTERVAL
    assert cfg.htf_interval == hb._DEFAULT_HTF_INTERVAL


def test_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELENE_PA_HEARTBEAT_HOUR_UTC", "7")
    monkeypatch.setenv("SELENE_PA_HEARTBEAT_DAILY_MAX", "3")
    monkeypatch.setenv("SELENE_PA_HEARTBEAT_INTERVAL", "4h")
    cfg = hb.PaHeartbeatConfig.from_env()
    assert (cfg.hour_utc, cfg.daily_max, cfg.interval) == (7, 3, "4h")


def test_from_env_bad_int_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELENE_PA_HEARTBEAT_DAILY_MAX", "not-a-number")
    assert hb.PaHeartbeatConfig.from_env().daily_max == hb._DEFAULT_DAILY_MAX


# ---------------------------------------------------------------------------
# ② 逐品种串行 + 与 panel 同形输入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_is_serial_in_favorite_order(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"], service=svc, klines_db=klines_db
    )

    await sched._run_round()

    assert svc.calls == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert builder.symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    # 串行约束:并发度恒为 1(若换成 gather 会 > 1)
    assert svc.max_active == 1


@pytest.mark.asyncio
async def test_round_feeds_identical_input_shape_as_panel(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """心跳与 /analyze-trading 必须走同一装配函数、同一取数参数。"""
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    cfg = hb.PaHeartbeatConfig(
        interval="1h", bars=120, market_type="futures", htf_interval="4h"
    )
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db, config=cfg)

    await sched._run_round()

    assert builder.calls == [
        {
            "symbol": "BTC/USDT",
            "interval": "1h",
            "bars": 120,
            "market_type": "futures",
            "htf_interval": "4h",
        }
    ]


def test_heartbeat_reuses_shared_assembly_function() -> None:
    """结构护栏:心跳端 import 的就是端点的那个装配函数(防静默漂移)。"""
    assert hb.build_trading_input is trading_input.build_trading_input


# ---------------------------------------------------------------------------
# ③ 成本预算门
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_gate_truncates_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["S1", "S2", "S3", "S4", "S5"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(daily_max=2),
    )

    await sched._run_round()

    assert svc.calls == ["S1", "S2"]
    # 超预算品种连 bar 取数都不做
    assert builder.symbols == ["S1", "S2"]
    status = sched.status()
    assert status["attempts_today"] == 2
    assert status["last_round"]["outcomes"] == {"ok": 2, "skipped": 0, "failed": 0}


@pytest.mark.asyncio
async def test_failed_attempt_still_consumes_budget(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """失败的调用也已经付过钱 → 按尝试计,不按成功计。"""
    builder = _FakeInputBuilder()
    svc = _FakeService(fail_on={"S1"})
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["S1", "S2"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(daily_max=1),
    )

    await sched._run_round()

    assert svc.calls == ["S1"]  # S2 被预算挡住
    status = sched.status()
    assert status["attempts_today"] == 1
    assert status["last_round"]["outcomes"] == {"ok": 0, "skipped": 0, "failed": 1}


@pytest.mark.asyncio
async def test_budget_resets_on_new_day(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["S1", "S2"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(daily_max=5),
    )
    sched._budget_day = "1999-01-01"
    sched._attempts_today = 99

    await sched._run_round()

    assert svc.calls == ["S1", "S2"]
    assert sched.status()["attempts_today"] == 2


@pytest.mark.asyncio
async def test_usage_logged_per_diagnosis(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    builder = _FakeInputBuilder()
    svc = _FakeService()
    svc.usage = {
        "BTC/USDT": {
            "prompt_tokens": 1234,
            "completion_tokens": 56,
            "total_tokens": 1290,
        }
    }
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    with caplog.at_level(logging.INFO, logger="app.core.pa_heartbeat"):
        await sched._run_round()

    assert "prompt_tokens=1234" in caplog.text
    assert "completion_tokens=56" in caplog.text


@pytest.mark.asyncio
async def test_missing_usage_total_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder = _FakeInputBuilder()
    svc = _NoUsageService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert svc.calls == 1
    assert sched.status()["last_round"]["outcomes"]["ok"] == 1


# ---------------------------------------------------------------------------
# ④ klines 新鲜度前置
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsynced_symbol_skipped_round_continues(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder = _FakeInputBuilder(missing={"ETH/USDT"})
    svc = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"], service=svc, klines_db=klines_db
    )

    await sched._run_round()

    assert svc.calls == ["BTC/USDT", "SOL/USDT"]
    assert sched.status()["last_round"]["outcomes"] == {
        "ok": 2,
        "skipped": 1,
        "failed": 0,
    }
    # 跳过的品种没花 LLM 钱 → 不占预算
    assert sched.status()["attempts_today"] == 2


@pytest.mark.asyncio
async def test_missing_klines_db_skips_whole_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=tmp_path / "nope.db")

    await sched._run_round()  # 不应抛

    assert svc.calls == []
    assert builder.calls == []
    assert sched.status()["last_round"] is None


@pytest.mark.asyncio
async def test_no_favorites_is_noop(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler([], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert svc.calls == []
    assert sched.status()["last_round"] is None


@pytest.mark.asyncio
async def test_unreadable_favorites_skips_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        service=svc, settings=_FakeSettings(raise_on_read=True), klines_db=klines_db
    )

    await sched._run_round()  # 不应抛

    assert svc.calls == []


def test_settings_loader_none_yields_empty_watchlist() -> None:
    sched = _scheduler(settings_loader=lambda: None)
    assert sched._watchlist() == []


@pytest.mark.asyncio
async def test_service_unavailable_skips_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder = _FakeInputBuilder()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service_loader=lambda: None, klines_db=klines_db)

    await sched._run_round()  # 不应抛

    assert builder.calls == []
    assert sched.status()["last_round"] is None


# --- ④b 末根 bar 超龄守卫(3×interval 容差) -------------------------------


@pytest.mark.asyncio
async def test_stale_last_bar_skips_symbol_free(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """末根 bar 老于 3×interval → skip;不烧预算(try 之前拦截)。"""
    three_weeks_ms = 3 * 7 * 24 * 3600 * 1000
    builder = _FakeInputBuilder(stale={"BTC/USDT": time.time() * 1000 - three_weeks_ms})
    svc = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    with caplog.at_level(logging.WARNING, logger="app.core.pa_heartbeat"):
        await sched._run_round()

    assert svc.calls == []
    assert sched.status()["last_round"]["outcomes"]["skipped"] == 1
    assert sched.status()["attempts_today"] == 0  # 不烧预算
    assert any("stale bars" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_fresh_last_bar_diagnoses(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """末根 bar = 现在 → 正常诊断(守卫不误伤)。"""
    builder = _FakeInputBuilder(stale={"BTC/USDT": time.time() * 1000})
    svc = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert svc.calls == ["BTC/USDT"]
    assert sched.status()["last_round"]["outcomes"]["ok"] == 1


@pytest.mark.asyncio
async def test_staleness_boundary_passes_inside_tolerance(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """恰好 2h(容差 3h 内)→ 放行;边界另一侧(4h)→ skip。1h interval。"""
    now_ms = time.time() * 1000
    builder = _FakeInputBuilder(stale={"BTC/USDT": now_ms - 2 * 3600 * 1000})
    svc = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)
    await sched._run_round()
    assert svc.calls == ["BTC/USDT"]  # 2h < 3h → 诊断

    builder2 = _FakeInputBuilder(stale={"ETH/USDT": now_ms - 4 * 3600 * 1000})
    svc2 = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder2)
    sched2 = _scheduler(["ETH/USDT"], service=svc2, klines_db=klines_db)
    await sched2._run_round()
    assert svc2.calls == []  # 4h > 3h → skip


@pytest.mark.asyncio
async def test_stale_symbol_does_not_block_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """一个品种陈旧,其余照常诊断(单品种 skip 不中止整轮)。"""
    three_weeks_ms = 3 * 7 * 24 * 3600 * 1000
    builder = _FakeInputBuilder(stale={"ETH/USDT": time.time() * 1000 - three_weeks_ms})
    svc = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"], service=svc, klines_db=klines_db
    )

    await sched._run_round()

    assert svc.calls == ["BTC/USDT", "SOL/USDT"]
    assert sched.status()["last_round"]["outcomes"] == {
        "ok": 2,
        "skipped": 1,
        "failed": 0,
    }


@pytest.mark.asyncio
async def test_unknown_interval_degrades_to_warning_not_block(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """interval 不在表里(如 '2m30s')→ 降级 warning,照常诊断(不拦)。"""
    builder = _FakeInputBuilder()  # fetched_at = 现在
    svc = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(interval="2m30s"),
    )

    with caplog.at_level(logging.WARNING, logger="app.core.pa_heartbeat"):
        await sched._run_round()

    assert svc.calls == ["BTC/USDT"]  # 降级不拦
    assert any("staleness table" in r.message for r in caplog.records)


# --- ④c 轮内逐品种重同步(③b) ----------------------------------------------


class _FakeSyncManager:
    """``sync_incremental`` 替身:记录调用契约 + 可注入失败 interval。"""

    def __init__(self, *, fail_intervals: set[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail_intervals or set()

    async def sync_incremental(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        days: int = 365,
    ) -> int:
        self.calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "interval": interval,
                "days": days,
            }
        )
        if interval in self._fail:
            raise RuntimeError("binance 599")
        return 42


@pytest.mark.asyncio
async def test_resync_runs_before_diagnosis_with_primary_and_htf(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """同步在诊断前,每品种只打 binance×config.market_type 的 主+HTF 两 interval。"""
    builder = _FakeInputBuilder()  # fetched_at = 现在 → 守卫放行
    svc = _FakeService()
    mgr = _FakeSyncManager()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT"], service=svc, klines_db=klines_db, sync_loader=lambda: mgr
    )

    await sched._run_round()

    assert svc.calls == ["BTC/USDT"]  # 同步后照常诊断
    assert [(c["exchange"], c["market_type"], c["interval"]) for c in mgr.calls] == [
        ("binance", "futures", "1h"),
        ("binance", "futures", "4h"),
    ]
    assert all(c["days"] == hb._RESYNC_DAYS for c in mgr.calls)


@pytest.mark.asyncio
async def test_resync_failure_degrades_not_blocks(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """sync 抛异常 → warning,流程继续(守卫/诊断照走)。"""
    builder = _FakeInputBuilder()
    svc = _FakeService()
    mgr = _FakeSyncManager(fail_intervals={"1h", "4h"})
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT"], service=svc, klines_db=klines_db, sync_loader=lambda: mgr
    )

    with caplog.at_level(logging.WARNING, logger="app.core.pa_heartbeat"):
        await sched._run_round()

    assert svc.calls == ["BTC/USDT"]  # 降级不拦
    assert any("resync failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_resync_loader_none_skips_sync_silently(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """manager 为 None → 直接跳过同步(如启动顺序里 kline manager 还没就绪)。"""
    builder = _FakeInputBuilder()
    svc = _FakeService()
    mgr = _FakeSyncManager()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT"], service=svc, klines_db=klines_db, sync_loader=lambda: None
    )

    await sched._run_round()

    assert mgr.calls == []
    assert svc.calls == ["BTC/USDT"]


@pytest.mark.asyncio
async def test_no_sync_loader_by_default(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """不传 sync_loader(如测试/独立脚本)→ 无同步行为,不炸。"""
    builder = _FakeInputBuilder()
    svc = _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()  # 不应抛

    assert svc.calls == ["BTC/USDT"]


@pytest.mark.asyncio
async def test_stale_after_failed_resync_still_skipped(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """同步失败 + 数据仍陈旧 → 守卫依然拦(两道闸独立,后一道不因前一道
    失败而放水)。"""
    three_weeks_ms = 3 * 7 * 24 * 3600 * 1000
    builder = _FakeInputBuilder(stale={"BTC/USDT": time.time() * 1000 - three_weeks_ms})
    svc = _FakeService()
    mgr = _FakeSyncManager(fail_intervals={"1h", "4h"})
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT"], service=svc, klines_db=klines_db, sync_loader=lambda: mgr
    )

    await sched._run_round()

    assert svc.calls == []  # 守卫拦下
    assert sched.status()["attempts_today"] == 0


# ---------------------------------------------------------------------------
# ⑤ demo 短路
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_mode_short_circuits_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    monkeypatch.setenv("SELENE_PA_DEMO_MODE", "1")
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert svc.calls == []
    assert builder.calls == []
    assert sched.status()["attempts_today"] == 0


# ---------------------------------------------------------------------------
# ⑥ 单品种失败不中止整轮
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_symbol_failure_does_not_abort_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder = _FakeInputBuilder()
    svc = _FakeService(fail_on={"BTC/USDT"})
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"], service=svc, klines_db=klines_db
    )

    await sched._run_round()

    assert svc.calls == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert sched.status()["last_round"]["outcomes"] == {
        "ok": 2,
        "skipped": 0,
        "failed": 1,
    }


# ---------------------------------------------------------------------------
# 调度:tick 触发 + busy guard + 生命周期
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_ignores_other_hours(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    hour = (datetime.datetime.now(datetime.timezone.utc).hour + 1) % 24
    sched = _scheduler(
        ["BTC/USDT"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(hour_utc=hour),
    )

    await sched._tick()

    assert svc.calls == []
    assert sched.status()["last_round"] is None


@pytest.mark.asyncio
async def test_tick_runs_round_at_configured_hour(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    hour = datetime.datetime.now(datetime.timezone.utc).hour
    sched = _scheduler(
        ["BTC/USDT"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(hour_utc=hour),
    )

    await sched._tick()

    if sched.status()["last_round"] is None:
        pytest.skip("UTC hour rolled over mid-test")
    assert svc.calls == ["BTC/USDT"]


@pytest.mark.asyncio
async def test_tick_claims_day_before_awaiting(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """当天已认领 → 后续 tick 不再触发(长轮次不会被每分钟重入)。"""
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    today = datetime.datetime.now(datetime.timezone.utc)
    sched = _scheduler(
        ["BTC/USDT"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(hour_utc=today.hour),
    )
    sched._last_day = today.strftime("%Y-%m-%d")

    await sched._tick()

    assert svc.calls == []


@pytest.mark.asyncio
async def test_busy_guard_prevents_reentry(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)
    sched._busy = True

    await sched._run_round()

    assert svc.calls == []


@pytest.mark.asyncio
async def test_busy_cleared_after_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert sched.status()["busy"] is False


@pytest.mark.asyncio
async def test_round_body_error_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """整轮体异常被吞(记日志),不打断主循环,且 busy 归还。"""
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    async def _boom() -> None:
        raise RuntimeError("round body exploded")

    monkeypatch.setattr(sched, "_round_body", _boom)

    await sched._run_round()  # 不应抛

    assert sched.status()["busy"] is False


@pytest.mark.asyncio
async def test_start_stop_lifecycle(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    builder, svc = _FakeInputBuilder(), _FakeService()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    hour = (datetime.datetime.now(datetime.timezone.utc).hour + 1) % 24
    sched = _scheduler(
        ["BTC/USDT"],
        service=svc,
        klines_db=klines_db,
        config=hb.PaHeartbeatConfig(hour_utc=hour, tick_seconds=30.0),
    )

    await sched.start()
    await sched.start()  # 幂等
    assert sched._task is not None
    assert sched._task.done() is False

    await sched.stop()
    assert sched._task is None
    assert svc.calls == []


@pytest.mark.asyncio
async def test_stop_without_start_is_safe() -> None:
    sched = _scheduler([])
    await sched.stop()  # 不应抛
    assert sched._task is None


# ---------------------------------------------------------------------------
# T3a — outcome 回看回填(接线层)
# ---------------------------------------------------------------------------


class _FakeRepo:
    """``_get_record_repo()`` 返回的 repo 替身:list_records 可注入记录,
    append_payload 记录调用并可注入失败/竞态。"""

    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        *,
        list_raises: bool = False,
        attach_result: bool = True,
        attach_raises: bool = False,
    ) -> None:
        self.records = records or []
        self.attach_calls: list[tuple[str, str, dict[str, Any]]] = []
        self._list_raises = list_raises
        self._attach_result = attach_result
        self._attach_raises = attach_raises

    async def list_records(
        self, symbol: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        if self._list_raises:
            raise RuntimeError("db gone")
        return [r for r in self.records if symbol is None or r.get("_symbol") == symbol]

    async def append_payload(
        self, input_id: str, key: str, value: dict[str, Any]
    ) -> bool:
        self.attach_calls.append((input_id, key, value))
        if self._attach_raises:
            raise RuntimeError("lock contention")
        return self._attach_result


class _OutcomeFakeService(_FakeService):
    """``_FakeService`` + ``_get_record_repo()``(心跳经 svc 取 repo)。"""

    def __init__(self, repo: _FakeRepo | None) -> None:
        super().__init__()
        self._repo = repo

    async def _get_record_repo(self) -> Any:
        return self._repo


_OUTCOME_ORDER = {
    "direction": "long",
    "entry": 100.0,
    "stop_loss": 95.0,
    "take_profit": 110.0,
    "type": "limit",
}


def _outcome_record(
    input_id: str, *, symbol: str = "BTC/USDT", outcome: Any = None
) -> dict[str, Any]:
    rec = {
        "meta": {
            "input_id": input_id,
            "session_id": "s",
            # 2020-01-01 早于任何合成窗口 → 默认触发 honest-refusal;
            # 需要真判定的测试自行覆写这个时戳。
            "timestamp": "2020-01-01T00:00:00+00:00",
        },
        "input_data": {"symbol": symbol, "interval": "1h"},
        "stage2": {
            "decision": "limit",
            "diagnosis_or_decision": {"decision": "limit", "order": _OUTCOME_ORDER},
        },
        "_symbol": symbol,
    }
    if outcome is not None:
        rec["outcome"] = outcome
    return rec


def _bar0_minus_1s_iso(t0: int) -> str:
    """决策时戳 = 首根 bar 之前 1 秒(决策后首根 = bar 0)。"""
    return (
        datetime.datetime.fromtimestamp(t0 / 1000.0, tz=datetime.timezone.utc)
        - datetime.timedelta(seconds=1)
    ).isoformat()


@pytest.mark.asyncio
async def test_outcome_review_backfills_win_and_costs_no_budget(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """上轮 trade 决策 + 已收敛 bars → outcome 回填;零 LLM 预算消耗。

    真实 klines.db 由 review 侧 load_bars 读——此处用 sqlite 造 120 根
    收敛序列(决策后 entry 触发 → TP 触达)。
    """
    import sqlite3

    conn = sqlite3.connect(klines_db)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, exchange TEXT,"
        " market_type TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL)"
    )
    t0 = 1_700_000_000_000
    rows = []
    for i in range(120):
        # 决策在 bar 0;bar 1 触 entry(低 99 ≤ 100),bar 2 触 TP(高 111 ≥ 110)。
        high, low = (105.0, 101.0) if i == 1 else (111.0, 99.0)
        rows.append(
            (
                "BTC/USDT",
                "1h",
                "binance",
                "futures",
                t0 + i * 3_600_000,
                103.0,
                high,
                low,
                103.0,
                10.0,
            )
        )
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    rec = _outcome_record("prev-1")
    rec["meta"]["timestamp"] = _bar0_minus_1s_iso(t0)  # 决策后首根 = bar 0
    repo = _FakeRepo([rec])
    svc = _OutcomeFakeService(repo)
    builder = _FakeInputBuilder()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    # 回填发生了:append_payload 收到 outcome 键
    assert len(repo.attach_calls) == 1
    input_id, key, payload = repo.attach_calls[0]
    assert input_id == "prev-1"
    assert key == "outcome"
    assert payload["status"] == "win"
    assert payload["source"] == "heartbeat_review"
    assert "closed_at" in payload
    # 零预算:outcome 判定不占 attempt 计数(仅诊断的 1 次)
    assert sched.status()["attempts_today"] == 1
    # 诊断照常
    assert svc.calls == ["BTC/USDT"]


@pytest.mark.asyncio
async def test_outcome_review_without_prior_trade_skips_silently(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """m1 具名验收:上轮无 trade 决策(全是 no_trade)→ 静默跳过,零回填。"""
    repo = _FakeRepo([])  # 无任何记录
    svc = _OutcomeFakeService(repo)
    builder = _FakeInputBuilder()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert repo.attach_calls == []
    assert svc.calls == ["BTC/USDT"]  # 诊断照常


@pytest.mark.asyncio
async def test_outcome_review_still_open_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """上轮决策未收敛(窗口未走完)→ still_open 不写,下轮再看。"""
    import sqlite3

    conn = sqlite3.connect(klines_db)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, exchange TEXT,"
        " market_type TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL)"
    )
    t0 = 1_700_000_000_000
    rows = []
    for i in range(120):
        # 决策在末根;之后无新 bar → start 无 → honest refusal
        high, low = 105.0, 101.0
        rows.append(
            (
                "BTC/USDT",
                "1h",
                "binance",
                "futures",
                t0 + i * 3_600_000,
                103.0,
                high,
                low,
                103.0,
                10.0,
            )
        )
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    # 决策时戳 = 末根之后 → bars 里没有"决策之后"的 bar → None(缺口期)
    rec = _outcome_record("prev-2")
    rec["meta"]["timestamp"] = (
        datetime.datetime.fromtimestamp(
            (t0 + 119 * 3_600_000) / 1000.0, tz=datetime.timezone.utc
        )
        + datetime.timedelta(seconds=1)
    ).isoformat()
    repo = _FakeRepo([rec])
    svc = _OutcomeFakeService(repo)
    builder = _FakeInputBuilder()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert repo.attach_calls == []  # 缺口期不误标,零写入
    assert svc.calls == ["BTC/USDT"]


@pytest.mark.asyncio
async def test_outcome_review_attach_race_does_not_break_round(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """乐观锁竞态(append_payload False)/异常 → 整轮照常,不重试。"""
    import sqlite3

    conn = sqlite3.connect(klines_db)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, exchange TEXT,"
        " market_type TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL)"
    )
    t0 = 1_700_000_000_000
    rows = []
    for i in range(120):
        high, low = (105.0, 101.0) if i == 1 else (111.0, 99.0)
        rows.append(
            (
                "BTC/USDT",
                "1h",
                "binance",
                "futures",
                t0 + i * 3_600_000,
                103.0,
                high,
                low,
                103.0,
                10.0,
            )
        )
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    # 竞态:attach 返回 False
    rec = _outcome_record("prev-1")
    rec["meta"]["timestamp"] = _bar0_minus_1s_iso(t0)
    repo = _FakeRepo([rec], attach_result=False)
    svc = _OutcomeFakeService(repo)
    builder = _FakeInputBuilder()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()  # 不应抛

    assert svc.calls == ["BTC/USDT"]
    assert sched.status()["last_round"]["outcomes"]["ok"] == 1


@pytest.mark.asyncio
async def test_outcome_review_timeout_open_terminal(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """entry 触发但双未触且窗口走完 → timeout_open 终态,不写 pnl_r。"""
    import sqlite3

    conn = sqlite3.connect(klines_db)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, exchange TEXT,"
        " market_type TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL)"
    )
    t0 = 1_700_000_000_000
    rows = []
    for i in range(120):
        # 决策在 bar 0 收盘;bar 1 触 entry(101..105 含 100);此后 106..109
        # 收敛带永不触 95/110。
        if i == 0:
            high, low = 102.0, 98.0
        elif i == 1:
            high, low = 105.0, 99.0
        else:
            high, low = 109.0, 106.0
        rows.append(
            (
                "BTC/USDT",
                "1h",
                "binance",
                "futures",
                t0 + i * 3_600_000,
                103.0,
                high,
                low,
                103.0,
                10.0,
            )
        )
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    # 决策在首根 bar 之前 1 秒 → start=0(首根即决策后第一根)
    rec = _outcome_record("prev-3")
    rec["meta"]["timestamp"] = _bar0_minus_1s_iso(t0)

    repo = _FakeRepo([rec])
    svc = _OutcomeFakeService(repo)
    builder = _FakeInputBuilder()
    monkeypatch.setattr(hb, "build_trading_input", builder)
    sched = _scheduler(["BTC/USDT"], service=svc, klines_db=klines_db)

    await sched._run_round()

    assert len(repo.attach_calls) == 1
    _, key, payload = repo.attach_calls[0]
    assert key == "outcome"
    assert payload["status"] == "timeout_open"
    assert payload["pnl_r"] is None
    assert payload["entry_triggered"] is True


def test_status_shape() -> None:
    sched = _scheduler([])
    status = sched.status()
    assert set(status) == {
        "last_round",
        "attempts_today",
        "budget_day",
        "busy",
        "next_fire_at",
        "config",
    }
    assert status["config"]["daily_max"] == hb._DEFAULT_DAILY_MAX
    assert status["config"]["htf_interval"] == hb._DEFAULT_HTF_INTERVAL


# ---------------------------------------------------------------------------
# 接线层(源码结构断言):门控关 = 零行为
# ---------------------------------------------------------------------------
#
# 单测钉住了 scheduler 自身的行为,但"默认关"这一条最终由 main.py 的
# `if heartbeat_enabled():` 兜底 —— 装配点被挪出门外,门控就形同虚设。
# 用 AST 而非子串,才能区分"提到过"与"真的在门内"。


def _main_tree() -> ast.Module:
    return ast.parse(_MAIN_PY.read_text(encoding="utf-8"))


def test_main_py_constructs_heartbeat_only_inside_gate() -> None:
    tree = _main_tree()
    gates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "heartbeat_enabled"
    ]
    assert gates, "main.py 找不到 `if heartbeat_enabled():` 门控"

    guarded = {
        n.func.id
        for gate in gates
        for n in ast.walk(gate)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "PaHeartbeatScheduler" in guarded, "心跳构造被挪到了门控之外"

    # 全文件只允许一个构造点,否则门控只挡住其中一个
    constructions = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "PaHeartbeatScheduler"
    ]
    assert len(constructions) == 1


def test_main_py_shutdown_stops_heartbeat() -> None:
    """shutdown 必须停心跳 —— 且要早于 DB 句柄关闭(否则在飞轮次写向已拆的库)。"""
    tree = _main_tree()
    stops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "stop"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_pa_heartbeat"
    ]
    assert stops, "main.py shutdown 没有停止 PA 心跳"

    # 停止块自身就在 shutdown 段内:锚定它前面的 state 取回
    source = _MAIN_PY.read_text(encoding="utf-8")
    assert 'state.get("_pa_heartbeat")' in source

    # 必须在 strategy-runner 停摆之前(该段之后 DB 句柄开始关闭)
    stop_line = min(node.lineno for node in stops)
    runner_lines = [
        i
        for i, line in enumerate(source.splitlines(), start=1)
        if "Stop strategy runner" in line
    ]
    assert runner_lines, "找不到 shutdown 段锚点,断言失效"
    assert stop_line < runner_lines[0]


# ---------------------------------------------------------------------------
# T4 mission sensor 接线:成功诊断投影 Mission(零 LLM、零 attempt)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mission_sensor_invoked_only_for_successful_diagnosis(
    monkeypatch: pytest.MonkeyPatch, klines_db: Any
) -> None:
    """成功诊断 → sensor 投影 Mission;failed/skipped 不投;投影不占 attempt 预算。"""
    calls: list[dict[str, Any]] = []

    async def _fake_sync(**kwargs: Any) -> str:
        calls.append(kwargs)
        return f"mis-fake-{len(calls)}"

    builder = _FakeInputBuilder(missing={"SOL/USDT"})
    svc = _FakeService(fail_on={"ETH/USDT"})
    monkeypatch.setattr(hb, "build_trading_input", builder)
    # _diagnose_one 在成功路径的 try 里 call-time import,monkeypatch 模块属性即被读到
    monkeypatch.setattr(
        "app.core.pa_mission_sensor.sync_diagnosis_to_mission", _fake_sync
    )
    sched = _scheduler(
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"], service=svc, klines_db=klines_db
    )

    await sched._run_round()

    assert sched.status()["last_round"]["outcomes"] == {
        "ok": 1,
        "skipped": 1,
        "failed": 1,
    }
    # BTC(ok)+ETH(failed) 在 counter 之后各自 +1;SOL 在 input 阶段就 skip → 0
    assert sched.status()["attempts_today"] == 2
    # 只有成功诊断投影;载荷携带 v009 行键(input_id)+ 配置上下文
    assert len(calls) == 1
    assert calls[0]["symbol"] == "BTC/USDT"
    payload = calls[0]["diagnosis"]
    assert payload["record_id"]
    assert payload["interval"] == "1h"
    assert "trading_stance" in payload  # 配置透传(即使默认 None 也带键)


# ---------------------------------------------------------------------------
# next_fire_at —— 诚实推导
#
# 调度器每天 UTC ``hour_utc`` 触发一次(pa_heartbeat.py:258-265),不是滚动间隔。
# 任何从 tick 周期 / 陈旧度容忍度推出的值都会印出一个调度器不会兑现的时间。
# ---------------------------------------------------------------------------


def _at(hour: int, minute: int = 0, *, day: int = 14) -> datetime.datetime:
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=datetime.timezone.utc)


def test_next_fire_at_today_when_the_hour_is_still_ahead():
    got = hb._next_fire_at(_at(10, 30), 14, already_fired_today=False)
    assert got == _at(14).timestamp()


def test_next_fire_at_tomorrow_when_the_hour_has_passed():
    got = hb._next_fire_at(_at(15), 14, already_fired_today=False)
    assert got == _at(14, day=15).timestamp()


def test_next_fire_at_open_window_not_yet_fired_stays_on_the_open_hour():
    # 14 点窗口已开、今天还没跑 → 下一 tick 就触发,说"明天"会悲观一整天
    got = hb._next_fire_at(_at(14, 30), 14, already_fired_today=False)
    assert got == _at(14).timestamp()
    assert got <= _at(14, 30).timestamp()


def test_next_fire_at_open_window_already_fired_rolls_to_tomorrow():
    got = hb._next_fire_at(_at(14, 30), 14, already_fired_today=True)
    assert got == _at(14, day=15).timestamp()


def test_status_next_fire_at_lands_on_the_configured_hour():
    """status() 投影的值由 config.hour_utc 决定,与当前时刻无关。"""
    sched = hb.PaHeartbeatScheduler(
        lambda: None, lambda: None, config=hb.PaHeartbeatConfig(hour_utc=13)
    )
    got = sched.status()["next_fire_at"]
    nxt = datetime.datetime.fromtimestamp(got, tz=datetime.timezone.utc)
    assert (nxt.hour, nxt.minute, nxt.second) == (13, 0, 0)


def test_status_next_fire_at_counts_todays_round_as_fired():
    """今天这轮跑过(_last_day == today)→ 下一窗口是明天,不是已过去的今天。

    触发小时钉在当前 UTC 小时,才复现得了"已跑过"这一状态 —— 调度器只在
    ``now.hour == hour_utc`` 时开跑,所以刚跑完的窗口此刻必然是开着的。
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    sched = hb.PaHeartbeatScheduler(
        lambda: None, lambda: None, config=hb.PaHeartbeatConfig(hour_utc=now.hour)
    )
    sched._last_day = now.strftime("%Y-%m-%d")

    got = sched.status()["next_fire_at"]

    target = now.replace(hour=now.hour, minute=0, second=0, microsecond=0)
    assert got > target.timestamp()  # 不是今天那个已经过去的窗口
    assert got == target.timestamp() + 86_400  # 恰好是明天同一小时
