#!/usr/bin/env bash
# Phase B smoke test — all pages (original + auto)
set -u

BASE="${BASE:-http://localhost:3000}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../app" && pwd)"

PASS=0
FAIL=0
FAILED_PAGES=()

# Walk app/{scene}/{slug}/page.tsx → /{scene}/{slug}
for page in "$APP_DIR"/*/*/page.tsx; do
  # Extract scene/slug from path
  rel="${page#"$APP_DIR"/}"
  route="${rel%/page.tsx}"
  url="${BASE}/${route}"
  code=$(curl -s -o /dev/null -w "%{http_code}" "$url")
  if [[ "$code" == "200" ]]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    FAILED_PAGES+=("$route → $code")
  fi
done

echo "=== Phase B Smoke Test ==="
echo "PASS: $PASS / $((PASS + FAIL))"
echo "FAIL: $FAIL"
if [[ $FAIL -gt 0 ]]; then
  echo "FAILED:"
  for p in "${FAILED_PAGES[@]}"; do echo "  $p"; done
  exit 1
fi
echo "ALL GREEN"
