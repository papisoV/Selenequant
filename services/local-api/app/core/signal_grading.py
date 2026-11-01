"""信号数分级(US-303):信号少 → 全市场粗筛;信号多 → 只盯票池。

思想来自 public-reference 选股系统的"信号数决定粗筛还是盯盘"(该项目无 LICENSE,
仅吸收思想,零代码搬运):资源分配跟着信号密度走。
"""

from __future__ import annotations

DEFAULT_SCAN_THRESHOLD = 20


def signal_grading(
    n_signals: int, *, scan_threshold: int = DEFAULT_SCAN_THRESHOLD
) -> str:
    """信号数 → 资源档位。

    ``n_signals < scan_threshold`` → ``full_scan``(信号稀缺,全市场粗筛);
    否则 → ``watchlist``(信号密集,只盯票池)。纯标注,不做任何交易决策。
    """
    if n_signals < 0:
        raise ValueError(f"n_signals must be non-negative: {n_signals!r}")
    if n_signals < scan_threshold:
        return "full_scan"
    return "watchlist"
