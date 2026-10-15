# Security Policy

## Supported version

Only the latest published snapshot on `main` is supported. This repository
publishes curated snapshots (see the posture note in the README): there are no
long-term support branches or patch releases. Security fixes land in the next
snapshot.

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Use GitHub's private vulnerability reporting on this repository
(Security → Report a vulnerability), or contact the maintainer directly if that
is unavailable. Include:

- the affected component and file path(s),
- the steps or input needed to reproduce,
- the impact you believe it enables.

You will receive an acknowledgment within 7 days. If the report is confirmed,
the fix will land in the next snapshot and the report will be credited in the
release notes unless you prefer otherwise.

## Scope

In scope: the Python packages under `packages/`, the FastAPI backend under
`services/local-api/`, and the desktop/web app under `apps/desktop/`.

Out of scope:

- anything requiring a compromised local machine (this is a local-first app —
  the backend binds to `127.0.0.1` by design),
- missing hardening that is not a concrete vulnerability,
- issues in third-party dependencies; report those upstream, but you are
  welcome to also notify us so the next snapshot picks up the fix.

## What this project is not

This software is research tooling for quantitative trading experiments. It is
not audited for handling of production trading credentials, and nothing here
should be used to place real trades without your own review of the risk. The
license's "no warranty" terms apply without exception.
