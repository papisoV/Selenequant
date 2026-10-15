"""A股全市场日K同步引擎.

吸收 public-reference data/engine.py 思路,重写关键缺陷:
- baostock login/logout 由 _BaostockSession 显式管理(修原版策略层裸 login 与多进程 worker 冲突)
- 增量写用 INSERT OR REPLACE(原版 DELETE-再-append 有查询窗口竞态)
- 复权参数化且默认 hfq 后复权(策略公式口径;stock 包 _fetch_baostock 写死不复权,不适用)
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    symbol   TEXT NOT NULL,
    date     TEXT NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    UNIQUE (symbol, date)
);
"""

_QUERY_FIELDS = "date,open,high,low,close,volume,amount"


def to_baostock_code(symbol: str) -> str:
    """6 位纯数字代码 → baostock 格式: 6/9 开头 sh, 其余 sz."""
    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
    return f"{prefix}.{symbol}"


def _ensure_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_SCHEMA_SQL)
        conn.commit()


class _BaostockSession:
    """baostock 会话: 显式 login, 幂等 logout."""

    def __init__(self) -> None:
        import baostock as bs

        self._bs = bs

    def login(self) -> bool:
        lg = self._bs.login()
        if lg.error_code != "0":
            logger.error("baostock 登录失败: %s", getattr(lg, "error_msg", ""))
            return False
        return True

    def logout(self) -> None:
        self._bs.logout()

    def query_daily(
        self, symbol: str, start: str, end: str, adjust: str
    ) -> list[list[str]]:
        adjustflag = {"hfq": "1", "qfq": "2"}.get(adjust, "3")
        rs = self._bs.query_history_k_data_plus(
            to_baostock_code(symbol),
            _QUERY_FIELDS,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag=adjustflag,
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query failed [{symbol}]: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return rows


def _rows_to_df(rows: list[list[str]], symbol: str):
    import pandas as pd

    df = pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "volume", "turnover"]
    )
    for col in ("open", "high", "low", "close", "volume", "turnover"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    df = df[df["volume"] > 0]
    df.insert(0, "symbol", symbol)
    return df


def _ak_all_symbols() -> list[str]:
    """全市场 A 股代码 (巨潮交易所名录, 不依赖东财)."""
    import akshare as ak

    df = ak.stock_info_a_code_name()
    codes = df["code"].astype(str).str.extract(r"(\d{6})")[0].dropna()
    return sorted(set(codes))


def _ak_query_daily(symbol: str, start: str, end: str, adjust: str) -> list[list[str]]:
    """腾讯日K (hfq/qfq) → baostock 兼容 rows [[date,open,high,low,close,volume,turnover],...].

    腾讯源量字段为手; turnover 无成交额 → close×volume×100 近似 (turtle 亿级阈值口径会略偏, 报告标注).
    东财接口 (stock_zh_a_hist) 网络环境不可用时以此为主力 fallback.
    """
    import akshare as ak

    tx_symbol = ("sh" if symbol.startswith(("6", "9")) else "sz") + symbol
    raw = ak.stock_zh_a_hist_tx(
        symbol=tx_symbol,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust if adjust in ("hfq", "qfq") else "",
    )
    if raw is None or raw.empty:
        return []
    d = raw.rename(columns={"amount": "volume"})
    d["turnover"] = d["close"] * d["volume"] * 100  # 元 近似 (手→股)
    return list(
        d[["date", "open", "high", "low", "close", "volume", "turnover"]]
        .astype(str)
        .values.tolist()
    )


class AshareSelectSync:
    """全市场 A 股日K同步到本地 SQLite (UNIQUE(symbol, date), 幂等重入).

    provider: "baostock" (TCP 协议) 或 "akshare" (东财 HTTP, baostock 不可用时的 fallback).
    akshare 模式单股逐次拉取无批量连接, get_all_symbols 用东财现货列表.
    """

    def __init__(
        self,
        db_path: str | Path,
        start_date: str = "20200101",
        provider: str = "baostock",
    ) -> None:
        self.db_path = str(db_path)
        self.start_date = start_date
        self.provider = provider
        _ensure_db(self.db_path)

    # ── 股票列表 ──

    def get_all_symbols(self) -> list[str]:
        """全市场 A 股代码. baostock: status==1 且 stock_type==1; akshare: 东财现货列表过滤沪深."""
        if self.provider == "akshare":
            return _ak_all_symbols()
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            logger.error("baostock 登录失败: %s", lg.error_msg)
            return []
        try:
            rs = bs.query_stock_basic(code_name="", code="")
            symbols = []
            while rs.next():
                row = rs.get_row_data()
                if row[4] == "1" and row[5] == "1":
                    symbols.append(row[0].split(".")[1])
            logger.info("获取股票列表完成, 共 %d 只", len(symbols))
            return symbols
        except Exception:
            logger.exception("获取股票列表失败")
            return []
        finally:
            bs.logout()

    # ── 写入 ──

    def _upsert(self, conn: sqlite3.Connection, df) -> int:
        """INSERT OR REPLACE 幂等写入 (替代原版 DELETE-再-append)."""
        rows = [
            (
                r.symbol,
                str(r.date),
                r.open,
                r.high,
                r.low,
                r.close,
                r.volume,
                r.turnover,
            )
            for r in df.itertuples(index=False)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO stock_daily "
            "(symbol, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)

    def _last_date(self, symbol: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM stock_daily WHERE symbol = ?", (symbol,)
            ).fetchone()
        return row[0] if row and row[0] else None

    # ── 增量同步 ──

    def sync_daily(
        self, symbols: list[str], end: str | None = None, adjust: str = "hfq"
    ) -> int:
        """增量同步: 每股从本地 MAX(date)+1 拉到 end (默认今天), OR REPLACE 幂等."""
        end_str = end or date.today().strftime("%Y-%m-%d")
        written = 0
        if self.provider == "akshare":
            for symbol in symbols:
                last = self._last_date(symbol)
                start = (
                    self.start_date
                    if not last
                    else (date.fromisoformat(last) + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                )
                if last and start > end_str:
                    continue
                try:
                    rows = _ak_query_daily(symbol, start, end_str, adjust)
                except Exception as exc:
                    logger.warning("[akshare %s] 拉取失败: %s", symbol, exc)
                    continue
                if rows:
                    with sqlite3.connect(self.db_path) as conn:
                        written += self._upsert(conn, _rows_to_df(rows, symbol))
                        conn.commit()
            return written
        session = _BaostockSession()
        if not session.login():
            return 0
        try:
            for symbol in symbols:
                last = self._last_date(symbol)
                start = (
                    self.start_date
                    if not last
                    else (date.fromisoformat(last) + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                )
                if last and start > end_str:
                    continue
                rows = session.query_daily(symbol, start, end_str, adjust)
                if rows:
                    with sqlite3.connect(self.db_path) as conn:
                        written += self._upsert(conn, _rows_to_df(rows, symbol))
                        conn.commit()
        finally:
            session.logout()
        return written

    # ── 历史回填 ──

    def backfill(
        self,
        symbols: list[str],
        start_date: str | None = None,
        adjust: str = "hfq",
        max_retries: int = 3,
        reconnect_interval: int = 200,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> dict[str, int]:
        """批量回填历史日K (容错吸收自 public-reference 思路):

        - 单股失败重试 max_retries 次, 间隔 2/4/8s 递增
        - 每 reconnect_interval 只重连 baostock 防长连接超时
        - 已入库到今天的 skip, 中断后重跑即续传
        """
        today_str = date.today().strftime("%Y-%m-%d")
        start_default = start_date or self.start_date
        stats = {"success": 0, "skipped": 0, "failed": 0}
        since_reconnect = 0

        session = None
        if self.provider != "akshare":
            session = _BaostockSession()
            if not session.login():
                return stats

        def _reconnect() -> bool:
            if session is None:
                return True
            session.logout()
            return session.login()

        try:
            for i, symbol in enumerate(symbols):
                last = self._last_date(symbol)
                if last and last >= today_str:
                    stats["skipped"] += 1
                    continue

                since_reconnect += 1
                if since_reconnect >= reconnect_interval:
                    if not _reconnect():
                        logger.error("重连失败, 终止回填")
                        break
                    since_reconnect = 0

                start = (
                    start_default
                    if not last
                    else (date.fromisoformat(last) + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                )

                rows: list[list[str]] = []
                ok = False
                for attempt in range(max_retries):
                    try:
                        if self.provider == "akshare" or session is None:
                            rows = _ak_query_daily(symbol, start, today_str, adjust)
                        else:
                            rows = session.query_daily(symbol, start, today_str, adjust)
                        ok = True
                        break
                    except Exception as exc:
                        if attempt < max_retries - 1:
                            wait = 2 ** (attempt + 1)
                            logger.warning(
                                "[%s] 第%d次失败: %s, %ds 后重试",
                                symbol,
                                attempt + 1,
                                exc,
                                wait,
                            )
                            sleep_fn(wait)
                            _reconnect()
                        else:
                            logger.warning(
                                "[%s] %d 次重试均失败, 跳过", symbol, max_retries
                            )

                if not ok or not rows:
                    stats["failed" if not ok else "skipped"] += 1
                    continue

                df = _rows_to_df(rows, symbol)
                if df.empty:
                    stats["skipped"] += 1
                    continue

                with sqlite3.connect(self.db_path) as conn:
                    self._upsert(conn, df)
                    conn.commit()
                stats["success"] += 1

                if (i + 1) % 500 == 0:
                    logger.info("已处理 %d/%d, %s", i + 1, len(symbols), stats)
        finally:
            if session is not None:
                session.logout()

        logger.info("回填完成: %s", stats)
        return stats

    # ── 读取 ──

    def get_ohlcv(self, symbol: str):
        import pandas as pd

        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(
                "SELECT * FROM stock_daily WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )

    def get_local_symbols(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall()
        return [r[0] for r in rows]
