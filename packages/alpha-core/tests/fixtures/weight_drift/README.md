# WeightDriftBacktest 黄金 fixture

由 AlphaPurify(真实 polars 实现)固定 seed 生成,供 Selene 侧 pandas 移植版
`selene_space_alpha.mining.weight_drift_backtest.WeightDriftBacktest` 做逐位
parity 比对。Selene 测试只读本目录的 parquet/json,**不 import AlphaPurify、不 import polars**。

## 守卫键(Guard Keys)

| 键 | 值 | 用途 |
|------|-----|--------|
| `ap_version` | `1.0.2` | AlphaPurify 版本,不匹配 → 应重新生成 fixture |
| `seed` | `42` | 数据生成种子,同 seed 必须复现相同面板 |
| `tolerance.strict` | `1e-8` | golden 比对的严格容差(plan §3.2) |

## 生成方式

```bash
# 在 AlphaPurify 仓库内运行(该环境有 polars + pyarrow)
python scripts/gen_weight_drift_fixtures.py \
  --out-dir "<local-path-redacted>"
```

- 面板:N=102 交易日 × 50 symbol;`price` 为对数正态(严格为正),`factor` 随机。
- `tax_rate=0.001`、direction=`"sell"`,使 buy/sell 费率映射在 parity 中呈非对称,
  可覆盖三种方向映射。
- `epoch=10`,`w0_halflife=0`(Selene 扩展关闭衰减,与 AlphaPurify 原版逐位等价)。
- 先写 parquet 再重读,保证两侧从相同字节开始。

## 文件清单

| 文件 | 角色 |
|--------|------|
| `factor_df.parquet` | 输入:全量因子面板 `[trade_date, symbol, factor]` |
| `ret_df.parquet` | 输入:全量前向收益 `[trade_date, symbol, fut_ret_1]`(最后一日 NaN,移植版自行丢弃) |
| `base_df.ipc` | AlphaPurify 侧内部 Arrow IPC 缓冲(生成期读取用);Selene 测试**不读它** |
| `meta.json` | 守卫键 + 输入/期望文件路径 + 期望标量(mean_ret / avg_turnover_ls) |
| `expected/df_result.parquet` | 期望:逐日分位收益表(101 × 37) |
| `expected/df_agg.parquet` | 期望:逐日换手/成本聚合(11 × 25) |
| `expected/stats_ls.json` | 期望:多空腿年化统计 |
| `expected/stats_l.json` | 期望:多头腿年化统计 |
| `expected/stats_s.json` | 期望:空头腿年化统计(`Ann. Turnover` 键为 AlphaPurify 源 quirk) |

## 比对注意事项

- polars pivot 列序**不排序**(如 `buy_2, buy_4, buy_1`),`test_weight_drift_backtest.py`
  的 `_assert_frame_close` 按**列名 + 行日期**对齐后再 `np.allclose(equal_nan=True)`。
- 数值容差 1e-8;统计值同容差。
- `base_df.ipc` 是生成中间产物,重新生成后若不需要可删除(测试不依赖)。
