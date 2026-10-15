"""Tests for PythonExecTool (PTC) — import whitelist, path guards, registration."""

from __future__ import annotations

import os
import sys

import pytest

from selene_space_agent.tools.base import ToolResult
from selene_space_agent.tools.code_exec import PythonExecTool
from selene_space_types.risk import RiskLevel

KLINES_DB = os.path.abspath("data/klines.db").replace("\\", "/")
STORAGE_DB = os.path.abspath("data/storage.db").replace("\\", "/")


@pytest.fixture()
def tool(tmp_path) -> PythonExecTool:
    import sqlite3

    klines = tmp_path / "klines.db"
    conn = sqlite3.connect(klines)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    return PythonExecTool(
        klines_db_path=str(klines).replace("\\", "/"),
        storage_db_path=str(tmp_path / "storage.db").replace("\\", "/"),
    )


def _run(tool: PythonExecTool, code: str) -> ToolResult:
    return tool.execute({"code": code})


class TestValidation:
    def test_missing_code_returns_errors(self, tool: PythonExecTool) -> None:
        errors = tool.validate_args({})
        assert errors
        assert any("code" in e for e in errors)

    def test_non_string_code_rejected(self, tool: PythonExecTool) -> None:
        result = tool.execute({"code": 123})
        assert result.error

    def test_risk_level_dangerous(self, tool: PythonExecTool) -> None:
        assert tool.risk_level == RiskLevel.DANGEROUS

    def test_name_and_schema(self, tool: PythonExecTool) -> None:
        assert tool.name == "python_exec"
        assert tool.parameters["required"] == ["code"]


class TestImportWhitelist:
    def test_pandas_import_allowed(self, tool: PythonExecTool) -> None:
        result = _run(tool, "from pandas import read_csv\nprint('ok')")
        assert result.error is None
        assert "ok" in result.output

    def test_socket_import_rejected(self, tool: PythonExecTool) -> None:
        result = _run(tool, "import socket\nprint('nope')")
        assert result.error is not None
        assert "not allowed" in result.error

    def test_urllib_root_module_rejected(self, tool: PythonExecTool) -> None:
        result = _run(tool, "from urllib.request import urlopen")
        assert result.error is not None
        assert "urllib" in result.error

    def test_math_allowed(self, tool: PythonExecTool) -> None:
        result = _run(tool, "import math\nprint(math.sqrt(4))")
        assert result.error is None
        assert "2.0" in result.output


class TestPathGuards:
    def test_sqlite_readonly_uri_allowed(self, tool: PythonExecTool) -> None:
        uri = f"file:{tool._allowed_paths[0]}?mode=ro"
        code = (
            "import sqlite3\n"
            f"conn = sqlite3.connect('{uri}', uri=True)\n"
            "print('connected')"
        )
        result = _run(tool, code)
        assert result.error is None
        assert "connected" in result.output

    def test_sqlite_bare_path_rejected(self, tool: PythonExecTool) -> None:
        bare = tool._allowed_paths[0].removeprefix("file:")
        result = _run(tool, f"import sqlite3\nsqlite3.connect('{bare}')")
        assert result.error is not None
        assert "mode=ro" in result.error

    def test_sqlite_foreign_path_rejected(self, tool: PythonExecTool) -> None:
        result = _run(
            tool,
            "import sqlite3\nsqlite3.connect('file:<local-path-redacted>')",
        )
        assert result.error is not None

    def test_sqlite_writable_mode_rejected(self, tool: PythonExecTool) -> None:
        result = _run(
            tool,
            f"import sqlite3\nsqlite3.connect('file:{KLINES_DB}?mode=rwc')",
        )
        assert result.error is not None

    def test_pandas_read_whitelist_path_allowed(self, tool: PythonExecTool) -> None:
        code = f"import pandas as pd\npd.read_parquet('{KLINES_DB}')"
        result = _run(tool, code)
        # parquet engine will fail at runtime on a sqlite file, but the guard
        # must let it through (error comes from execution, not validation)
        assert result.error is None or "mode=ro" not in (result.error or "")

    def test_pandas_read_foreign_path_rejected(self, tool: PythonExecTool) -> None:
        result = _run(tool, "import pandas as pd\npd.read_csv('<local-path-redacted>')")
        assert result.error is not None

    def test_pandas_to_csv_rejected(self, tool: PythonExecTool) -> None:
        result = _run(
            tool, "import pandas as pd\ndf = pd.DataFrame()\ndf.to_csv('x.csv')"
        )
        assert result.error is not None
        assert "to_csv" in result.error


class TestExecution:
    def test_stdout_truncated(self, tool: PythonExecTool) -> None:
        code = "print('x' * 5000)"
        result = _run(tool, code)
        assert result.error is None
        assert len(result.output) <= 2000

    def test_timeout_returns_error(self, tool: PythonExecTool) -> None:
        result = _run(tool, "while True: pass")
        assert result.error is not None
        assert "timed out" in result.error

    def test_runtime_error_returns_stderr(self, tool: PythonExecTool) -> None:
        result = _run(tool, "1 / 0")
        assert result.error is not None
        assert "ZeroDivisionError" in result.error

    def test_honest_code_runs(self, tool: PythonExecTool) -> None:
        uri = f"file:{tool._allowed_paths[0]}?mode=ro"
        code = (
            "import json, sqlite3\n"
            f"conn = sqlite3.connect('{uri}', uri=True)\n"
            "print(json.dumps({'tables': [r[0] for r in conn.execute("
            "\"SELECT name FROM sqlite_master WHERE type='table'\")]}))"
        )
        result = _run(tool, code)
        assert result.error is None


class TestRegistration:
    def test_register_in_catalog(self) -> None:
        from selene_space_agent.tool_catalog import ToolCatalog

        catalog = ToolCatalog()
        tool = PythonExecTool(klines_db_path=KLINES_DB, storage_db_path=STORAGE_DB)
        before = len(catalog)
        catalog.register(tool)
        assert "python_exec" in catalog.tool_names()
        assert len(catalog) == before + 1

    def test_duplicate_registration_raises(self) -> None:
        from selene_space_agent.tool_catalog import ToolCatalog, ToolCatalogError

        catalog = ToolCatalog()
        tool = PythonExecTool(klines_db_path=KLINES_DB, storage_db_path=STORAGE_DB)
        catalog.register(tool)
        with pytest.raises(ToolCatalogError):
            catalog.register(
                PythonExecTool(klines_db_path=KLINES_DB, storage_db_path=STORAGE_DB)
            )

    def test_register_agent_tools_wires_python_exec(self) -> None:
        """Main-path wiring: main.py's _register_agent_tools registers python_exec.

        local-api is not on agent-runtime's test path (and main.py's import chain
        is heavy), so assert on the registration pattern in source instead of
        importing the function. ToolCatalogError-on-duplicate is covered above.
        """
        import re
        from pathlib import Path

        main_py = (
            Path(__file__).resolve().parents[3]
            / "services"
            / "local-api"
            / "app"
            / "main.py"
        )
        src = main_py.read_text(encoding="utf-8")

        def_block = re.search(
            r"def _register_agent_tools\(.*?(?=\n(?:def |async def ))",
            src,
            re.DOTALL,
        )
        assert def_block, "_register_agent_tools not found in main.py"
        body = def_block.group(0)
        assert "PythonExecTool(" in body
        assert "tc.register(" in body
        # 注册调用包裹构造(tc.register(\n    PythonExecTool(...))
        assert re.search(r"tc\.register\(\s*PythonExecTool\(", body)


class TestMemoryBound:
    def test_normal_code_still_works_under_job(self, tool: PythonExecTool) -> None:
        result = _run(tool, "print(sum(range(100)))")
        assert result.output.strip() == "4950"
        assert result.error is None

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="memory cap is enforced by a Windows Job Object "
        "(_JobMemoryLimiter.__enter__ returns None elsewhere), so on other "
        "platforms a 2GB allocation simply succeeds",
    )
    def test_memory_hog_killed_at_cap(self, tool: PythonExecTool) -> None:
        # 2GB bytearray > 1GB job cap: allocation must fail (or process die),
        # result is an error — never an OOM of the host.
        result = _run(tool, "x = bytearray(2 * 1024 * 1024 * 1024)\nprint(len(x))")
        assert result.error

    def test_job_creation_failure_falls_back(self, tool, monkeypatch) -> None:
        from selene_space_agent.tools import code_exec

        def boom(*a, **kw):
            raise OSError("no job objects")

        monkeypatch.setattr(code_exec, "_JobMemoryLimiter", boom)
        result = _run(tool, "print(1 + 1)")
        assert result.output.strip() == "2"
