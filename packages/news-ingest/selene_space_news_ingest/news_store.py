"""新闻数据落盘 — 进程内单例的 SQLite 存储。

来源: 本模块最初移植自作者本人的既有项目 `binance-quant`(本机
`<local-path-redacted>`,同一作者 2026-04 建构、无第三方许可问题),
2026-10-05 做过一轮表达改写; 2026-10-05 再做一次 cleanroom 重写,
SQL 构造方式改为表驱动。完整历史见 `packages/news-ingest/PROVENANCE.md`。

调用约定
--------
批量写、以及 ``compute_stats`` 这类重查询是同步阻塞的: 调用方(asyncio 循环、
router handler)要用 ``asyncio.to_thread`` 包一层, 免得卡住事件循环。单行小
查询可以留在事件循环里直接跑。

线程模型
--------
模块级单例持唯一连接(``check_same_thread=False``), 读写共用; 全部访问经同一把
``threading.Lock`` 串行化。读走 ``_read_snapshot``, 写走 ``_write_txn`` —— 两个
上下文管理器包办加锁、开游标、提交/回滚、关游标, 方法体里只剩 SQL 与取数。

SQL 构造
--------
列名集中在 ``_COLUMNS`` 一节按表声明, 语句由 ``_insert_sql`` / ``_select_sql``
这类构造器拼出, 不写整段字面量 —— 加列只改常量, 不会漏改某个语句。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from bisect import bisect_left
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from selene_space_news_ingest.config import NEWS_DB_PATH

# ---------------------------------------------------------------------------
# 表与列(外部契约: 测试与 API 直接依赖这些名字, 改动需同步迁移)
# ---------------------------------------------------------------------------

ITEMS_TABLE = "news_items"
ANOMALIES_TABLE = "volume_anomalies"
STATUS_TABLE = "news_status"

#: 抓取任务的固定集合 —— 心跳表为每个名字留两列, 白名单即此元组。
TASKS: Tuple[str, ...] = ("collector", "tagger", "detector")

#: 抓取时写入的列(其余列交给默认值)。
ITEM_WRITE_COLUMNS: Tuple[str, ...] = (
    "link",
    "title",
    "content",
    "lang",
    "published_at_ms",
    "fetched_at_ms",
)

#: 打标成功后写入的列。
TAG_WRITE_COLUMNS: Tuple[str, ...] = (
    "symbols_json",
    "importance",
    "news_type",
    "sentiment",
)

#: 异动表可写列(自增主键除外), 读写共用同一份顺序。
ANOMALY_COLUMNS: Tuple[str, ...] = (
    "news_id",
    "symbol",
    "volume_ratio",
    "triggered",
    "insufficient_data",
    "price_change_1m_pct",
    "price_change_5m_pct",
    "price_change_15m_pct",
    "checked_at_ms",
)

_ITEM_SELECT_COLUMNS: Tuple[str, ...] = (
    "id",
    "link",
    "title",
    "content",
    "lang",
    "published_at_ms",
    "fetched_at_ms",
    "symbols_json",
    "importance",
    "news_type",
    "sentiment",
    "status",
)

#: query_news 从异动表带出的列(JOIN 后以前缀 a. 出现)。
_ITEM_JOIN_COLUMNS: Tuple[str, ...] = (
    "volume_ratio",
    "triggered",
    "price_change_1m_pct",
    "price_change_5m_pct",
    "price_change_15m_pct",
)

# 建表脚本 —— 列名/约束是外部契约, 保持列顺序稳定。
_SCHEMA = (
    f"""
CREATE TABLE IF NOT EXISTS {ITEMS_TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  link TEXT UNIQUE NOT NULL,
  title TEXT,
  content TEXT,
  lang TEXT,
  published_at_ms INTEGER NOT NULL,
  fetched_at_ms INTEGER NOT NULL,
  symbols_json TEXT DEFAULT '[]',
  importance TEXT,
  news_type TEXT,
  sentiment TEXT,
  status TEXT DEFAULT 'pending',
  error TEXT
);
CREATE TABLE IF NOT EXISTS {ANOMALIES_TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  news_id INTEGER NOT NULL REFERENCES {ITEMS_TABLE}(id),
  symbol TEXT NOT NULL,
  volume_ratio REAL,
  triggered INTEGER DEFAULT 0,
  insufficient_data INTEGER DEFAULT 0,
  price_change_1m_pct REAL,
  price_change_5m_pct REAL,
  price_change_15m_pct REAL,
  checked_at_ms INTEGER,
  UNIQUE(news_id, symbol)
);
CREATE TABLE IF NOT EXISTS {STATUS_TABLE} (
  id INTEGER PRIMARY KEY CHECK (id = 1),
"""
    + "".join(f"  last_{t}_ok_ms INTEGER,\n" for t in TASKS)
    + "".join(
        f"  {t}_errors INTEGER DEFAULT 0{',' if i < len(TASKS) - 1 else ''}\n"
        for i, t in enumerate(TASKS)
    )
    + """
);
"""
)


# ---------------------------------------------------------------------------
# SQL 构造
# ---------------------------------------------------------------------------


def _quoted_placeholders(count: int) -> str:
    return ", ".join("?" * count)


def _insert_sql(table: str, columns: Sequence[str], *, or_replace: bool = False) -> str:
    """生成 INSERT(或 INSERT OR REPLACE) 语句。"""
    verb = "INSERT OR REPLACE INTO" if or_replace else "INSERT OR IGNORE INTO"
    return f"{verb} {table} ({', '.join(columns)}) VALUES ({_quoted_placeholders(len(columns))})"


def _select_sql(table: str, columns: Sequence[str]) -> str:
    return f"SELECT {', '.join(columns)} FROM {table}"


def _heartbeat_columns(task: str) -> Tuple[str, str]:
    """任务名 -> (最近成功列, 累计错误列)。不在 :data:`TASKS` 里则 KeyError。"""
    if task not in TASKS:
        raise KeyError(task)
    return f"last_{task}_ok_ms", f"{task}_errors"


# ---------------------------------------------------------------------------
# 行转换
# ---------------------------------------------------------------------------


def _rows_as_dicts(cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
    """把 ``cursor.fetchall()`` 的结果按列名转成 dict 列表。"""
    names = [col[0] for col in cursor.description or ()]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _split_symbols(record: Dict[str, Any]) -> Dict[str, Any]:
    """``symbols_json`` -> ``symbols`` 列表(键名换掉, 原字段不再保留)。"""
    raw = record.pop("symbols_json", None)
    record["symbols"] = json.loads(raw) if raw else []
    return record


def _as_bool(value: Any) -> Any:
    """SQLite 的 0/1 转 bool; NULL 原样保留(表示"未记录")。"""
    return None if value is None else bool(value)


def _escape_like(literal: str) -> str:
    """转义 LIKE 通配符 —— 参数化只防注入, 不防用户输入里的 ``%`` / ``_``。"""
    return literal.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _histogram(values: Iterable[Optional[float]], bins: int = 10) -> dict:
    """等宽分桶 + min/max/mean。

    全 None 时桶全零; 所有取值相同时(宽度为零)整体落进最后一个桶。
    """
    present = [v for v in values if v is not None]
    if not present:
        return {"bins": [0] * bins, "min": None, "max": None, "mean": None}

    low, high = min(present), max(present)
    summary = {"min": low, "max": high, "mean": sum(present) / len(present)}
    counts = [0] * bins
    if high == low:
        counts[-1] = len(present)
        return {"bins": counts, **summary}

    step = (high - low) / bins
    edges = [low + step * i for i in range(bins + 1)]
    for value in present:
        index = min(max(bisect_left(edges, value) - 1, 0), bins - 1)
        counts[index] += 1
    return {"bins": counts, **summary}


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


class NewsStore:
    """新闻主表 + 异动明细 + 任务心跳, 三张表一个连接。"""

    def __init__(self, db_path: Any = None) -> None:
        target = Path(db_path) if db_path is not None else Path(NEWS_DB_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        # 心跳行恒存在, 且只有 id=1 一行
        with self._lock:
            self._conn.execute(f"INSERT OR IGNORE INTO {STATUS_TABLE} (id) VALUES (1)")
            self._conn.commit()

    # ---- 访问原语 ----

    @contextmanager
    def _read_snapshot(self) -> Iterator[sqlite3.Cursor]:
        """只读: 持锁开游标, 用完关掉, 不提交。"""
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    @contextmanager
    def _write_txn(self) -> Iterator[sqlite3.Cursor]:
        """写: 持锁开游标, 顺利走完提交, 抛异常回滚后原样重抛。"""
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
            finally:
                cursor.close()

    # ---- 写入 ----

    def insert_items(self, items: List[dict]) -> int:
        """落库新条目。``link`` 唯一约束去重, 返回真正新增的条数。"""
        statement = _insert_sql(ITEMS_TABLE, ITEM_WRITE_COLUMNS)
        rows = [
            (
                item["link"],
                item.get("title"),
                item.get("content"),
                item.get("lang"),
                int(item["published_at_ms"]),
                int(item["fetched_at_ms"]),
            )
            for item in items
        ]
        inserted = 0
        with self._write_txn() as cursor:
            for row in rows:
                cursor.execute(statement, row)
                inserted += cursor.rowcount
        return inserted

    def claim_pending(self, limit: int = 10) -> List[dict]:
        """把至多 ``limit`` 条 pending 原子地认领为 tagging, 返回完整行。

        先 ``BEGIN IMMEDIATE`` 拿写锁, 再逐行条件 UPDATE 并看 rowcount ——
        两个 worker 同时跑也不会认领到同一行。
        """
        grabbed: List[dict] = []
        with self._write_txn() as cursor:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                f"SELECT id FROM {ITEMS_TABLE} WHERE status = 'pending' "
                "ORDER BY published_at_ms LIMIT ?",
                (limit,),
            )
            candidates = [row[0] for row in cursor.fetchall()]

            for item_id in candidates:
                cursor.execute(
                    f"UPDATE {ITEMS_TABLE} SET status = 'tagging' "
                    "WHERE id = ? AND status = 'pending'",
                    (item_id,),
                )
                if cursor.rowcount == 0:
                    continue  # 已被别的 worker 抢走
                cursor.execute(
                    _select_sql(ITEMS_TABLE, ("*",)) + " WHERE id = ?", (item_id,)
                )
                fetched = cursor.fetchone()
                if fetched is not None:
                    grabbed.append(
                        _split_symbols(
                            dict(zip([c[0] for c in cursor.description], fetched))
                        )
                    )
        return grabbed

    def reclaim_orphan_tagging(self) -> int:
        """把仍卡在 tagging 的行退回 pending, 返回退回条数。

        单进程下 tagger 串行处理每批, 进批处理时不可能还有本进程在写的 tagging
        行 —— 剩下的都是上次崩溃/被杀留下的僵尸认领。每批开头调一次即自愈。
        """
        with self._write_txn() as cursor:
            cursor.execute(
                f"UPDATE {ITEMS_TABLE} SET status = 'pending' WHERE status = 'tagging'"
            )
            affected = cursor.rowcount
        return int(affected or 0)

    def update_tag(
        self,
        item_id: int,
        symbols: List[str],
        importance: str,
        news_type: str,
        sentiment: str,
        ok: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """回写打标结果: 成功置 tagged 并清 error, 失败置 failed 并留 error。"""
        with self._write_txn() as cursor:
            if ok:
                assignments = ", ".join(
                    f"{column} = ?" for column in (*TAG_WRITE_COLUMNS, "status")
                )
                cursor.execute(
                    f"UPDATE {ITEMS_TABLE} SET {assignments}, error = NULL WHERE id = ?",
                    (
                        json.dumps(symbols or [], ensure_ascii=False),
                        importance,
                        news_type,
                        sentiment,
                        "tagged",
                        item_id,
                    ),
                )
            else:
                cursor.execute(
                    f"UPDATE {ITEMS_TABLE} SET status = 'failed', error = ? WHERE id = ?",
                    (error, item_id),
                )

    def release_to_pending(self, item_id: int, error: Optional[str] = None) -> None:
        """把暂时性失败退回 pending 等下一轮。

        failed 是终态, 只留给重试救不回的错(解析失败 / 响应结构异常); 限速、超时
        这类退回队列 —— 否则在紧限速端点上 tagger 会把整批积压一次打成 failed。
        """
        with self._write_txn() as cursor:
            cursor.execute(
                f"UPDATE {ITEMS_TABLE} SET status = 'pending', error = ? WHERE id = ?",
                (error, item_id),
            )

    def fetch_ready_to_check(self, now_ms: int) -> List[dict]:
        """挑出可以开始异动检测的新闻。

        条件: high + breaking、带 symbol、发布已满 6 分钟(5 分钟统计窗 + 1 分钟
        收线)。已 checked 的行在发布满 17 分钟前仍会被再次挑中 —— 首检 +6min 时
        +15m 那根 K 线还没生成, 靠复检补 p15; 过 17 分钟不再挑, 于是永久失败的
        symbol 也有界(最多复检约十轮)。
        """
        with self._read_snapshot() as cursor:
            cursor.execute(
                _select_sql(ITEMS_TABLE, ("*",))
                + " WHERE importance = 'high' AND news_type = 'breaking'"
                " AND symbols_json != '[]' AND published_at_ms <= ?"
                " AND (status = 'tagged'"
                " OR (status = 'checked' AND published_at_ms > ?))",
                (now_ms - 6 * 60 * 1000, now_ms - 17 * 60 * 1000),
            )
            return [_split_symbols(record) for record in _rows_as_dicts(cursor)]

    def save_anomaly(
        self,
        news_id: int,
        symbol: str,
        volume_ratio: Optional[float],
        triggered: bool,
        insufficient_data: bool,
        p1: Optional[float],
        p5: Optional[float],
        p15: Optional[float],
        now_ms: int,
    ) -> None:
        """写一行异动结果, 同 (news_id, symbol) 冲突则覆盖。

        这里不碰新闻状态 —— status 统一由 :meth:`mark_checked` 管, 以支持
        +6min 首检 / +15min 复检两阶段。
        """
        values = [
            news_id,
            symbol,
            volume_ratio,
            int(triggered),
            int(insufficient_data),
            p1,
            p5,
            p15,
            now_ms,
        ]
        assert len(values) == len(ANOMALY_COLUMNS)
        with self._write_txn() as cursor:
            cursor.execute(
                _insert_sql(ANOMALIES_TABLE, ANOMALY_COLUMNS, or_replace=True),
                tuple(values),
            )

    def mark_checked(self, news_id: int) -> None:
        """把新闻置为 checked。

        detector 在一条新闻的所有 symbol 都落库后才调; 中途有 symbol 抛异常没落库
        就不调, 状态留在原处等下一轮复检。
        """
        with self._write_txn() as cursor:
            cursor.execute(
                f"UPDATE {ITEMS_TABLE} SET status = 'checked' WHERE id = ?", (news_id,)
            )

    # ---- 查询 ----

    def query_news(
        self,
        symbol: Optional[str] = None,
        importance: Optional[str] = None,
        news_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        keyword: Optional[str] = None,
        fetched_after_ms: Optional[int] = None,
    ) -> List[dict]:
        """分页倒序查新闻, 可选过滤; LEFT JOIN 异动表带出关联列。

        ``keyword`` 在标题与正文里做子串匹配(SQLite 的 LIKE 对 ASCII 大小写
        不敏感, 中文无大小写之分), ``%`` / ``_`` / ``\\`` 一律转义 —— 参数化
        只防注入, 不防用户把 ``%`` 当通配符打出全表。
        ``fetched_after_ms`` 把「刚进来的消息」与陈年旧闻分开。口径取
        ``fetched_at_ms`` 而不是 ``published_at_ms``: 源站的发布时间经常带时区
        偏差, 而 link 去重保证每篇只入库一次, 首次见到的时间就是「新」本身。
        """
        clauses: List[str] = []
        params: List[Any] = []

        if symbol:
            clauses.append("n.symbols_json LIKE ? ESCAPE '\\'")
            params.append(f'%"{_escape_like(symbol)}"%')
        if importance:
            clauses.append("n.importance = ?")
            params.append(importance)
        if news_type:
            clauses.append("n.news_type = ?")
            params.append(news_type)
        if keyword:
            pattern = f"%{_escape_like(keyword.strip())}%"
            clauses.append(
                "(n.title LIKE ? ESCAPE '\\' OR n.content LIKE ? ESCAPE '\\')"
            )
            params.extend((pattern, pattern))
        if fetched_after_ms is not None:
            clauses.append("n.fetched_at_ms >= ?")
            params.append(int(fetched_after_ms))

        selected = ", ".join(
            [f"n.{column}" for column in _ITEM_SELECT_COLUMNS]
            + [f"a.{column} AS {column}" for column in _ITEM_JOIN_COLUMNS]
        )
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((limit, offset))

        with self._read_snapshot() as cursor:
            cursor.execute(
                f"SELECT {selected} FROM {ITEMS_TABLE} n"
                f" LEFT JOIN {ANOMALIES_TABLE} a ON a.news_id = n.id"
                f"{where} ORDER BY n.published_at_ms DESC LIMIT ? OFFSET ?",
                params,
            )
            records = _rows_as_dicts(cursor)

        for record in records:
            _split_symbols(record)
            record["triggered"] = _as_bool(record.get("triggered"))
        return records

    def query_anomalies(self, days: int = 7) -> List[dict]:
        """最近 N 天有效异动明细(略去 insufficient_data), 按检测时间倒序。"""
        since_ms = int(time.time() * 1000) - days * 24 * 3600 * 1000
        with self._read_snapshot() as cursor:
            cursor.execute(
                _select_sql(ANOMALIES_TABLE, ("id", *ANOMALY_COLUMNS))
                + " WHERE insufficient_data = 0 AND checked_at_ms >= ?"
                " ORDER BY checked_at_ms DESC",
                (since_ms,),
            )
            records = _rows_as_dicts(cursor)

        for record in records:
            record["triggered"] = bool(record["triggered"])
        return records

    def compute_stats(self, days: int = 7) -> dict:
        """最近 N 天 high+breaking 新闻的触发情况与价格分布。

        时间口径看 ``fetched_at_ms``; 价格分布对每条 anomaly 的 1m/5m/15m 涨跌幅
        各画一张 10 桶直方图(纯 Python ``bisect`` 分桶), 附 min/max/mean。

        触发率分母取"真正完成有效检测的新闻数"(有 ratio 的 anomaly 行按新闻去重),
        而不是 ``total_high_breaking``。空 symbol 的、以及因故 insufficient 的新闻
        进了 total 却没资格进分母 —— 拿 total 当分母会把触发率系统性压低。
        """
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - days * 24 * 3600 * 1000

        with self._read_snapshot() as cursor:
            cursor.execute(
                f"SELECT id FROM {ITEMS_TABLE}"
                " WHERE importance = 'high' AND news_type = 'breaking'"
                " AND fetched_at_ms >= ?",
                (since_ms,),
            )
            news_ids = [row[0] for row in cursor.fetchall()]

            measurements: List[tuple] = []
            if news_ids:
                marks = _quoted_placeholders(len(news_ids))
                cursor.execute(
                    "SELECT news_id, triggered, price_change_1m_pct,"
                    " price_change_5m_pct, price_change_15m_pct"
                    f" FROM {ANOMALIES_TABLE} WHERE news_id IN ({marks})"
                    " AND insufficient_data = 0",
                    news_ids,
                )
                measurements = cursor.fetchall()

        tested = {row[0] for row in measurements}
        fired = sum(1 for row in measurements if row[1])
        return {
            "days": days,
            "total_high_breaking": len(news_ids),
            "tested_count": len(tested),
            "triggered_count": fired,
            "trigger_rate": (fired / len(tested)) if tested else 0.0,
            "price_dist": {
                "p1": _histogram(row[2] for row in measurements),
                "p5": _histogram(row[3] for row in measurements),
                "p15": _histogram(row[4] for row in measurements),
            },
        }

    # ---- 任务心跳 ----

    def record_task_ok(self, task: str, now_ms: int) -> None:
        """记一次成功心跳。``task`` 不在白名单会 KeyError。"""
        ok_column, _ = _heartbeat_columns(task)
        with self._write_txn() as cursor:
            cursor.execute(
                f"UPDATE {STATUS_TABLE} SET {ok_column} = ? WHERE id = 1", (now_ms,)
            )

    def record_task_error(self, task: str, now_ms: int) -> None:
        """记一次失败: 同时刷新心跳时间并把错误计数 +1。"""
        ok_column, error_column = _heartbeat_columns(task)
        with self._write_txn() as cursor:
            cursor.execute(
                f"UPDATE {STATUS_TABLE} SET {ok_column} = ?,"
                f" {error_column} = {error_column} + 1 WHERE id = 1",
                (now_ms,),
            )

    def get_status(self) -> dict:
        """三个后台任务各自的心跳时刻与累计错误数。"""
        with self._read_snapshot() as cursor:
            cursor.execute(_select_sql(STATUS_TABLE, ("*",)) + " WHERE id = 1")
            snapshot = (_rows_as_dicts(cursor) or [{}])[0]
        return {
            task: {
                "last_ok_ms": snapshot.get(f"last_{task}_ok_ms"),
                "errors": snapshot.get(f"{task}_errors", 0),
            }
            for task in TASKS
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


#: 进程内单例 —— 首次取用时构造。
_store_instance: Optional[NewsStore] = None


def get_news_store() -> NewsStore:
    """取新闻存储单例。"""
    global _store_instance
    if _store_instance is None:
        _store_instance = NewsStore()
    return _store_instance


def reset_store_singleton() -> None:
    """测试辅助: 关掉并丢弃单例, 让每个测试用各自的 tmp DB。"""
    global _store_instance
    if _store_instance is not None:
        _store_instance.close()
    _store_instance = None
