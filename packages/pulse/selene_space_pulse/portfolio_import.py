from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml



COLUMN_ALIASES = {
    "symbol": {"symbol", "ticker", "code", "stock", "证券代码", "股票代码", "代码"},
    "name": {"name", "asset", "security", "stock_name", "证券名称", "股票名称", "名称"},
    "market": {"market", "exchange", "市场", "交易所"},
    "currency": {"currency", "ccy", "币种", "货币"},
    "quantity": {"quantity", "qty", "shares", "position", "holding", "持仓", "数量", "股数"},
    "cost_basis": {
        "cost_basis",
        "avg_cost",
        "average_cost",
        "cost",
        "entry_price",
        "成本价",
        "持仓成本",
        "平均成本",
    },
    "tags": {"tags", "tag", "labels", "标签"},
    "note": {"note", "notes", "comment", "备注"},
}


class PortfolioImportError(RuntimeError):
    pass


def import_portfolio_config(
    input_path: str | Path,
    output_path: str | Path,
    template: str = "default",
    title: str | None = None,
) -> Path:
    assets = read_portfolio_assets(input_path)
    if not assets:
        raise PortfolioImportError("No valid assets found in portfolio file.")

    config = _template_config(template)
    config["assets"] = assets
    if title:
        config["title"] = title

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def read_portfolio_assets(input_path: str | Path) -> list[dict[str, Any]]:
    path = Path(input_path)
    if not path.exists():
        raise PortfolioImportError(f"Portfolio file not found: {path}")
    frame = _read_frame(path)
    if frame.empty:
        return []

    normalized_columns = {_normalize_column(column): column for column in frame.columns}
    resolved = _resolve_columns(normalized_columns)
    if "symbol" not in resolved:
        raise PortfolioImportError("Portfolio file must include a symbol/ticker/code column.")

    assets: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        symbol = _symbol_text(row.get(resolved["symbol"]))
        if not symbol:
            continue
        asset: dict[str, Any] = {
            "symbol": _normalize_symbol(symbol),
            "market": _clean_text(row.get(resolved.get("market"))) or _infer_market(symbol),
        }
        for key in ["name", "currency", "note"]:
            column = resolved.get(key)
            value = _clean_text(row.get(column)) if column else None
            if value:
                asset[key] = value
        for key in ["quantity", "cost_basis"]:
            column = resolved.get(key)
            value = _number(row.get(column)) if column else None
            if value is not None:
                asset[key] = value
        tags_column = resolved.get("tags")
        tags = _tags(row.get(tags_column)) if tags_column else []
        if tags:
            asset["tags"] = tags
        assets.append(asset)
    return assets


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str)
    if suffix in {".xlsx", ".xlsm"}:
        try:
            return pd.read_excel(path)
        except ImportError as exc:
            raise PortfolioImportError("Excel import requires openpyxl. Run `pip install -e .[excel]`.") from exc
    raise PortfolioImportError(f"Unsupported portfolio file type: {suffix}. Use .csv, .tsv, or .xlsx.")


def _resolve_columns(columns: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        normalized_aliases = {_normalize_column(alias) for alias in aliases}
        for alias in normalized_aliases:
            if alias in columns:
                resolved[field] = columns[alias]
                break
    return resolved


def _template_config(template: str) -> dict[str, Any]:
    from selene_space_pulse.sample import SAMPLE_CONFIGS

    if template not in SAMPLE_CONFIGS:
        names = ", ".join(sorted(SAMPLE_CONFIGS))
        raise PortfolioImportError(f"Unknown template '{template}'. Available templates: {names}")
    return yaml.safe_load(SAMPLE_CONFIGS[template]) or {}


def _normalize_column(value: object) -> str:
    return re.sub(r"[\s_\-./]+", "", str(value).strip().lower())


def _clean_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _symbol_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        number = int(value)
        if 0 <= number <= 999999:
            return f"{number:06d}"
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        number = int(float(text))
        if 0 <= number <= 999999:
            return f"{number:06d}"
    if re.fullmatch(r"\d{1,6}", text):
        return f"{int(text):06d}"
    return text or None


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1].strip()
    if _is_european_decimal_comma(text):
        whole, decimals = text.rsplit(",", 1)
        text = f"{whole.replace('.', '')}.{decimals}"
    text = re.sub(r"[,$¥￥€£\s]", "", text)
    if is_negative:
        text = f"-{text}"
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_european_decimal_comma(text: str) -> bool:
    if text.count(",") != 1:
        return False
    whole, decimals = text.rsplit(",", 1)
    if not (decimals.isdigit() and 1 <= len(decimals) <= 2):
        return False
    if "." not in whole:
        return True
    groups = whole.lstrip("-").split(".")
    leading_ok = groups[0].isdigit() and 1 <= len(groups[0]) <= 3
    return leading_ok and all(group.isdigit() and len(group) == 3 for group in groups[1:])


def _tags(value: object) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,;|，；、]", text) if item.strip()]


# Narrow special-case: these are well-known Shanghai-listed *index* codes that
# happen to share the "000" prefix with ordinary Shenzhen stock codes (e.g.
# "000001" Ping An Bank, which must stay .SZ). This is not a general fix for
# that prefix ambiguity -- just an allowlist for common indices.
_SHANGHAI_INDEX_CODES = {"000300", "000905", "000016"}


def _normalize_symbol(symbol: str) -> str:
    text = symbol.strip().upper()
    if re.fullmatch(r"\d{6}", text):
        if text in _SHANGHAI_INDEX_CODES or text.startswith(("6", "5", "9")):
            return f"{text}.SS"
        return f"{text}.SZ"
    return text


def _infer_market(symbol: str) -> str:
    text = symbol.strip().upper()
    if text.endswith((".SS", ".SZ")) or re.fullmatch(r"\d{6}", text):
        return "CN"
    if text.endswith("-USD"):
        return "CRYPTO"
    if text.endswith(".HK"):
        return "HK"
    return "US"
