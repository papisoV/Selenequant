# TGWatcher E2E Tests

Playwright end-to-end tests for the TGWatcher Web UI.

## Setup

```bash
npm install
npx playwright install chromium
```

## Running

Start the Flask dev server in one terminal:

```bash
python -m tgwatcher.web.app
```

Run tests in another:

```bash
npm test              # headless
npm run test:headed   # visible browser
npm run test:debug    # step debugger
```

## Test Coverage

| Spec | AC | What it verifies |
|------|----|----|
| `test_sse_auth.spec.js` | AC-1 | `/api/events` request URL has no `?token=`, auth via `Authorization` header |
| `test_loadMessages_seq.spec.js` | AC-2 | Rapid chat switches show final chat's messages, no stale response |
| `test_sse_reconnect.spec.js` | AC-3 | SSE reconnect backoff 1s→2s→4s→8s→15s, uses `page.clock` virtual time |

## Notes

- Tests run sequentially (`workers: 1`) because SSE is stateful.
- `test_sse_reconnect` uses `page.clock.fastForward()` to avoid real wall-clock waits.
- Auth auto-login via `/api/auth/bootstrap` works on localhost; tests assume dev server is running.
