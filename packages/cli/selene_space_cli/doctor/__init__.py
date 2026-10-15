"""Doctor diagnostics sub-package.

Exposes the shared :class:`CheckResult` value type so command modules and
tests can reference results without importing the checks module directly.
Layout: ``checks`` defines the probes and the ordered registry, ``runner``
formats a report, and ``repair`` implements idempotent fix-ups behind the
``--repair`` flag.
"""

from selene_space_cli.doctor.checks import CheckResult

DOCTOR_SUBMODULES: list[str] = ["checks", "runner", "repair"]

__all__: list[str] = ["CheckResult", "DOCTOR_SUBMODULES"]
