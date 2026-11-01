#!/bin/bash
# Phase 1 冒烟测试：45 页 HTTP 200
# 用法：等 npm run dev 起来后跑 bash apps/desktop/scripts/smoke-45.sh

BASE="http://localhost:3000"
PAGES=(
  # watch 20
  watch/kf watch/kfi watch/mk watch/liqmap watch/liqratio watch/gex
  watch/tac watch/sig watch/vchart watch/poly watch/dex

  watch/onchain watch/us-radar watch/hyperliquid
  watch/cvd watch/heatmap watch/liquidations
  # research 9
  research/rsch research/pa research/strat research/wid
  research/alpha_mine research/portfolio
  research/serenity research/qevolve research/factors
  # trade 9
  trade/pos trade/paper trade/trd trade/grid trade/tof trade/live_log
  trade/backtests trade/dex-trading trade/optimizations
  # system 9
  system/dash system/skill system/mcp system/set
  system/notifications system/memory system/marketplace
  system/agent system/chat-sessions
)

PASS=0
FAIL=0
FAILED=()

for p in "${PAGES[@]}"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$p")
  if [ "$code" = "200" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    FAILED+=("$p ($code)")
  fi
done

echo "=== Phase 1 Smoke Test ==="
echo "PASS: $PASS / ${#PAGES[@]}"
echo "FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "FAILED:"
  for f in "${FAILED[@]}"; do echo "  $f"; done
  exit 1
fi
echo "ALL GREEN"
