"""Check all pending endpoints against running backend on :8088."""
import re
import urllib.request
import urllib.error
import concurrent.futures

PATH = r"<local-path-redacted>"

with open(PATH, "r", encoding="utf-8") as f:
    content = f.read()

# Match lines like: { endpoint: "/api/v1/...", method: "GET", ... status: "pending" },
pattern = re.compile(r'endpoint:\s*"([^"]+)".*?method:\s*"([^"]+)".*?status:\s*"pending"')
pending = pattern.findall(content)
print(f"Total pending: {len(pending)}")


def check(item):
    ep, method = item
    if "{" in ep:
        return (ep, method, "PATH_PARAM")
    url = f"http://localhost:8088{ep}"
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (ep, method, str(resp.status))
    except urllib.error.HTTPError as e:
        return (ep, method, f"HTTP {e.code}")
    except Exception as e:
        return (ep, method, f"ERR {type(e).__name__}")


results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    for r in ex.map(check, pending):
        results.append(r)

ok = [r for r in results if r[2] in ("200", "HTTP 404", "HTTP 405", "PATH_PARAM")]
missing = [r for r in results if r[2] not in ("200", "HTTP 404", "HTTP 405", "PATH_PARAM")]
print(f"\n=== OK (200/404/405/path_param): {len(ok)} ===")
print(f"=== Missing/error: {len(missing)} ===")
for ep, method, status in missing:
    print(f"  {method:6} {ep:60} {status}")
print("\n=== 404 breakdown ===")
nf = [r for r in results if r[2] == "HTTP 404"]
print(f"404 count: {len(nf)}")
for ep, method, status in nf[:15]:
    print(f"  {method:6} {ep}")
