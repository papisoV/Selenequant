"""PTC tool — agent writes Python to process local data, results (not raw rows) enter context."""

from __future__ import annotations

import ast
import subprocess
import sys
from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult

_ALLOWED_ROOT_MODULES = frozenset(
    {"math", "statistics", "json", "re", "datetime", "sqlite3", "pandas", "numpy"}
)

_STDOUT_LIMIT = 2000
_TIMEOUT_SECONDS = 30
_MEMORY_LIMIT_MB = 1024


class _JobMemoryLimiter:
    """Windows Job Object that hard-caps a child process's committed memory.

    KILL_ON_JOB_CLOSE ensures the child dies with the job handle even if
    the parent is killed first. Usable only on win32; ``__enter__`` returns
    None when the job could not be created (caller falls back).
    """

    def __init__(self, limit_mb: int) -> None:
        self._limit_mb = limit_mb
        self._handle = None

    def __enter__(self) -> "_JobMemoryLimiter | None":
        if sys.platform != "win32":
            return None
        import ctypes

        try:
            kernel32 = ctypes.windll.kernel32
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return None

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.POINTER(ctypes.c_uint64)),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    (n, ctypes.c_uint64)
                    for n in (
                        "ReadOperationCount",
                        "WriteOperationCount",
                        "OtherOperationCount",
                        "ReadTransferCount",
                        "WriteTransferCount",
                        "OtherTransferCount",
                    )
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.ProcessMemoryLimit = self._limit_mb * 1024 * 1024
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            ok = kernel32.SetInformationJobObject(
                job,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                kernel32.CloseHandle(job)
                return None
            self._handle = job
            return self
        except Exception:
            return None

    def assign(self, proc: subprocess.Popen) -> bool:
        import ctypes

        handle = getattr(proc, "_handle", None)
        if not handle:
            return False
        try:
            return bool(
                ctypes.windll.kernel32.AssignProcessToJobObject(
                    self._handle, int(handle)
                )
            )
        except Exception:
            return False

    def __exit__(self, *exc: object) -> None:
        if self._handle:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None


def _run_memory_bounded(code: str) -> subprocess.CompletedProcess:
    """Run code in a subprocess with a hard memory cap on Windows.

    Falls back to a plain subprocess.run when the Job Object cannot be
    created (timeout still applies; memory cap degrades to none).
    """
    try:
        job = _JobMemoryLimiter(_MEMORY_LIMIT_MB).__enter__()
    except Exception:
        job = None
    if job is None:
        return subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    try:
        try:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            return subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        if not job.assign(proc):
            proc.kill()
            proc.wait()
            return subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        try:
            stdout, stderr = proc.communicate(timeout=_TIMEOUT_SECONDS)
            return subprocess.CompletedProcess(
                proc.args, proc.returncode, stdout, stderr
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise
    finally:
        job.__exit__()


def _iter_root_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if (
                node.module is None
            ):  # relative import — deny (no package context in -I mode)
                yield ""
            else:
                yield node.module.split(".")[0]


def _is_readonly_uri(literal: str, allowed_paths: list[str]) -> bool:
    if not literal.startswith("file:"):
        return False
    for allowed in allowed_paths:
        prefix = f"file:{allowed}?mode=ro"
        if literal == prefix or literal.startswith(prefix + "&"):
            # ponytail: exact-prefix match; no URI-decoding — paths are self-injected
            return True
    return False


def _resolve_path_arg(node: ast.expr) -> str | None:
    """Return the literal string of a path argument, or None if not a string literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class PythonExecTool(BaseTool):
    """Run LLM-written Python in a subprocess against whitelisted local read-only data.

    Guardrails are mistake-prevention (deny-by-default), not an adversarial sandbox:
    the local, single-user threat model is accidental deletes/writes, not evasion.
    """

    def __init__(self, *, klines_db_path: str, storage_db_path: str) -> None:
        import os

        self._allowed_paths = [
            os.path.abspath(p).replace("\\", "/")
            for p in (klines_db_path, storage_db_path)
        ]
        self._description = (
            "Execute Python code to analyze local data (klines.db / storage DB). "
            "Code runs in a restricted subprocess with deny-by-default import "
            "whitelist (math/statistics/json/re/datetime/sqlite3/pandas/numpy) and "
            "read-only path guards: sqlite3.connect must use the injected "
            "file:...?mode=ro URI; pandas.read_* paths must hit the injected DBs; "
            "pandas.to_* is rejected. Raw data rows stay out of LLM context — only "
            "your code and its printed summary enter the conversation. Guardrails "
            "prevent mistakes, they are not an adversarial sandbox: do not attempt "
            "filesystem writes or network access; memory is hard-capped at "
            f"{_MEMORY_LIMIT_MB}MB via a Windows Job Object (when available) and "
            "the run is killed at the 30s timeout."
        )

    @property
    def name(self) -> str:
        return "python_exec"

    @property
    def description(self) -> str:
        return self._description

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.DANGEROUS

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "description": {
                    "type": "string",
                    "description": "What this snippet computes (say it before running)",
                },
            },
            "required": ["code"],
        }

    def _validate_imports(self, code: str) -> str | None:
        tree = ast.parse(code)
        for root in _iter_root_modules(tree):
            if root not in _ALLOWED_ROOT_MODULES:
                return (
                    f"Import of '{root}' is not allowed. Whitelist: "
                    f"{sorted(_ALLOWED_ROOT_MODULES)}"
                )
        return None

    def _validate_path_usage(self, code: str) -> str | None:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            dotted = None
            if isinstance(func, ast.Attribute):
                dotted = func.attr
                base = func.value
                base_name = base.id if isinstance(base, ast.Name) else None
            elif isinstance(func, ast.Name):
                dotted = func.id
                base_name = None
            else:
                continue

            # sqlite3.connect(<literal>, ...) — must be the injected read-only URI
            # (keywords allowed: sqlite3.connect(uri, uri=True) is the correct call;
            #  a URI string with uri=False fails at runtime = fail-safe)
            if dotted == "connect" and base_name == "sqlite3" and node.args:
                path = _resolve_path_arg(node.args[0])
                if path is None or not _is_readonly_uri(path, self._allowed_paths):
                    return (
                        "sqlite3.connect must use the injected read-only URI with "
                        "uri=True: sqlite3.connect('file:<db>?mode=ro', uri=True). "
                        f"Injected DBs: {self._allowed_paths}"
                    )
                continue

            if dotted and dotted.startswith("to_"):
                # Any .to_* call is a write face (df.to_csv, arr.tofile, ...) — reject.
                # ponytail: broad by design; legit in-memory to_* uses are rare in
                # data-analysis snippets and naming a new one costs one whitelist line.
                return f".{dotted}(...) write operations are not allowed"

            if dotted and dotted.startswith("read_"):
                # Any .read_* call — path arg must be an injected read-only URI.
                if node.args:
                    path = _resolve_path_arg(node.args[0])
                    if path is not None and not _is_readonly_uri(
                        path, self._allowed_paths
                    ):
                        return (
                            f".{dotted} path must be one of the injected read-only "
                            f"URIs: {self._allowed_paths}"
                        )
                continue

        return None

    def execute(self, args: dict[str, Any]) -> ToolResult:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(
                output="", error="Parameter 'code' must be non-empty code"
            )

        import_error = self._validate_imports(code)
        if import_error:
            return ToolResult(output="", error=import_error)

        path_error = self._validate_path_usage(code)
        if path_error:
            return ToolResult(output="", error=path_error)

        try:
            completed = _run_memory_bounded(code)
        except subprocess.TimeoutExpired:
            return ToolResult(
                output="",
                error=f"Execution timed out after {_TIMEOUT_SECONDS}s",
            )

        stdout = (completed.stdout or "")[-_STDOUT_LIMIT:]
        stderr = (completed.stderr or "")[-_STDOUT_LIMIT:]

        if completed.returncode != 0:
            return ToolResult(
                output=stdout, error=stderr or f"exit {completed.returncode}"
            )
        return ToolResult(output=stdout, error=None)
