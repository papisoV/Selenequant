#!/usr/bin/env python
"""mypy pre-commit hook 包装：Windows 下 mirrors-mypy 传参丢失的变通。

对 staged 的 py 文件跑 mypy --ignore-missing-imports；无文件时静默通过。
mypy 用系统安装（pip install mypy）或跳过（未装时不阻断提交）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _is_ipc_crash(output: str) -> bool:
    # mypy 2.0 在 Windows 的 IPC 崩坏会打 INTERNAL ERROR，与真实类型错误
    # （"file: error: ..."）区分开。崩坏文案不止 WinError 233 一种（实测还见过
    # 无 WinError 字样的裸 INTERNAL ERROR），故只要出现 INTERNAL ERROR 就重试：
    # mypy 的内部崩溃永远不是用户类型错误，重试不可能掩盖真错。
    return "INTERNAL ERROR" in output


def main() -> int:
    mypy = shutil.which("mypy")
    if not mypy:
        print("[mypy-hook] 系统未安装 mypy，跳过类型检查")
        return 0
    # mypy 默认按 CPU 核数开 worker（本机 32 核 → 32 进程），
    # 2026-08-25 曾与多会话并行叠加导致 65 个 python 进程触发熔断，限 4 个。
    # 2026-08-27：Windows 下 --num-workers>1 会触发 mypy.ipc.IPCException error 233
    # （多进程管道写失败，mypy 2.0 在 Windows 的已知崩溃），改为单 worker 稳定。
    # 2026-09-28：--num-workers 1 自身也开始稳定 233 崩坏（IPC 管道路径），
    # 崩坏时降级为无 --num-workers 的普通模式重试一次（该路径实测稳定）。
    # --follow-imports=silent：错误只报 staged 文件本身（与脚本契约一致），
    # 否则改任何一个测试文件都会经 import 链把无关历史源文件的存量类型债
    # 全部拉进报告，阻断提交。
    base_args = [mypy, "--ignore-missing-imports", "--follow-imports", "silent"]
    r = subprocess.run(
        [*base_args, "--num-workers", "1", *sys.argv[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 and _is_ipc_crash(output):
        print("[mypy-hook] mypy IPC 233 崩坏，降级为普通模式重试")
        r = subprocess.run(
            [*base_args, *sys.argv[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (r.stdout or "") + (r.stderr or "")
    sys.stdout.write(output)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
