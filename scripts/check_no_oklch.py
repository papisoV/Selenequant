#!/usr/bin/env python3
"""禁止 oklch() 颜色字符串出现在前端图表组件里。

根因：lightweight-charts / echarts / SVG stroke 等图表库的 color parser
不支持 oklch()，会报 "Failed to parse color: oklch(...)" 导致图表渲染失败。
CSS 引擎（globals.css）支持 oklch，不在本检查范围。

校验范围：apps/desktop/components/**/*.{ts,tsx} + apps/desktop/lib/**/*.{ts,tsx}
白名单：apps/desktop/app/globals.css（CSS 用 oklch 是合法的）

退出码：0=通过，1=发现 oklch 残留
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = [
    ROOT / "apps" / "desktop" / "components",
    ROOT / "apps" / "desktop" / "lib",
]
SCAN_EXTS = {".ts", ".tsx"}
OKLCH_RE = re.compile(r"oklch\(", re.IGNORECASE)


def _strip_line_comment(line: str) -> str:
    """粗略去掉 // ... 和 # ... 行内注释，避免误报文档里的 oklch() 字样。
    不处理块注释跨行情况（极少在图表组件里写多行 oklch 文档）。"""
    for marker in ("//", "#"):
        idx = line.find(marker)
        # 避免把 URL 里的 # 当注释
        if marker == "#" and ("http://" in line or "https://" in line):
            continue
        if idx >= 0:
            line = line[:idx]
    return line


def scan() -> int:
    violations: list[tuple[Path, int, str]] = []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix not in SCAN_EXTS:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            in_block_comment = False
            for i, raw in enumerate(text.splitlines(), start=1):
                line = raw.rstrip()
                # 块注释 /* ... */ 简单状态机
                if "/*" in line and "*/" not in line:
                    in_block_comment = True
                    continue
                if in_block_comment:
                    if "*/" in line:
                        in_block_comment = False
                    continue
                code = _strip_line_comment(line)
                if OKLCH_RE.search(code):
                    violations.append((p.relative_to(ROOT), i, line.strip()))
    if not violations:
        print("[oklch-check] OK: no oklch() in components/lib")
        return 0
    print("[oklch-check] FAIL: oklch() found in chart/library files")
    print("  图表库 (lightweight-charts/echarts/SVG) 不支持 oklch，会渲染失败。")
    print("  请改用 hex 或 rgba，集中管理在 apps/desktop/lib/theme/chart-colors.ts")
    print()
    for path, lineno, line in violations:
        print(f"  {path}:{lineno}: {line}")
    return 1


if __name__ == "__main__":
    sys.exit(scan())
