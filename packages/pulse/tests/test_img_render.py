from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from selene_space_pulse import img_render


def test_render_returns_none_when_no_engine_available(monkeypatch) -> None:
    monkeypatch.setattr(img_render.shutil, "which", lambda name: None)
    monkeypatch.setattr(img_render.importlib.util, "find_spec", lambda name: None)

    assert img_render.render_markdown_image("# Hello") is None


def test_render_returns_none_when_wkhtmltoimage_subprocess_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        img_render.shutil, "which", lambda name: "/usr/bin/wkhtmltoimage"
    )
    monkeypatch.setattr(img_render.importlib.util, "find_spec", lambda name: None)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, returncode=1, stdout=b"", stderr=b"boom"
        )

    monkeypatch.setattr(img_render.subprocess, "run", fake_run)

    assert img_render.render_markdown_image("# Hello") is None


def test_render_returns_none_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        img_render.shutil, "which", lambda name: "/usr/bin/wkhtmltoimage"
    )
    monkeypatch.setattr(img_render.importlib.util, "find_spec", lambda name: None)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="wkhtmltoimage", timeout=30)

    monkeypatch.setattr(img_render.subprocess, "run", fake_run)

    assert img_render.render_markdown_image("# Hello") is None


def test_render_returns_none_for_empty_input(monkeypatch) -> None:
    # No engine probing should even happen for empty content.
    monkeypatch.setattr(
        img_render.shutil,
        "which",
        lambda name: pytest.fail("engine probe should not run for empty input"),
    )

    assert img_render.render_markdown_image("") is None
    assert img_render.render_markdown_image("   ") is None


def test_render_does_not_raise_when_run_raises_unexpected_error(monkeypatch) -> None:
    monkeypatch.setattr(
        img_render.shutil, "which", lambda name: "/usr/bin/wkhtmltoimage"
    )
    monkeypatch.setattr(img_render.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(
        img_render.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no exec")),
    )

    assert img_render.render_markdown_image("# Hello") is None


def test_render_uses_stdin_not_argv_for_html(monkeypatch, tmp_path) -> None:
    # DSA security note: HTML must travel via stdin, never argv.
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        img_render.shutil, "which", lambda name: str(tmp_path / "wkhtmltoimage")
    )
    monkeypatch.setattr(img_render.importlib.util, "find_spec", lambda name: None)

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        # The engine writes to the output path given as the last argv item.
        subprocess_safe_path = Path(str(argv[-1]))
        subprocess_safe_path.write_bytes(b"png-bytes")

        class _Result:
            returncode = 0
            stderr = b""

        return _Result()

    monkeypatch.setattr(img_render.subprocess, "run", fake_run)

    result = img_render.render_markdown_image("# Hello")

    assert result == b"png-bytes"
    argv = captured["argv"]
    assert str(tmp_path / "wkhtmltoimage") in str(argv[0])
    # The markdown content is only in stdin input, never in argv.
    assert all("Hello" not in str(part) for part in argv)
    assert b"Hello" in captured["input"]  # type: ignore[operator]
