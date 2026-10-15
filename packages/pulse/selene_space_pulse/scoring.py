"""Signal scoring for pulse assets.

Thin re-export of the canonical implementation in
``selene_space_stock.scoring``.  This module previously held a byte-identical
copy of that scoring body, so the two could silently drift apart.  Keeping a
single source and re-exporting it here preserves the ``ai_market_pulse.scoring``
/ ``selene_space_pulse.scoring`` import paths that existing callers rely on.
"""

from __future__ import annotations

from selene_space_stock.scoring import score_asset

__all__ = ["score_asset"]
