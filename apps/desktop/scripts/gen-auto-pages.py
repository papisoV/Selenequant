"""Phase B step 2 — for each slug in BACKEND_AUTO_PAGES, generate app/{scene}/{slug}/page.tsx.

Each page is 7 lines using PageRenderer.
"""
from __future__ import annotations
import re
from pathlib import Path

APP_DIR = Path(r"<local-path-redacted>")
AUTO_FILE = Path(r"<local-path-redacted>")

# Parse slug + scene from BACKEND_AUTO_PAGES
content = AUTO_FILE.read_text(encoding="utf-8")
pattern = re.compile(r'slug:\s*"([^"]+)".*?scene:\s*"([^"]+)"')
matches = pattern.findall(content)

created = 0
skipped = 0
for slug, scene in matches:
    # slug may have slashes/hyphens — convert to path segments
    # e.g. "agent-analysis-status-analysis_id" → app/system/agent-analysis-status-analysis_id/page.tsx
    page_dir = APP_DIR / scene / slug
    page_file = page_dir / "page.tsx"
    if page_file.exists():
        skipped += 1
        continue
    page_dir.mkdir(parents=True, exist_ok=True)
    page_file.write_text(
        'import { PageRenderer } from "@/components/shared/page-renderer";\n'
        'import { ALL_PAGES } from "@/lib/page-manifest";\n'
        "\n"
        "export default function Page() {\n"
        f'  const meta = ALL_PAGES.find((p) => p.slug === "{slug}")!;\n'
        "  return <PageRenderer meta={meta} />;\n"
        "}\n",
        encoding="utf-8",
    )
    created += 1

print(f"Created {created} page.tsx files, skipped {skipped} existing")
