"""tuner OOS 防过拟合硬门的纯函数测试。

只测 _oos_gate,不跑 Optuna(参照 feedback_jsdom-heavy-chart-pure-helper 同思路:
重型逻辑抽纯函数,测试打纯函数)。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from llm_param_tuner import _oos_gate


def _rec(best: float, oos: float | None) -> dict:
    return {"best_sharpe": best, "oos_sharpe": oos}


def test_oos_gate_ratio_reject():
    # OOS 衰减到 20%(0.4/2.0),阈值 0.3 → 踢出
    assert _oos_gate(_rec(2.0, 0.4), min_ratio=0.3, min_abs=None) is True


def test_oos_gate_absolute_reject():
    # 绝对门:oos=0.1 < 0.2 → 踢出(即使比值没超)
    assert _oos_gate(_rec(2.0, 0.1), min_ratio=0.0, min_abs=0.2) is True


def test_oos_gate_pass():
    # 比值 0.75 >= 0.3,绝对门关 → 留
    assert _oos_gate(_rec(2.0, 1.5), min_ratio=0.3, min_abs=None) is False


def test_oos_gate_off_by_default():
    # 默认阈值(ratio=0, abs=None) → 永不踢
    assert _oos_gate(_rec(2.0, 0.0), min_ratio=0.0, min_abs=None) is False
    assert _oos_gate(_rec(2.0, -1.0), min_ratio=0.0, min_abs=None) is False


def test_oos_gate_none_passes():
    # oos_sharpe=None(数据过短) → 不受门影响
    assert _oos_gate(_rec(2.0, None), min_ratio=0.3, min_abs=0.2) is False


def test_oos_gate_negative_is_falls_to_absolute():
    # IS<=0 时比值无意义,退化按绝对门:abs 关 → 留;abs 开且 oos 低 → 踢
    assert _oos_gate(_rec(-0.5, 0.1), min_ratio=0.3, min_abs=None) is False
    assert _oos_gate(_rec(-0.5, 0.1), min_ratio=0.3, min_abs=0.2) is True
