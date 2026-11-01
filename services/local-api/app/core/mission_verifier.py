"""MissionVerifier:把"验证与反驳"从 LLM 的一段文字变成一次可复现的回测。

**为什么需要它。** ``mission_worker`` 原先只做一件事:把行情摘要喂给 LLM,
让它用 ``signal`` 描述"这批材料对支柱的支持程度"。实测 53 条分析全是
``untested`` —— 不是模型偷懒,是材料里根本没有可判定的量:日线涨跌对
"1h 上 half_delay=1 的衰减"这个问题不构成证据。没有实验,就没有反驳。

本模块补的是第三步(分析、验证与反驳)里缺的那一半:

1. **跑**:用真实本地 K 线跑一次回测(``BacktestEngine``),得到交易笔数、
   Sharpe、回撤、置换检验 p 值、bootstrap Sharpe 置信区间;
2. **判**:按**写死在此处的阈值**把结果映射成 ``SIGNALS`` 里的一个值。
   判定权在代码,不在模型 —— LLM 不参与"这个结果算不算支持";
3. **记**:落一行 ``research_experiments``(实验是否跑通)与一行
   ``mission_evidence``(证据说什么),并把数字写进 detail 供界面展示。

**门槛先声明,后执行。** 阈值全部是模块常量,出现在这里就意味着"跑之前
就定好了"。改阈值是一次代码变更,会留下 diff —— 这正是它该有的成本。
若把阈值交给 prompt,同一个结果换个措辞就能从"支持"变成"反驳"。

**样本不足就说不算。** 交易笔数 < ``MIN_TRADES``、置换 p 值 > ``MAX_P``、
或 bootstrap 置信区间跨零,一律 ``untested`` 而不是"弱支持"。把 18 笔交易
上的 Sharpe 2.2 说成证据,是这个模块最该拒绝的事。

零迁移:复用 v016 的 ``research_experiments`` / v019 的 ``mission_evidence``。
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from selene_space_core.kline_paths import default_klines_db_path
from selene_space_storage.repositories._mission_thesis_vocab import SIGNALS
from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_graph import (
    ExperimentRepository,
    HypothesisRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)

logger = logging.getLogger(__name__)

#: 低于这个交易笔数,任何 Sharpe 都不构成证据。回测里的 10 笔 ≈ 噪声。
MIN_TRADES = 20
#: 置换检验 p 值上限。高于它说明"交易顺序"本身就能解释这个 Sharpe。
MAX_PERMUTATION_P = 0.10
#: bootstrap Sharpe 置信区间下界必须为正,否则"边缘"与 0 不可区分。
MIN_CI_LOWER = 0.0
#: 一根 K 线最多取多少根。1h 的 20000 根 ≈ 2.3 年,足够覆盖多轮牛熊片段,
#: 又不至于让单次回测超过一分钟。
MAX_BARS = 20_000
#: 各周期默认取多少根(数据不足时按实际可得数量走,并如实标注)。
BARS_FOR_INTERVAL: dict[str, int] = {
    "1m": 5000,
    "5m": 5000,
    "15m": 8000,
    "30m": 8000,
    "1h": MAX_BARS,
    "4h": 8000,
    "8h": 5000,
    "12h": 5000,
    "1d": 2000,
}
#: mission 没声明策略时,用这个模板跑验证。选它是因为参数少、在所有周期上
#: 都能出信号;它的结论只对"这个模板"成立,不能外推到 mission 的原始命题。
DEFAULT_STRATEGY = "rsi_ma_delta"
#: 回测引擎的模板注册表(策略 id → 模块路径:类名)。
#: 与 ``app.api.v1.backtests._PRESET_STRATEGY_MODULES`` 同源,但只复制这里
#: 需要的一小撮,避免 core 层 import API 层(会形成循环)。
_PRESET_MODULES: dict[str, str] = {
    "bb_v3": "selene_space_strategy.templates.presets.bb_v3:BBV3Strategy",
    "bb_atr_trend": "selene_space_strategy.templates.presets.bb_atr_trend:BBATRTrendStrategy",
    "dmi_delta": "selene_space_strategy.templates.presets.dmi_delta:DMIDeltaStrategy",
    "ema_triple_cross": "selene_space_strategy.templates.presets.ema_triple_cross:EMATripleCrossStrategy",
    "keltner_v2": "selene_space_strategy.templates.presets.keltner_v2:KeltnerChannelV2Strategy",
    "rsi_ma_delta": "selene_space_strategy.templates.presets.rsi_ma_delta:RSIMADeltaStrategy",
    "rsi_momentum_continuation": (
        "selene_space_strategy.templates.presets.rsi_momentum_continuation:"
        "RSIMomentumContinuationStrategy"
    ),
    "tema_channel": "selene_space_strategy.templates.presets.tema_channel:TEMAChannelStrategy",
    "vw_ema_cross": "selene_space_strategy.templates.presets.vw_ema_cross:VWEMACrossoverStrategy",
    "dca_grid": "selene_space_strategy.templates.presets.dca_grid:DCAGridStrategy",
    "ma_extended_runs": (
        "selene_space_strategy.templates.presets.ma_extended_runs:"
        "MAExtendedRunsStrategy"
    ),
    "p_signal": "selene_space_strategy.templates.presets.p_signal:PSignalStrategy",
    "chop_guard": "selene_space_strategy.templates.presets.chop_guard:ChopGuardStrategy",
    "er_state_machine": (
        "selene_space_strategy.templates.presets.er_state_machine:"
        "ERStateMachineStrategy"
    ),
    "reversion_cost_guard": (
        "selene_space_strategy.templates.presets.reversion_cost_guard:"
        "ReversionCostGuardStrategy"
    ),
}

#: 自动生成的支柱声明 —— 只在 mission 没有任何 pillar 时用。
#: 它必须可证伪,所以写成一个能被上面阈值判定的陈述,而不是问题标题。
_AUTO_CLAIM = (
    "策略 {strategy} 在 {symbol} {interval} 上具有可持续交易的边缘"
    "(Sharpe 的 bootstrap 下界 > 0,且置换检验不能以随机顺序解释)"
)

RunFn = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class GateResult:
    """一项门槛的判定;``passed=False`` 时 ``reason`` 必须写清缺什么。"""

    name: str
    passed: bool
    reason: str
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
            "value": self.value,
        }


@dataclass(frozen=True)
class VerificationOutcome:
    """一次验证的完整结果,供 worker 写活动流与 evidence。"""

    mission_id: str
    ran: bool
    signal: str = "untested"
    reason: str = ""
    experiment_id: str | None = None
    evidence_id: str | None = None
    pillar_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    gates: tuple[GateResult, ...] = ()
    fact: str = ""
    interpretation: str = ""

    def to_detail(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "signal": self.signal,
            "reason": self.reason,
            "experiment_id": self.experiment_id,
            "evidence_id": self.evidence_id,
            "pillar_id": self.pillar_id,
            "metrics": self.metrics,
            "gates": [g.to_dict() for g in self.gates],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ms_to_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


def _bars_per_year(interval: str) -> int:
    """24/7 市场的年化根数。日线 365,小时线 8760。"""
    per_day = {
        "1m": 1440,
        "5m": 288,
        "15m": 96,
        "30m": 48,
        "1h": 24,
        "4h": 6,
        "8h": 3,
        "12h": 2,
        "1d": 1,
    }.get(interval)
    if per_day is None:
        return 252
    return int(per_day * 365)


def _normalize_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s:
        return ""
    return s if "/" in s else f"{s}/USDT"


async def _template_for_subject(db: Any, subject_id: str) -> str:
    """``strategies.id`` → 回测模板 id(``bb_v3`` 之类);解不出返回 ``""``。

    ``strategies`` 行里没有独立的 template 列 —— 模板身份的权威来源是
    ``config_json.source`` 的 ``template:<id>`` 前缀(回测路径自己也这么认,
    见 ``app/api/v1/backtests.py`` 的 ``original_template_id`` 推导)。
    ``config_json.type`` 是 ``StrategyType`` 枚举(``trend``/``custom``…),
    **不是**模板 id,拿它查 ``_PRESET_MODULES`` 必失败。
    """
    import json

    if not subject_id:
        return ""
    try:
        rows = await db.execute(
            "SELECT config_json FROM strategies WHERE id = ?", (subject_id,)
        )
    except Exception:  # noqa: BLE001 — 表不可读 = 解不出模板,按"不能验证"处理
        logger.warning("_template_for_subject: strategies lookup failed", exc_info=True)
        return ""
    if not rows:
        return ""
    try:
        cfg = json.loads(rows[0]["config_json"] or "{}")
    except (TypeError, ValueError):
        return ""
    source = str(cfg.get("source") or "")
    if source.startswith("template:"):
        candidate = source.split(":", 1)[1].strip()
        if candidate in _PRESET_MODULES:
            return candidate
    # 兜底:``id`` 本身可能就是模板名(strategies.id 由调用方决定)。
    if subject_id in _PRESET_MODULES:
        return subject_id
    return ""


async def resolve_verification_target(
    db: Any, mission: dict[str, Any], anchor: dict | None, pillars: list[dict[str, Any]]
) -> dict[str, Any]:
    """从 mission / anchor 里找出"用哪个策略、什么标的、什么周期"。

    **不猜策略。** mission context 里显式写了 ``strategy_id`` 才用;否则看
    anchor 的 ``strategy_ids``;两者都没有、且 mission 主体不是策略时,返回
    ``strategy_id=None`` —— 调用方据此跳过回测,而不是随便挑一个模板跑,
    然后把无关策略的结果当成这个问题的证据。

    ⚠️ ``subject_type='strategy'`` 时 ``subject_id`` **不能**直接当模板 id:
    它是 ``strategies.id``(库内主键,如 ``strat-xxxx``),而回测要的是
    ``_PRESET_MODULES`` 里的模板名(如 ``bb_v3``)。两者混用只会得到
    ``unknown preset strategy`` —— 见 ``_template_for_subject``。
    """
    ctx = mission.get("context") or {}
    anchor = anchor or {}
    strategy_id = str(ctx.get("strategy_id") or "").strip()
    if not strategy_id:
        sids = anchor.get("strategy_ids")
        if isinstance(sids, (list, tuple)) and sids:
            strategy_id = str(sids[0]).strip()
    if not strategy_id and mission.get("subject_type") == "strategy":
        strategy_id = await _template_for_subject(
            db, str(mission.get("subject_id") or "")
        )

    symbol = ""
    if mission.get("subject_type") == "strategy":
        symbol = str(anchor.get("symbols", [""])[0] if anchor.get("symbols") else "")
    elif mission.get("subject_type") == "market":
        symbol = str(mission.get("subject_id") or "")
    if not symbol:
        syms = anchor.get("symbols")
        if isinstance(syms, (list, tuple)) and syms:
            symbol = str(syms[0])
        elif isinstance(anchor.get("symbol"), str):
            symbol = str(anchor["symbol"])
    if not symbol:
        # 手建 mission 把标的写在 context 里(anchor 为空),见 worker 的
        # ``_resolve_anchor`` 对 ``context.interval`` 的同款处理。
        ctx_syms = ctx.get("symbols")
        if isinstance(ctx_syms, (list, tuple)) and ctx_syms:
            symbol = str(ctx_syms[0])
        elif isinstance(ctx.get("symbol"), str):
            symbol = str(ctx["symbol"])

    interval = ""
    raw_intervals = anchor.get("intervals")
    if isinstance(raw_intervals, (list, tuple)) and raw_intervals:
        interval = str(raw_intervals[0]).strip().lower()
    elif isinstance(raw_intervals, str):
        interval = raw_intervals.strip().lower()
    if not interval:
        ctx_interval = ctx.get("interval")
        if isinstance(ctx_interval, str) and ctx_interval.strip():
            interval = ctx_interval.strip().lower()
    if not interval:
        interval = "1h"

    return {
        "strategy_id": strategy_id or None,
        "symbol": _normalize_symbol(symbol),
        "interval": interval,
        "pillar_id": str(pillars[0]["id"]) if pillars else None,
    }


def _load_frame(
    symbol: str,
    interval: str,
    *,
    exchange: str = "binance",
    market_type: str = "futures",
) -> Any:
    """从本地 SQLite 取 K 线;取不到返回 ``None``(调用方记原因)。"""
    from selene_space_market.kline_db import KlineDB

    db = KlineDB(db_path=str(default_klines_db_path()))
    n = min(BARS_FOR_INTERVAL.get(interval, 2000), MAX_BARS)
    if not db.has_data(exchange, market_type, symbol, interval, min_candles=2):
        return None
    frame = db.get_klines(exchange, market_type, symbol, interval)
    if frame is None or len(frame) < 2:
        return None
    return frame.tail(n).reset_index(drop=True)


def _build_strategy(strategy_id: str, symbol: str, parameters: dict[str, Any] | None):
    """实例化内置模板策略;未知 id 抛 ``KeyError``(调用方转成"不能验证")。"""
    module_path = _PRESET_MODULES.get(strategy_id)
    if module_path is None:
        raise KeyError(f"unknown preset strategy: {strategy_id}")
    module_name, class_name = module_path.rsplit(":", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    sig = inspect.signature(cls.__init__)
    defaults: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname in (
            "self",
            "symbol",
            "initial_capital",
            "leverage",
            "fee_rate",
            "max_bars",
            "long_only",
        ):
            continue
        if param.default is not inspect.Parameter.empty:
            defaults[pname] = param.default
    merged = {**defaults, **(parameters or {})}
    return cls(symbol=symbol, initial_capital=10_000.0, **merged)


def run_backtest_sync(
    strategy_id: str,
    symbol: str,
    interval: str,
    parameters: dict[str, Any] | None = None,
    *,
    robustness: bool = True,
) -> dict[str, Any]:
    """跑一次真实回测,返回扁平化的结果字典。

    同步函数 —— 调用方必须用 ``asyncio.to_thread`` 包起来,否则会阻塞事件
    循环(实测 1h/18k 根约 30 秒)。

    ``robustness=True`` 会额外跑置换检验与 bootstrap(约 +10%),这两项正是
    "样本外能不能站住"的判据来源,不能省。
    """
    import pandas as pd  # noqa: F401  — 保证引擎依赖已加载

    from selene_space_backtest.commissions import PercentageCommission
    from selene_space_backtest.engine import BacktestConfig, BacktestEngine
    from selene_space_backtest.slippage import RealisticSlippage

    frame = _load_frame(symbol, interval)
    if frame is None:
        return {"ok": False, "error": "no_klines", "detail": f"{symbol} {interval}"}

    try:
        strategy = _build_strategy(strategy_id, symbol, parameters)
    except KeyError as exc:
        return {"ok": False, "error": "unknown_strategy", "detail": str(exc)}

    config = BacktestConfig(
        initial_cash=10_000.0,
        commission=PercentageCommission(rate=0.001),
        slippage=RealisticSlippage(),
        strategy_id=strategy_id,
        symbol=symbol,
        interval=interval,
        robustness=robustness,
        lookahead_guard=True,
    )
    engine = BacktestEngine(config=config)
    result = engine.run(strategy, frame)

    ts = frame["timestamp"]
    robustness_block = result.robustness or {}
    permutation = robustness_block.get("permutation") or {}
    bootstrap = robustness_block.get("bootstrap") or {}

    return {
        "ok": True,
        "strategy_id": strategy_id,
        "symbol": symbol,
        "interval": interval,
        "bars": int(len(frame)),
        "start": _ms_to_date(int(ts.iloc[0])),
        "end": _ms_to_date(int(ts.iloc[-1])),
        "trades": int(result.trades_count),
        "sharpe": float(result.metrics.sharpe_ratio),
        "total_return": float(result.metrics.total_return),
        "max_drawdown": float(result.metrics.max_drawdown),
        "win_rate": float(result.metrics.win_rate),
        "profit_factor": float(result.metrics.profit_factor),
        "lookahead_warnings": list(result.lookahead_warnings)[:5],
        "permutation_p": permutation.get("p_value_sharpe"),
        "permutation_error": permutation.get("error"),
        "ci_lower": bootstrap.get("ci_lower"),
        "ci_upper": bootstrap.get("ci_upper"),
        "prob_positive": bootstrap.get("prob_positive"),
        "bootstrap_error": bootstrap.get("error"),
    }


def evaluate_gates(metrics: dict[str, Any]) -> tuple[list[GateResult], str]:
    """按**预先声明**的阈值给回测结果打分,返回 (门槛们, signal)。

    判定顺序固定,先否决后分级:

    1. 笔数不足 → ``untested``(样本量是硬门,任何 Sharpe 都救不了)
    2. 置换检验不显著 → ``untested``(顺序就能解释,不是边缘)
    3. 置信区间跨零 → ``untested``(与 0 不可区分)
    4. 全部通过后按 Sharpe 分级

    返回的 signal 一定是 ``SIGNALS`` 里的合法值。
    """
    gates: list[GateResult] = []
    trades = int(metrics.get("trades") or 0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    p_value = metrics.get("permutation_p")
    ci_lower = metrics.get("ci_lower")

    sample_ok = trades >= MIN_TRADES
    gates.append(
        GateResult(
            "sample_size",
            sample_ok,
            f"{trades} 笔成交(下限 {MIN_TRADES})",
            float(trades),
        )
    )
    if not sample_ok:
        return gates, "untested"

    if p_value is None:
        gates.append(
            GateResult(
                "permutation",
                False,
                f"置换检验无法计算:{metrics.get('permutation_error') or '无结果'}",
            )
        )
        return gates, "untested"
    perm_ok = float(p_value) <= MAX_PERMUTATION_P
    gates.append(
        GateResult(
            "permutation",
            perm_ok,
            f"p={float(p_value):.3f}(上限 {MAX_PERMUTATION_P})",
            float(p_value),
        )
    )
    if not perm_ok:
        return gates, "untested"

    if ci_lower is None:
        gates.append(
            GateResult(
                "sharpe_ci",
                False,
                f"bootstrap 置信区间无法计算:{metrics.get('bootstrap_error') or '无结果'}",
            )
        )
        return gates, "untested"
    ci_ok = float(ci_lower) > MIN_CI_LOWER
    gates.append(
        GateResult(
            "sharpe_ci",
            ci_ok,
            f"Sharpe 置信下界 {float(ci_lower):.3f}(须 > {MIN_CI_LOWER})",
            float(ci_lower),
        )
    )
    if not ci_ok:
        return gates, "untested"

    # 三道门都过了 —— 现在才允许用 Sharpe 的**大小**分级。
    if sharpe >= 2.0:
        signal = "strongly_confirming"
    elif sharpe >= 1.0:
        signal = "confirming"
    elif sharpe > 0.0:
        signal = "mildly_confirming"
    elif sharpe > -0.5:
        signal = "mildly_weakening"
    elif sharpe > -1.0:
        signal = "weakening"
    else:
        signal = "invalidating"
    gates.append(GateResult("sharpe_level", True, f"Sharpe {sharpe:.3f}", sharpe))
    return gates, signal


def _hypothesis_status_for(signal: str) -> str | None:
    """证据 → 假设状态。``untested`` 不动状态(仍在 testing)。"""
    if signal in ("strongly_confirming", "confirming", "mildly_confirming"):
        return "supported"
    if signal in ("weakening", "strongly_weakening", "invalidating"):
        return "refuted"
    return None


def _render_fact(metrics: dict[str, Any]) -> str:
    return (
        f"回测 {metrics['strategy_id']} @ {metrics['symbol']} {metrics['interval']}: "
        f"{metrics['bars']} 根 K 线({metrics['start']}→{metrics['end']}),"
        f"{metrics['trades']} 笔成交,Sharpe {metrics['sharpe']:.3f},"
        f"总收益 {metrics['total_return']:+.2%},最大回撤 {metrics['max_drawdown']:.2%}"
        + (
            f",置换 p={float(metrics['permutation_p']):.3f}"
            if metrics.get("permutation_p") is not None
            else ""
        )
        + (
            f",Sharpe 95% CI [{float(metrics['ci_lower']):.2f}, "
            f"{float(metrics['ci_upper']):.2f}]"
            if metrics.get("ci_lower") is not None
            and metrics.get("ci_upper") is not None
            else ""
        )
    )


def _render_interpretation(gates: list[GateResult], signal: str) -> str:
    failed = [g for g in gates if not g.passed]
    if signal == "untested":
        why = ";".join(g.reason for g in failed) or "门槛不足"
        return (
            f"这组结果**不构成证据**:{why}。"
            "在样本量、置换检验与置信区间任一不达标时,任何 Sharpe 都不能用来"
            "支持或反驳支柱。"
        )
    passed = ";".join(g.reason for g in gates if g.passed)
    return (
        f"三道门槛全部通过({passed}),结果可视为该策略在此标的与周期上的"
        f"可复现表现;信号 {signal} 由阈值表确定,不由模型措辞决定。"
    )


async def verify_mission(
    db: Any,
    mission_id: str,
    *,
    strategy_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    run_fn: RunFn | None = None,
    anchor: dict | None = None,
) -> VerificationOutcome:
    """对一条 mission 跑一次可复现验证,写实验 + 证据。

    ``run_fn`` 可注入(测试用),签名为
    ``run_fn(strategy_id, symbol, interval, parameters) -> dict``;默认走
    ``run_backtest_sync`` 并在线程池里执行。

    任何一步失败都返回 ``ran=False`` 并写清原因,不抛异常 —— worker 的
    一次失败不该拖垮整轮扫描。
    """
    mission_repo = ResearchMissionRepository(db)
    mission = await mission_repo.get(mission_id)
    if mission is None:
        return VerificationOutcome(mission_id, False, reason="mission 不存在")

    thesis_repo = MissionThesisRepository(db)
    pillars = [
        p
        for p in await thesis_repo.list_pillars(mission_id)
        if p.get("superseded_by") is None and p.get("status") != "retired"
    ]

    if anchor is None:
        from app.core.mission_binding import mission_question_id

        question_id = await mission_question_id(db, mission)
        if question_id:
            from selene_space_storage.repositories.research_graph import (
                ResearchQuestionRepository,
            )

            question = await ResearchQuestionRepository(db).get(str(question_id))
            anchor = (question or {}).get("anchor")
        anchor = anchor or {}

    target = await resolve_verification_target(db, mission, anchor, pillars)
    chosen_strategy = strategy_id or target["strategy_id"]
    symbol = target["symbol"]
    interval = target["interval"]

    if not chosen_strategy:
        # 没有策略 ≠ 没有可判定性。PA 心跳诊断这类记录在
        # ``stage1.diagnosis_or_decision.next_period`` 里许下过方向承诺,
        # 可以对照之后真实收盘价结算 —— 这是第二条腿,不是"补一行 untested"。
        # 只有在记录确实带方向承诺时才走它;否则维持原来的早退。
        from app.core.counterfactual_settlement import settle_mission
        from app.core.mission_binding import mission_question_id

        question_id = await mission_question_id(db, mission)
        counterfactual = await settle_mission(
            db,
            mission,
            symbol=symbol,
            interval=interval,
            pillar_id=target["pillar_id"],
            question_id=question_id,
        )
        if counterfactual.ran:
            return VerificationOutcome(
                mission_id,
                True,
                signal=counterfactual.signal,
                reason=counterfactual.reason,
                evidence_id=counterfactual.evidence_id,
                pillar_id=counterfactual.pillar_id,
                metrics=counterfactual.verdict,
                fact=counterfactual.fact,
                interpretation=counterfactual.interpretation,
            )
        return VerificationOutcome(
            mission_id,
            False,
            reason=(
                "这条 mission 没有可执行的策略(既无 context.strategy_id,"
                "也无 anchor.strategy_ids);反事实结算也未成立:"
                f"{counterfactual.reason}"
            ),
        )
    if not symbol:
        return VerificationOutcome(
            mission_id, False, reason="这条 mission 没有可回测的标的"
        )

    pillar_id = target["pillar_id"]
    if pillar_id is None:
        # 没有支柱时自动开一条**可证伪**的:它描述的是"这个策略在此标的周期
        # 上有边缘",正好被下面的门槛直接判定。provenance=draft 表明它由
        # 系统生成、待人确认,不是人类提出的主张。
        claim = _AUTO_CLAIM.format(
            strategy=chosen_strategy, symbol=symbol, interval=interval
        )
        pillar_id = await thesis_repo.add_pillar(
            mission_id,
            claim,
            "core",
            confirm_threshold=(
                f"交易笔数 >= {MIN_TRADES} 且置换 p <= {MAX_PERMUTATION_P} "
                f"且 Sharpe CI 下界 > {MIN_CI_LOWER}"
            ),
            break_threshold="三项门槛任一不达标(视为不可判定,非反驳)",
            threshold_provenance="draft",
            provenance="draft",
            model_line="mission_verifier 自动生成",
        )

    # 实验行先建后跑:即便回测中途异常,也要留下"尝试过"的痕迹。
    experiment_repo = ExperimentRepository(db)
    experiment_id = await experiment_repo.create(
        "backtest",
        config={
            "strategy_id": chosen_strategy,
            "symbol": symbol,
            "interval": interval,
            "parameters": parameters or {},
            "source": "mission_verifier",
        },
    )

    if run_fn is None:

        async def _default_run(sid: str, sym: str, iv: str, params: dict | None):
            import asyncio

            return await asyncio.to_thread(run_backtest_sync, sid, sym, iv, params)

        run_fn = _default_run

    try:
        raw = await run_fn(chosen_strategy, symbol, interval, parameters or {})
    except Exception as exc:  # noqa: BLE001 — 回测失败是一条结果,不是崩溃
        logger.warning(
            "mission verifier backtest crashed for %s", mission_id, exc_info=True
        )
        await experiment_repo.set_outcome(experiment_id, "failed")
        return VerificationOutcome(
            mission_id,
            False,
            reason=f"回测异常:{type(exc).__name__}: {exc}"[:300],
            experiment_id=experiment_id,
            pillar_id=pillar_id,
        )

    if not raw.get("ok"):
        await experiment_repo.set_outcome(experiment_id, "failed")
        return VerificationOutcome(
            mission_id,
            False,
            reason=f"回测无法执行:{raw.get('error')} {raw.get('detail') or ''}".strip(),
            experiment_id=experiment_id,
            pillar_id=pillar_id,
        )

    await experiment_repo.mark_ran(experiment_id)
    await experiment_repo.set_outcome(experiment_id, "success")

    # 上下文字段由我们填 —— ``_render_fact`` 要它们,而注入的 ``run_fn``
    # (测试、未来的别的取数器)没有义务回传自己刚跑的是哪个策略/标的/周期。
    raw = {
        **raw,
        "strategy_id": raw.get("strategy_id") or chosen_strategy,
        "symbol": raw.get("symbol") or symbol,
        "interval": raw.get("interval") or interval,
    }

    gates, signal = evaluate_gates(raw)
    if signal not in SIGNALS:  # 防御:阈值表改错时不要写脏值
        signal = "untested"

    fact = _render_fact(raw)
    interpretation = _render_interpretation(gates, signal)
    evidence_id = await thesis_repo.append_evidence(
        mission_id,
        pillar_id,
        occurred_at=_now(),
        source_type="backtest",
        fact=fact,
        interpretation=interpretation,
        signal=signal,
        source_ref=f"experiment:{experiment_id}",
        follow_up=";".join(g.reason for g in gates if not g.passed) or None,
        owner="selene",
    )

    # 假设状态跟着证据走 —— 但 ``untested`` 不动状态,避免把"没测出来"
    # 记成"被反驳"。
    hypothesis_id = (mission.get("context") or {}).get("hypothesis_id")
    status = _hypothesis_status_for(signal)
    if hypothesis_id and status:
        try:
            await HypothesisRepository(db).set_status(str(hypothesis_id), status)
        except (KeyError, ValueError):
            logger.warning(
                "mission verifier could not update hypothesis %s", hypothesis_id
            )

    return VerificationOutcome(
        mission_id,
        True,
        signal=signal,
        reason=interpretation,
        experiment_id=experiment_id,
        evidence_id=evidence_id,
        pillar_id=pillar_id,
        metrics=raw,
        gates=tuple(gates),
        fact=fact,
        interpretation=interpretation,
    )


def smallest_sample_note(metrics: dict[str, Any]) -> str | None:
    """给界面的一句人话:这次验证的数据够不够支撑结论。

    数值本身不撒谎,但"Sharpe 2.2"单独出现会让人忘记它只建立在 4 个月
    数据上。把它写进证据旁边,而不是留给读者自己推断。
    """
    bars = int(metrics.get("bars") or 0)
    interval = str(metrics.get("interval") or "")
    if interval == "1d" and bars < 365:
        return f"仅 {bars} 根日线(不足一年),跨周期稳健性无法判断"
    if interval in ("1h", "4h") and bars < 8760:
        return f"仅 {bars} 根 {interval} K 线,样本长度有限"
    return None


__all__ = [
    "BARS_FOR_INTERVAL",
    "GateResult",
    "MAX_PERMUTATION_P",
    "MIN_TRADES",
    "VerificationOutcome",
    "evaluate_gates",
    "resolve_verification_target",
    "run_backtest_sync",
    "smallest_sample_note",
    "verify_mission",
]
