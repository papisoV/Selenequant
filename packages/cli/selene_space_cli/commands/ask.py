"""`selenespace ask` — talk to the agent loop from the terminal.

Same react loop as the GUI chat panel, rendered as terminal text. Zero
backend changes: this is the second face of the loop, not a second brain.
"""

from __future__ import annotations

import json
import sys

import click
import httpx

from selene_space_cli.ansi import dim, err, info, ok, warn
from selene_space_core.config import load_config

_TIMEOUT_CONNECT_S = 5.0
_TIMEOUT_READ_S = 600.0  # agent rounds can think long between chunks


def _render_event(event_type: str, event: dict, written: list) -> None:
    """Render one SSE event as terminal text. ``written`` tracks deltas."""
    data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
    if event_type == "text_delta":
        text = data.get("text", "")
        if text:
            sys.stdout.write(text)
            sys.stdout.flush()
            written.append(text)
    elif event_type == "thinking":
        content = data.get("content", "")
        if content:
            click.echo(dim(f"  … {content}"))
    elif event_type == "tool_call":
        name = data.get("name", "?")
        click.echo(info(f"  → tool: {name}"))
    elif event_type == "tool_result":
        summary = str(data.get("summary", data.get("output", "")))[:200]
        if summary:
            click.echo(dim(f"  ← {summary}"))
    elif event_type == "reflection":
        content = data.get("content", "")
        if content:
            click.echo(dim(f"  ⋯ {content}"))
    # done / unknown types: rendered at end


def _prompt_and_resolve_approval(
    client: "httpx.Client",
    base: str,
    session_id: str,
    event: dict,
) -> None:
    """Render a write-tool approval request and resolve it over HTTP.

    B0: the backend pauses the turn on ``tool_approval_request`` and only
    proceeds once ``POST /agent/approve`` resolves the gate. A CLI that
    ignores this event hangs until the read timeout — so ``ask`` must ask the
    human. Default is **deny** (bare Enter rejects), matching the gate's
    fail-closed posture: an unattended terminal must never approve a write.
    """
    data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
    tool_name = str(data.get("tool_name", "?"))
    tool_call_id = str(data.get("tool_call_id", ""))
    arguments = data.get("arguments", {})
    click.echo()
    click.echo(warn(f"  ⚠ Agent 请求执行写操作:{tool_name}"))
    if isinstance(arguments, dict) and arguments:
        click.echo(dim(f"    参数: {json.dumps(arguments, ensure_ascii=False)}"))
    approved = click.confirm("  批准执行?", default=False)
    try:
        client.post(
            f"{base}/api/v1/agent/approve",
            json={
                "session_id": session_id,
                "tool_call_id": tool_call_id,
                "approve": approved,
            },
        )
    except httpx.HTTPError as exc:
        click.echo(err(f"  审批请求失败:{exc}"))
        return
    click.echo(ok("  已批准") if approved else warn("  已拒绝"))


def _iter_sse(lines):
    """Yield (event_type, data_dict) from an SSE line iterator.

    Real httpx ``iter_lines()`` yields str; tests and raw transports may
    hand over bytes — accept both.
    """
    event_type = ""
    for raw in lines:
        line = (
            raw.decode("utf-8", errors="replace")
            if isinstance(raw, (bytes, bytearray))
            else raw
        )
        line = line.rstrip("\r\n")
        if line.startswith("event: "):
            event_type = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            payload = line[len("data: ") :]
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {}
            yield event_type, data
            event_type = ""


@click.command("ask")
@click.argument("message", nargs=-1, required=True)
@click.option("--session", default=None, help="Existing session ID to continue.")
def ask(message: tuple[str, ...], session: str | None) -> None:
    """Ask the agent a question; the answer streams into the terminal."""
    cfg = load_config()
    base = f"http://localhost:{cfg.api_port}"
    text = " ".join(message)
    body: dict = {"message": text}
    if session:
        body["session_id"] = session

    written: list[str] = []
    tool_uses: dict[str, int] = {}
    try:
        with httpx.Client(
            timeout=httpx.Timeout(_TIMEOUT_READ_S, connect=_TIMEOUT_CONNECT_S)
        ) as client:
            with client.stream(
                "POST", f"{base}/api/v1/agent/stream", json=body
            ) as response:
                response.raise_for_status()
                new_session = response.headers.get("X-Session-ID") or session or ""
                for event_type, data in _iter_sse(response.iter_lines()):
                    _render_event(event_type, data, written)
                    if event_type == "tool_approval_request":
                        _prompt_and_resolve_approval(client, base, new_session, data)
                    elif event_type == "tool_call":
                        payload = (
                            data.get("data", {})
                            if isinstance(data.get("data"), dict)
                            else {}
                        )
                        name = payload.get("name")
                        if name:
                            tool_uses[name] = tool_uses.get(name, 0) + 1
    except httpx.HTTPError as exc:
        raise click.ClickException(
            f"cannot reach agent at {base} — is the API running? ({exc})"
        ) from exc

    click.echo()  # newline after streamed deltas
    if not written:
        click.echo("(no answer text)")
    if tool_uses:
        summary = " · ".join(f"{name} ×{n}" for name, n in tool_uses.items())
        click.echo(dim(f"provenance: {summary}"))
    if new_session:
        click.echo(
            dim(f"session: {new_session}  (continue with --session {new_session})")
        )
