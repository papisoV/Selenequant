"""selene-space-ashare-select — A股全市场日K同步 + 可回测选股模板.

吸收自 public-reference (<local-path-redacted> 2026-09-04 深度调查结论.
抄思路不抄实现: backfill 容错模式 / UNIQUE(symbol,date) schema / 横截面选股公式;
不复刻: baostock 全局 login hack / DELETE-append 竞态 / 无回测的推送皮囊.
"""

from selene_space_ashare_select.sync import AshareSelectSync

__all__ = ["AshareSelectSync"]
