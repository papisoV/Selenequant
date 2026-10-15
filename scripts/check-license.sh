#!/usr/bin/env bash
# License compliance check for Vibe-Trading subsystem absorption (ADR v2 §9.3).
#
# Runs the 7 checks defined in ADR v2 §9.3 and prints a pass/fail summary.
# Exit code 0 if 7/7 passed, otherwise 1.
#
# Usage:
#   bash scripts/check-license.sh
#
# Windows Git Bash compatible (forward-slash paths only).

set -u

SELENE_ROOT="<local-path-redacted>"
VIBE_ROOT="${SELENE_ROOT}/subsystems/vibe"
ZOO_ROOT="${VIBE_ROOT}/agent/src/factors/zoo"

passed=0
failed=0

report() {
  # $1 = check number, $2 = status (PASS/FAIL), $3 = description
  printf "Check %s: [%s] %s\n" "$1" "$2" "$3"
  if [ "$2" = "PASS" ]; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
  fi
}

# Check 1: selene 根目录有 LICENSE
if [ -f "${SELENE_ROOT}/LICENSE" ]; then
  report 1 PASS "selene root LICENSE exists"
else
  report 1 FAIL "missing ${SELENE_ROOT}/LICENSE"
fi

# Check 2: selene 根目录有 NOTICE
if [ -f "${SELENE_ROOT}/NOTICE" ]; then
  report 2 PASS "selene root NOTICE exists"
else
  report 2 FAIL "missing ${SELENE_ROOT}/NOTICE"
fi

# Check 3: vibe 子系统 LICENSE 保留原 Copyright
if grep -q "Copyright (c) 2026 Vibe-Trading Contributors" "${VIBE_ROOT}/LICENSE" 2>/dev/null; then
  report 3 PASS "vibe LICENSE Copyright line preserved"
else
  report 3 FAIL "vibe LICENSE Copyright line modified or missing"
fi

# Check 4: vibe 子系统 NOTICE 保留 HKUDS 归属
if grep -q "HKUDS" "${VIBE_ROOT}/NOTICE" 2>/dev/null; then
  report 4 PASS "vibe NOTICE HKUDS attribution preserved"
else
  report 4 FAIL "vibe NOTICE HKUDS attribution removed"
fi

# Check 5: Alpha Zoo 4 个子模块 LICENSE.md 都在
zoo_missing=""
for zoo in alpha101 gtja191 qlib158 academic; do
  if [ ! -f "${ZOO_ROOT}/${zoo}/LICENSE.md" ]; then
    zoo_missing="${zoo_missing} ${zoo}"
  fi
done
if [ -z "${zoo_missing}" ]; then
  report 5 PASS "Alpha Zoo 4 sub-module LICENSE.md all present"
else
  report 5 FAIL "missing LICENSE.md for:${zoo_missing}"
fi

# Check 6: UPSTREAM.md 记录了上游 commit hash (markdown bold form: **Vendored version**:)
if grep -Eq '\*\*Vendored version\*\*:' "${VIBE_ROOT}/UPSTREAM.md" 2>/dev/null; then
  report 6 PASS "UPSTREAM.md records vendored version"
else
  report 6 FAIL "UPSTREAM.md missing vendored version marker"
fi

# Check 7: git log 不含 HKUDS commit (确认手动 vendor,不是 subtree)
# 检测 subtree 的可靠信号:HKUDS 作者邮箱,或 git subtree 的 "Squashed '...'" 合并提交。
# 单纯匹配 "Vibe-Trading" 会误报 selene 自己的 absorb 提交 (如 feat(vibe): absorb vibe-trading ...)。
if git -C "${SELENE_ROOT}" log --pretty=format:'%ae|%s' 2>/dev/null | grep -iE "HKUDS|Squashed 'subsystems/vibe" >/dev/null; then
  report 7 FAIL "upstream commits found in git history (subtree was used?)"
else
  report 7 PASS "git log clean of HKUDS-authored / subtree-merge commits"
fi

echo "----"
echo "${passed}/7 passed"
if [ "${passed}" -eq 7 ]; then
  exit 0
else
  exit 1
fi
