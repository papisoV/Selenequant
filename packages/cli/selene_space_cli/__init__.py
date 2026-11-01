"""SeleneQuant command-line interface package.

The distribution ships a single console script, ``selenespace``, whose
command tree lives in :mod:`selene_space_cli.commands` and whose doctor
diagnostics live in :mod:`selene_space_cli.doctor`. This top level only
carries package metadata.
"""

from __future__ import annotations

PACKAGE_PURPOSE = "local-first crypto quant research CLI"
__version_info_source__ = "selene_space_cli.constants.cli_version"
