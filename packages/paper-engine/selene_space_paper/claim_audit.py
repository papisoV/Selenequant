"""LLM 数字 claim 审计 — 从交易信号文本抽取数据点并给出准出/打回判决。

Absorbed from ai-berkshire (https://github.com/xbtlin/ai-berkshire), MIT License (c) 2026 xbtlin.
Source: tools/report_audit.py  — 移植为 selene_space_paper/claim_audit.py。见 docs/ABSORPTION_FROM_AI_BERKSHIRE.md。

将 ai-berkshire 的 report_audit 重塑为纯库模块（无 CLI、无 __main__）：
  - ``ClaimAuditor.extract(text)``：从 markdown 抽取数字 claim（表格行、KV 冒号行、
    可配置 pattern registry）。抽取层同时覆盖权益域（亿/万亿/PE x/percent/$BMT/表格行）
    与 crypto 域（BTC/ETH/USDT 计价数量、$ 价格、funding rate bp、market cap B/T）。
  - ``ClaimAuditor.verdict(claims, fetched)``：域无关的准出/打回判决，默认 1% 容差。

零第三方依赖（纯 stdlib）。默认不实例化、不调用——由决策管线以可选参数接线，
``claim_auditor=None`` 时默认路径行为逐字节不变（见 docs/ABSORPTION_FROM_AI_BERKSHIRE.md §4）。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from random import Random
from typing import Mapping, Sequence

# ---------------------------------------------------------------------------
# 数字清洗与符号归一化
# ---------------------------------------------------------------------------

# 所有数字捕获组必须带 _SIGN：否则 "-1.72%" 会被抓成 "1.72"，核验时报告值与
# 信源值符号相反、偏差 200%，产生假打回。涵盖 ASCII 正负号、Unicode 减号(U+2212)、
# en-dash(U+2013)、全角正负号(U+FF0D/U+FF0B)。
_SIGN = r'[+\-−–－＋]?'
_NUM = r'[\d,，\.]+'

# 多列 markdown 表格 cell 里的数值+单位
_CELL_RE = re.compile(
    r'[~约]?\$?(' + _SIGN + r'[\d,，\.]+)\s*'
    r'(亿[元美港]?元?|万亿|[xX倍]|%|[BMT])?'
)

# 带标签的 KV 冒号行：标签：数值 单位
_KV_LABEL_RE = re.compile(
    r'(?P<label>[一-龥A-Za-z][^\|\n：:*]{1,30})[：:]\s*[~约]?\$?'
    r'(?P<num>' + _SIGN + r'[\d,，\.]+)\s*(?P<unit>亿[元美港]?元?|万亿|[xX倍]|%|[BMT])?'
)


def _clean_num(s: str) -> float | None:
    """把带逗号、中文逗号、各类正负号的数字字符串转为 float。

    支持 ASCII '-'/'+'、Unicode 减号 '−'(U+2212)、en-dash '–'(U+2013)、
    全角 '－'(U+FF0D)/'＋'(U+FF0B)——报告中这些符号都可能被用作正负号。
    """
    s = s.replace(',', '').replace('，', '').strip()
    for ch in ('−', '–', '－'):
        s = s.replace(ch, '-')
    s = s.replace('＋', '+')
    try:
        return float(s)
    except ValueError:
        return None


def _is_valid_label(label: str) -> bool:
    """判断标签是否是有意义的财务字段名，过滤噪声。"""
    label = label.strip()
    if len(label) < 2:
        return False
    if re.fullmatch(r'[\d\s年季度Q]+', label):
        return False
    if re.match(r'^[+\-\*#\|~\$>_`]', label):
        return False
    if '**' in label or '`' in label or '__' in label:
        return False
    if re.fullmatch(r'[+\-]?\d+(\.\d+)?%', label):
        return False
    _SKIP = {'来源', 'sources', 'source', '说明', '注意', '备注', '数据来源',
             'n/a', '—', '-', '/', '合计', 'total', '单位', '趋势'}
    if label.lower() in _SKIP:
        return False
    return True


# ---------------------------------------------------------------------------
# 表格解析
# ---------------------------------------------------------------------------


def _parse_md_tables(lines: list[str]) -> list[tuple[str, str, float, str, int, str]]:
    """解析 markdown 中所有表格，返回 (row_label, col_header, value, unit, lineno, raw)。"""
    results = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 检测表头行（含 | 且不是分隔行）
        if '|' in line and not re.match(r'^\|[\-\s\|:]+\|$', line):
            headers_raw = [h.strip().strip('*_').strip() for h in line.split('|')]
            headers_raw = [h for h in headers_raw if h]
            # 下一行应是分隔行
            if i + 1 < len(lines) and re.match(r'^\|[\-\s\|:]+\|$', lines[i + 1].strip()):
                i += 2  # 跳过分隔行
                while i < len(lines):
                    dline = lines[i].strip()
                    if not dline or not dline.startswith('|'):
                        break
                    cells = [c.strip().strip('*_~').strip() for c in dline.split('|')]
                    cells = [c for c in cells if c != '']
                    if len(cells) < 2:
                        i += 1
                        continue
                    row_label = cells[0]
                    for col_idx, cell in enumerate(cells[1:], start=1):
                        col_header = headers_raw[col_idx] if col_idx < len(headers_raw) else f'列{col_idx}'
                        m = _CELL_RE.search(cell)
                        if m:
                            val = _clean_num(m.group(1))
                            unit = (m.group(2) or '').strip()
                            if val is not None and val != 0 and abs(val) < 1e15:
                                results.append((row_label, col_header, val, unit, i + 1, dline))
                    i += 1
                continue
        i += 1
    return results


# ---------------------------------------------------------------------------
# 可配置 pattern registry（权益域 + crypto 域）
# ---------------------------------------------------------------------------
# 每个 pattern = (name, regex, unit_spec)。unit_spec 为 int 时表示从对应
# match group 取单位（如 usd_abs 的 B/M/T/亿），否则为固定单位字符串。

_EQUITY_PATTERNS: tuple[tuple[str, str, object], ...] = (
    # $...BMT 绝对金额（源 report_audit.py L57）——必须带 B/M/T/亿 后缀，
    # 无后缀的裸 $ 价格由 crypto_price 捕获，二者不串位
    ("usd_abs", r'\$\s*(' + _SIGN + _NUM + r')\s*([BMT亿])', 2),
    ("percent", r'(' + _SIGN + _NUM + r')\s*%', '%'),
    ("multiple", r'(' + _SIGN + _NUM + r')\s*[xX倍]', 'x'),
    ("trillion", r'(' + _SIGN + _NUM + r')\s*万亿', '万亿'),
    ("hundred_million", r'(' + _SIGN + _NUM + r')\s*亿(元|美元|港元|RMB|USD|HKD)?', '亿'),
)

_CRYPTO_PATTERNS: tuple[tuple[str, str, object], ...] = (
    # 裸 $ 价格（无 B/M/T/亿 后缀），如 $64,200
    ("crypto_price", r'\$\s*(' + _SIGN + _NUM + r')(?!\s*[BMT亿])', '$'),
    ("crypto_qty", r'(' + _SIGN + _NUM + r')\s*(BTC|ETH|USDT)\b', 2),
    ("funding_rate", r'(' + _SIGN + _NUM + r')\s*bp\b', 'bp'),
)


def _unit_for(spec: object, m: re.Match) -> str:
    if isinstance(spec, int):
        return (m.group(spec) or '').strip()
    return spec if isinstance(spec, str) else ''


def _pct_diff(reported: float, fetched: float) -> float:
    """相对偏差 (absolute)。"""
    if reported == 0:
        return 0.0 if fetched == 0 else float('inf')
    return abs(reported - fetched) / abs(reported)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimDataPoint:
    """从报告/信号文本中提取出的一个数字 claim。"""

    id: int
    label: str
    reported_value: float
    unit: str
    raw_text: str
    line_number: int


@dataclass(frozen=True)
class ClaimResult:
    """单个 claim 的核验结果。"""

    id: int
    label: str
    reported_value: float
    unit: str
    fetched_value: float
    source: str
    diff_pct: float  # 相对偏差，百分比（如 0.5 = 0.5%）
    passed: bool


@dataclass(frozen=True)
class ClaimReport:
    """准出/打回判决结果。"""

    verdict: str  # 'PASS' | 'FAIL'
    total: int
    pass_count: int
    fail_count: int
    results: list[ClaimResult]


# ---------------------------------------------------------------------------
# ClaimAuditor
# ---------------------------------------------------------------------------


class ClaimAuditor:
    """从 markdown 文本抽取数字 claim 并给出准出/打回判决。

    Args:
        tolerance: 相对容差，默认 0.01（1%）。偏差 ≤ 容差为通过。
        domains: 启用的抽取 pattern 域，可取 ("equity",) / ("crypto",) / 两者。
            verdict 引擎域无关，始终可用。
        patterns: 显式覆盖的 (name, regex, unit_spec) 列表；给定时代替按 domains
            拼接的 registry（用于自定义域/口径）。
    """

    def __init__(
        self,
        tolerance: float = 0.01,
        domains: Sequence[str] = ("equity", "crypto"),
        patterns: Sequence[tuple[str, str, object]] | None = None,
    ) -> None:
        self._tolerance = tolerance
        if patterns is not None:
            self._patterns = [(name, re.compile(src), unit) for name, src, unit in patterns]
        else:
            merged: list[tuple[str, str, object]] = []
            if "equity" in domains:
                merged.extend(_EQUITY_PATTERNS)
            if "crypto" in domains:
                merged.extend(_CRYPTO_PATTERNS)
            self._patterns = [(name, re.compile(src), unit) for name, src, unit in merged]

    def extract(self, text: str) -> list[ClaimDataPoint]:
        """从 markdown 文本中提取所有可识别的数字 claim。

        覆盖三类结构：
          1. 多列 markdown 表格（最主要的来源）：(行标签 + 列标题) → 数值
          2. 带冒号的 KV 行：标签：数值 单位
          3. pattern registry 扫描未带标签的行（覆盖权益域 + crypto 域）

        返回 list[ClaimDataPoint]，id 从 1 起顺序编号。
        """
        lines = text.split('\n')
        points: list[ClaimDataPoint] = []
        seen: set[str] = set()

        def _add(label: str, val: float, unit: str, lineno: int, raw: str) -> None:
            label = re.sub(r'[\*_`]+', '', label).strip()
            if not _is_valid_label(label):
                return
            if val is None or val == 0 or abs(val) > 1e15:
                return
            if re.fullmatch(r'(20\d{2}|Q[1-4]|\d{4}\s*Q[1-4])', label.strip()):
                return
            key = f"{label}|{round(val, 4)}|{unit}"
            if key in seen:
                return
            seen.add(key)
            points.append(ClaimDataPoint(
                id=len(points) + 1,
                label=label,
                reported_value=val,
                unit=unit,
                raw_text=raw[:120],
                line_number=lineno,
            ))

        # --- 1. 多列表格 ---
        for row_label, col_header, val, unit, lineno, raw in _parse_md_tables(lines):
            if not _is_valid_label(row_label):
                continue
            # 跳过无意义列标题（YoY 增速列单独标注，不作为待核验数据）
            if col_header.upper() in ('YOY', 'YOY增速', '增速', '同比', '变化', '趋势', '说明', '备注'):
                continue
            label = f"{row_label} · {col_header}" if col_header and col_header != row_label else row_label
            _add(label, val, unit, lineno, raw)

        # --- 2. KV 冒号行 + pattern registry ---
        in_code = False
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code = not in_code
                continue
            if in_code or stripped.startswith('> ') or re.match(r'^#{1,6}\s', stripped):
                continue
            if '|' in stripped:
                continue  # 表格已在上面处理

            consumed: list[tuple[int, int]] = []

            # KV 行先认领自己的 span，避免与 pattern registry 重复捕获
            for m in _KV_LABEL_RE.finditer(stripped):
                label = m.group('label')
                val = _clean_num(m.group('num'))
                unit = (m.group('unit') or '').strip()
                consumed.append(m.span())
                _add(label, val, unit, lineno, stripped)

            # pattern registry 扫描未被认领的 span
            for name, cre, unit_spec in self._patterns:
                for m in cre.finditer(stripped):
                    if any(s < m.end() and m.start() < e for s, e in consumed):
                        continue
                    val = _clean_num(m.group(1))
                    unit = _unit_for(unit_spec, m)
                    consumed.append(m.span())
                    _add(name, val, unit, lineno, stripped)

        return points

    def verdict(self, claims: Sequence[ClaimDataPoint], fetched: Mapping[int, object]) -> ClaimReport:
        """根据核验值输出准出/打回判决。

        Args:
            claims: extract() 返回的 claim 列表。
            fetched: claim id → 核验值。核验值可为 float（source 记为 ""），
                或 (value, source) 二元组。

        Returns:
            ClaimReport：verdict='PASS' | 'FAIL'，附逐 claim 偏差。
                没有核验值的 claim 不计入 total（与源报告行为一致）。
        """
        results: list[ClaimResult] = []
        fail_count = 0
        for c in claims:
            if c.id not in fetched:
                continue
            raw = fetched[c.id]
            if isinstance(raw, (tuple, list)):
                value, source = float(raw[0]), str(raw[1])
            else:
                value, source = float(raw), ''
            diff = _pct_diff(c.reported_value, value)
            passed = diff <= self._tolerance
            if not passed:
                fail_count += 1
            results.append(ClaimResult(
                id=c.id,
                label=c.label,
                reported_value=c.reported_value,
                unit=c.unit,
                fetched_value=value,
                source=source,
                diff_pct=diff * 100,
                passed=passed,
            ))
        pass_count = len(results) - fail_count
        return ClaimReport(
            verdict='PASS' if fail_count == 0 else 'FAIL',
            total=len(results),
            pass_count=pass_count,
            fail_count=fail_count,
            results=results,
        )


def sample_points(points: Sequence[ClaimDataPoint], ratio: float = 0.15, seed: int | None = None) -> list[ClaimDataPoint]:
    """随机抽取 ratio 比例的数据点，最少 3 个，最多 30 个（按行号排序，方便比对）。"""
    n = max(3, min(30, math.ceil(len(points) * ratio)))
    n = min(n, len(points))
    rng = Random(seed)
    sampled = rng.sample(list(points), n)
    return sorted(sampled, key=lambda p: p.line_number)
