# Contributing

Thanks for your interest in improving SeleneQuant.

## Repository posture

This repository tracks an internally developed project as a series of curated,
verified snapshots rather than its full development history. The practical
consequences:

- **Pull requests cannot be merged directly.** There is no upstream branch that
  maps to the internal development history, so a PR has nowhere here to merge
  into — but a proposed change still has a way in, described below.
- **Issues are welcome and read.** Bug reports, reproducible test failures,
  documentation errors, and license/attribution corrections are all useful —
  see below for what makes a good report.

If you have built something on top of this code, fork it and publish your own
version under the same license — that is exactly what the license is for. If
you want to propose a change, open an issue describing the problem and your
patch approach; substantial, relevant proposals may be absorbed into the next
snapshot with credit.

## Good issue reports

For a bug report, please include:

- the snapshot commit hash (visible on the `main` branch tip),
- your OS and Python/Node versions,
- the command you ran and the full error output,
- what you expected to happen.

For documentation issues, quoting the incorrect sentence and the corrected one
is enough.

## Local setup

The README's Quick start covers running the app:

- Windows launcher: `dev.bat` (starts backend + web app, waits for health check)
- Manual: `pip install -r requirements.txt`, then start
  `services/local-api` (FastAPI, port 8088) and `apps/desktop` (Next.js, port 3000)

Tests: `make test` at the repository root, or
`python -m pytest packages/<name>` for a single package.

## License

By reporting an issue you agree that its contents may be incorporated into
this AGPL-3.0-only project and future snapshots. Code you publish in a fork
remains yours, subject to the license terms governing derived works.

If the maintainer chooses to absorb material you submitted (a patch described
in an issue, for example) into the project's own source tree, you will be asked
to confirm the [Contributor License Agreement](CLA.md) first. That agreement
keeps your copyright yours while granting the maintainer the right to also
license the absorbed work under commercial terms — the reason a single
codebase can serve both the public AGPL edition and a separately licensed
commercial edition. Nothing you submit is absorbed without that confirmation.
