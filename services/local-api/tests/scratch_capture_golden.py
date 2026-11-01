"""Golden-fixture capture harness for the build_ashare_panel extraction
(US-101). NOT collected by pytest (name doesn't match test_*.py).

The scenario (fakes, request) lives in _ashare_panel_golden_scenario.py —
single source of truth, import-free of app/selene deps so it works against
pre-refactor code too. This script wires the scenario onto real modules
(direct attribute patching) and writes the response JSON.

CAPTURE PROCEDURE (only for deliberate behavior changes; the golden must
come from the PRE-refactor implementation):
    1. git show HEAD:services/local-api/app/api/v1/ashare.py > /tmp/orig.py
       (verify: grep -c "def build_ashare_panel" /tmp/orig.py -> 0)
    2. cp services/local-api/app/api/v1/ashare.py /tmp/refactored.py
       cp /tmp/orig.py services/local-api/app/api/v1/ashare.py
    3. python services/local-api/tests/scratch_capture_golden.py
    4. cp /tmp/refactored.py services/local-api/app/api/v1/ashare.py
    5. pytest services/local-api/tests/test_api_ashare_panel_helper.py -q

Usage (from repo root):
    python services/local-api/tests/scratch_capture_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.ashare import router as ashare_router  # noqa: E402
from app.middleware.error_handler import install_error_handlers  # noqa: E402
import selene_space_ashare.fundamentals_pit as fp_mod  # noqa: E402
from selene_space_ashare import data_fetcher as df_mod  # noqa: E402

# scenario single-source-of-truth (same dir)
sys.path.insert(0, str(_SERVICE_ROOT / "tests"))
import _ashare_panel_golden_scenario as sc  # noqa: E402


def main() -> None:
    df_mod.get_daily_klines = sc.fake_klines
    df_mod.get_index_daily = lambda *a, **k: sc.fake_index_daily()
    fp_mod.load_financials = lambda *a, **k: sc.financials()

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_router, prefix="/api/v1")
    client = TestClient(app)

    resp = client.post(f"/api/v1/ashare/backtest?{sc.REQUEST_QS}")
    if resp.status_code != 200:
        print("FAILED:", resp.status_code, resp.text)
        raise SystemExit(1)
    body = resp.json()

    out = _SERVICE_ROOT / "tests" / "fixtures" / "ashare_backtest_golden.json"
    out.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
    print("captured", out)
    print("time_in_market:", body["metrics"]["time_in_market"])
    print("cumulative_return:", body["metrics"]["cumulative_return"])


if __name__ == "__main__":
    main()
