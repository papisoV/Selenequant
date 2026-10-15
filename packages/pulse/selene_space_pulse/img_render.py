# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis (commit ce364e45)
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: stdlib-only, no imgkit dependency.
"""Optional Markdown-to-image rendering for notification channels.

Mirrors daily_stock_analysis ``md2img.py`` (commit ce364e45) engine probe
order: ``wkhtmltoimage`` first, then Playwright. Both engines are optional
external programs detected at runtime; if neither is available, or any
engine call fails, ``render_markdown_image`` returns ``None`` and the
caller falls back to plain-text sending. This module never raises.

Deviations from upstream (forced by the no-new-dependencies constraint):
- DSA wraps markdown in a branded HTML poster via ``src/share_image`` and
  drives wkhtmltoimage through the ``imgkit`` library. Selene embeds a
  minimal HTML wrapper and calls the ``wkhtmltoimage`` binary directly
  via :mod:`subprocess` (HTML is passed on stdin, never argv, matching
  DSA's command-injection-safety note).
- DSA's playwright branch renders a CLI ``playwright screenshot``; the
  Selene branch uses the installed python ``playwright`` package if
  importable, and is skipped entirely otherwise.
"""

from __future__ import annotations

import html as html_module
import importlib.util
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_WKHTMLTOIMAGE_TIMEOUT_SECONDS = 30
_PLAYWRIGHT_TIMEOUT_SECONDS = 30


def _wrap_html(markdown_text: str) -> str:
    """Minimal HTML wrapper around markdown text (poster-layout stand-in)."""
    body = html_module.escape(markdown_text)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<style>body{font-family:sans-serif;margin:24px;white-space:pre-wrap;"
        "color:#111;background:#fff;width:1080px;}</style></head>"
        f"<body>{body}</body></html>"
    )


def _has_playwright() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


def _render_with_wkhtmltoimage(markdown_text: str) -> bytes | None:
    binary = shutil.which("wkhtmltoimage")
    if binary is None:
        return None
    temp_dir = tempfile.mkdtemp(prefix="selene-md2img-")
    output_path = Path(temp_dir) / "render.png"
    try:
        result = subprocess.run(
            [
                binary,
                "--format",
                "png",
                "--encoding",
                "utf-8",
                "--quality",
                "95",
                "-",
                str(output_path),
            ],
            input=_wrap_html(markdown_text).encode("utf-8"),
            capture_output=True,
            timeout=_WKHTMLTOIMAGE_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0 or not output_path.is_file():
            logger.warning(
                "wkhtmltoimage conversion failed: returncode=%s stderr=%s",
                result.returncode,
                (getattr(result, "stderr", b"") or b"").decode(
                    "utf-8", errors="replace"
                )[:200],
            )
            return None
        return output_path.read_bytes()
    except subprocess.TimeoutExpired:
        logger.warning(
            "wkhtmltoimage conversion timed out (%ss)", _WKHTMLTOIMAGE_TIMEOUT_SECONDS
        )
        return None
    except Exception as exc:
        logger.warning("wkhtmltoimage conversion error: %s", exc)
        return None
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _render_with_playwright(markdown_text: str) -> bytes | None:
    temp_dir = tempfile.mkdtemp(prefix="selene-md2img-")
    html_path = Path(temp_dir) / "render.html"
    png_path = Path(temp_dir) / "render.png"
    try:
        html_path.write_text(_wrap_html(markdown_text), encoding="utf-8")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1080, "height": 720})
                page.goto(html_path.resolve().as_uri())
                page.screenshot(path=str(png_path), full_page=True)
            finally:
                browser.close()
        if png_path.is_file():
            return png_path.read_bytes()
        return None
    except Exception as exc:
        logger.warning("playwright image conversion error: %s", exc)
        return None
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def render_markdown_image(md_text: str) -> bytes | None:
    """Render markdown to PNG bytes, or ``None`` when no engine is available.

    Engine probe order mirrors DSA md2img.py: ``wkhtmltoimage`` on PATH
    first, then the playwright package. Any failure yields ``None``;
    callers must fall back to text sending.
    """
    if not md_text or not md_text.strip():
        return None
    image = _render_with_wkhtmltoimage(md_text)
    if image is not None:
        return image
    if _has_playwright():
        return _render_with_playwright(md_text)
    logger.info(
        "No markdown-to-image engine available (wkhtmltoimage/playwright); using text"
    )
    return None
