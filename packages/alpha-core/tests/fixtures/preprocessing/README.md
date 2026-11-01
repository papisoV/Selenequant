# preprocessing golden fixtures

Golden outputs from the **real AlphaPurify (polars)** implementations of the
13 sklearn-bridged neutralize methods, used to verify the Selene pandas port
is numerically equivalent.

## Regenerate

Run from the AlphaPurify repo (which owns `scripts/gen_preprocessing_fixtures.py`):

```
cd <local-path-redacted>
python scripts/gen_preprocessing_fixtures.py \
  --out-dir <local-path-redacted>
```

The generator loads `APr_utils.py` by file path (bypassing the package
`__init__`, which pulls in duckdb). It writes the synthetic input, re-reads it
so both sides see identical bytes, runs each method across the
`scale_X × dummy_cols` quadrant matrix, and emits `meta.json`.

## Guard key & tolerance

| Guard | Value | Behavior |
|-------|-------|----------|
| `sklearn_version` (exact) | e.g. `1.7.0` | Match → **strict** `1e-6` compare |
| — (mismatch) | running ≠ fixture | **Relaxed** `1e-2` compare — never silently skipped; fails only if relaxed also mismatches |
| `ap_version` | `1.0.2` | Informational (AlphaPurify release the fixtures were generated from) |

## Layout

```
input.parquet          # fixed-seed synthetic input (250 rows: 5 dates × 50 symbols)
meta.json              # columns, quadrants, per-method params, guard key, tolerance
expected/{key}__scaleX_{true,false}__dummy_{has,empty}.parquet   # 13 × 4 = 52
```

## Data

- Seed `42`, 5 dates × 50 symbols = 250 rows, **pure finite data** (no NaN).
  NaN is deliberately absent: polars `drop_nulls` keeps NaN while pandas
  `dropna` drops it, so NaN rows would break parity. Null-handling edge cases
  are covered by `test_preprocessing_reference.py`.
- Stochastic methods (`ransac`/`theilsen`/`random_forest`/`GBDT`/`PCA`/`ICA`)
  are pinned with `random_state=0` in `meta.json`; the parity test replays
  those exact params.
- `neutralize_multiOLS` and `neutralize_partialcorrelation` are intentionally
  **not** in this fixture — they use pure numpy/scipy and are verified against
  hand-computed references in `test_preprocessing_reference.py`.
