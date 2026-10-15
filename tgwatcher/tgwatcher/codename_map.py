"""Persistent 币种→代号 mapping for v2.3.2 codename enforcement.

Same coin always maps to the same 标的X code across sessions.
Thread-safe, file-backed for persistence across restarts.

Usage:
    from tgwatcher.codename_map import codename_map

    code = codename_map.get_code("BTC")       # "标的A"
    code2 = codename_map.get_code("BTC")      # "标的A" (same)
    code3 = codename_map.get_code("ETH")      # "标的B"
    real = codename_map.decode("标的A")        # "BTC"
    safe = codename_map.replace_names("BTC暴跌")  # "标的A暴跌"
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MAP_FILE = Path(__file__).parent / "data" / "codename_map.json"

# Common crypto symbols that might appear in text (case-insensitive match)
# This is a seed list — new symbols are auto-added when first encountered.
_SEED_SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "DOT", "AVAX",
    "MATIC", "LINK", "UNI", "ATOM", "LTC", "NEAR", "APT", "ARB", "OP",
    "SUI", "SEI", "INJ", "TIA", "JUP", "WIF", "PEPE", "SHIB", "FIL",
    "AAVE", "MKR", "SNX", "COMP", "CRV", "LDO", "RPL", "FXS", "PENDLE",
    "STX", "RUNE", "IMX", "BLUR", "DYDX", "GMX", "RDNT", "WOO",
    "TRX", "TON", "BCH", "EOS", "XTZ", "ALGO", "VET", "ICP", "HBAR",
    "SAND", "MANA", "AXS", "GALA", "ENJ", "IMX", "FLOW",
    "USDT", "USDC", "DAI", "FRAX", "TUSD", "BUSD",
    "WLD", "STRK", "MANTA", "DYM", "ALT", "PIXEL", "PORTAL", "AEVO",
    "ENA", "ETHFI", "W", "OMNI", "REZ", "SAGA",
]


class CodenameMap:
    """Thread-safe persistent 币种→代号 mapping.

    Assigns sequential codes: 标的A, 标的B, ..., 标的Z, 标的AA, ...
    Persists to JSON so the same coin always gets the same code.
    """

    def __init__(self, map_file: Path | None = None) -> None:
        self._file = map_file or _DEFAULT_MAP_FILE
        self._lock = threading.Lock()
        self._name_to_code: dict[str, str] = {}
        self._code_to_name: dict[str, str] = {}
        self._next_index: int = 0
        self._load()

    def _load(self) -> None:
        """Load mapping from disk, or initialize with seed symbols."""
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text(encoding="utf-8"))
                self._name_to_code = data.get("name_to_code", {})
                self._code_to_name = data.get("code_to_name", {})
                self._next_index = data.get("next_index", 0)
                logger.info("CodenameMap: loaded %d mappings from %s", len(self._name_to_code), self._file)
                return
            except Exception as e:
                logger.warning("CodenameMap: failed to load %s: %s", self._file, e)

        # Initialize with seed symbols
        for symbol in _SEED_SYMBOLS:
            self._assign(symbol)
        self._save()
        logger.info("CodenameMap: initialized with %d seed symbols", len(self._name_to_code))

    def _save(self) -> None:
        """Persist mapping to disk."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "name_to_code": self._name_to_code,
                "code_to_name": self._code_to_name,
                "next_index": self._next_index,
            }
            self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("CodenameMap: failed to save %s: %s", self._file, e)

    @staticmethod
    def _index_to_code(index: int) -> str:
        """Convert 0-based index to 标的X code.

        0→标的A, 1→标的B, ..., 25→标的Z, 26→标的AA, 27→标的AB, ...
        """
        if index < 26:
            return f"标的{chr(ord('A') + index)}"
        # AA, AB, ... for indices >= 26
        first = (index - 26) // 26
        second = (index - 26) % 26
        return f"标的{chr(ord('A') + first)}{chr(ord('A') + second)}"

    def _assign(self, name: str) -> str:
        """Assign a new code (caller must hold _lock)."""
        code = self._index_to_code(self._next_index)
        self._name_to_code[name] = code
        self._code_to_name[code] = name
        self._next_index += 1
        return code

    def get_code(self, name: str) -> str:
        """Get 标的X code for a coin name. Auto-assigns if new."""
        name = name.upper().strip()
        if not name:
            return ""
        with self._lock:
            if name in self._name_to_code:
                return self._name_to_code[name]
            code = self._assign(name)
            self._save()
            logger.debug("CodenameMap: assigned %s → %s", name, code)
            return code

    def decode(self, code: str) -> str | None:
        """Reverse lookup: 标的A → BTC. Returns None if unknown."""
        with self._lock:
            return self._code_to_name.get(code)

    def replace_names(self, text: str) -> str:
        """Replace all known coin names in text with their 标的X codes.

        Matches coin symbols as standalone tokens — surrounded by non-ASCII
        or non-alphanumeric characters (handles Chinese text where \\b fails).
        Longer names matched first to avoid partial replacements.
        """
        if not text:
            return text
        with self._lock:
            # Sort by length descending to match longer names first
            sorted_names = sorted(self._name_to_code.keys(), key=len, reverse=True)
            for name in sorted_names:
                code = self._name_to_code[name]
                # Use lookahead/lookbehind instead of \b — \b fails with CJK text.
                # Match when symbol is not preceded/followed by ASCII alphanumeric.
                text = re.sub(
                    rf'(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])',
                    code,
                    text,
                    flags=re.IGNORECASE,
                )
        return text

    def replace_codes(self, text: str) -> str:
        """Replace all 标的X codes in text back to real coin names.

        Used internally for debugging/admin views only — never expose
        decoded output to end users.
        """
        if not text:
            return text
        with self._lock:
            for code, name in self._code_to_name.items():
                text = text.replace(code, name)
        return text

    def all_mappings(self) -> dict[str, str]:
        """Snapshot of current name→code mapping."""
        with self._lock:
            return dict(self._name_to_code)


# Module-level singleton — shared across all threads
codename_map = CodenameMap()
