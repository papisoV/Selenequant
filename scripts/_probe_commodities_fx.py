"""US-010 PoC: 大宗/外汇 数据源粒度探测 (一次性脚本,结论记录到 plan).

探测 3 路:
1. AKShare 国内期货分钟线 (futures_zh_minute_sina AU0 1min)
2. AKShare 国内期货主力日线 (futures_main_sina AU0)
3. Yahoo GC=F 日线 (query1.finance.yahoo.com, 纯 HTTP, 零依赖)

禁用 proxy (trust_env=False) — 与本仓库 data_fetcher._fetch_with_retry 同策略。
"""
import requests
orig = requests.Session.__init__
def _no_proxy(self, *a, **k):
    orig(self, *a, **k); self.trust_env = False
requests.Session.__init__ = _no_proxy

def probe(label, fn):
    print(f"\n=== {label} ===")
    try:
        df = fn()
        if df is None or len(df) == 0:
            print("EMPTY (0 rows)")
            return None
        print(f"rows={len(df)} cols={list(df.columns)}")
        print(df.tail(2).to_string())
        return df
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {str(e)[:200]}")
        return None

# 1. AKShare 国内期货分钟线
probe("AKShare 国内期货分钟线 futures_zh_minute_sina AU0 1min",
      lambda: __import__("akshare").futures_zh_minute_sina(symbol="AU0", period="1"))
# 2. AKShare 国内期货主力日线
probe("AKShare 主力日线 futures_main_sina AU0",
      lambda: __import__("akshare").futures_main_sina(symbol="AU0", start_date="20240101", end_date="20240110"))
# 3. Yahoo GC=F 日线 (纯 HTTP)
def yahoo_gc():
    import requests as r
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF"
    params = {"interval": "1d", "range": "1mo"}
    resp = r.get(url, params=params, timeout=15)
    resp.raise_for_status()
    j = resp.json()
    res = j["chart"]["result"][0]
    meta = res["meta"]
    ts = res.get("timestamp", [])[:2]
    print(f"meta.symbol={meta.get('symbol')} currency={meta.get('currency')} regularMarketPrice={meta.get('regularMarketPrice')}")
    print(f"timestamps_sample={ts}")
    q = res.get("indicators", {}).get("quote", [{}])[0]
    print(f"close_sample={q.get('close', [])[:2]}")
    return None
probe("Yahoo GC=F 日线 (query1.finance.yahoo.com)",
      yahoo_gc)

requests.Session.__init__ = orig
print("\n=== probe done ===")
