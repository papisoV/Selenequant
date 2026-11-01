"""fund-ingest 场内工具数据包 — 转债/LOF-ETF/REITs。

吸收自 <local-path-redacted> 调查）:
- 只吸收策略形态(LOF 折溢价监控/转债双低),不吸收代码本体(2021 风格)
- 数据源统一 akshare(接口探针实测 9/10 命中,覆盖 stock 项目的集思录/ttjj 等全部端点知识)
- 落地模式复刻 news-ingest(binance-quant L3 吸收先例)
"""

from selene_space_fund_ingest.config import FUND_DB_PATH

__all__ = ["FUND_DB_PATH"]
