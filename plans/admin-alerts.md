# Admin alert system

Goal: a simple, centralized way to surface critical problems. When something critical is logged, show a short message in-game and optionally POST a JSON alert to a user-defined webhook (which could end up in Discord, Teams, Slack or anything else). This is deliberately basic, not a first-class notification platform.

## Behavior

- A `logging.Handler` (new module `mc/alerts.py`) attached alongside the existing console and file handlers in `run_mc_server.py::main()` to the `mc` logger and the `run_mc_server` logger only (not the `out` logger, which carries raw server stdout). Handles records at or above a configurable level, default `CRITICAL`.
- Two sinks per alert:
  1. **In-game:** a short `say` command to the running server. Strip newlines and control characters, truncate to ~100 characters (add an ellipsis), prefix with something like `[Admin alert]`. Only send if a runtime is running; silently skip otherwise. The handler must not import `run_mc_server`. Provide a registration hook (e.g. `alerts.set_in_game_sender(callable | None)`) that `run_mc_server.py` calls whenever `_current_runtime` is created or replaced (initial start, crash restart, `restart_sequence`), and with `None` while it is stopped. Sending must be exception-safe.
  2. **Webhook:** HTTP POST of JSON to the URL in `MC_ALERT_WEBHOOK_URL`.
- **If `MC_ALERT_WEBHOOK_URL` is unset or empty (the default), there is no HTTP route:** the webhook sink is simply disabled, with no errors or warnings on every alert. Log a single INFO line at startup stating whether the webhook is enabled (never print the full URL; it may contain a secret token, show scheme and host only). An invalid URL (not http/https) logs one warning at startup and disables the sink.

## Payload

JSON body, POSTed with `Content-Type: application/json`:

```json
{
  "content": "<message, truncated to 1900 chars>",
  "text": "<same message>",
  "level": "CRITICAL",
  "logger": "mc.update",
  "message": "<full log message>",
  "host": "<hostname>",
  "server_version": "<current version or null>",
  "timestamp": "<UTC ISO 8601>",
  "repeat_count": 1,
  "traceback": "<formatted exception text or null>"
}
```

`content` is for Discord-style webhooks and `text` for Slack/Teams-style; both carry the same short human-readable message. Truncate traceback sensibly (e.g. last ~3000 chars). Get the server version from `mc.paths.get_current_version()` wrapped in try/except.

## Robustness requirements

- **Never block or crash the server.** `emit` only formats and puts the alert on a bounded in-memory queue (drop the oldest or newest if full, never block). A single daemon worker thread does the HTTP POST with a short timeout (e.g. 5s connect, 10s read), catches every exception, and does at most a couple of retries with backoff.
- **No feedback loop.** The worker must never log at WARNING or above to loggers the handler is attached to when a POST fails (otherwise a dead webhook alerts about itself forever). Log webhook failures at DEBUG on a logger the handler ignores, or write a single rate-limited line to stderr. Also skip records emitted by the alerts module itself.
- **Dedupe and cooldown.** Identical alerts (same logger and same message text) within a cooldown window (default 30 minutes, `MC_ALERT_COOLDOWN_MINUTES`) are suppressed and counted. The next alert that gets through after the window includes `repeat_count` (how many occurred including suppressed ones). The update thread retries every 5 minutes, so this matters.
- Handler must be safe to construct when `requests` is available but config is empty; it must do nothing harmful in tests.

## Configuration (all optional, read via `mc/config.py` helpers)

- `MC_ALERT_WEBHOOK_URL`: webhook endpoint. **Unset by default.**
- `MC_ALERT_MIN_LEVEL`: default `CRITICAL`; accepts `ERROR`, `CRITICAL` (invalid falls back to default with one warning).
- `MC_ALERT_IN_GAME`: default `true`.
- `MC_ALERT_COOLDOWN_MINUTES`: default `30`.

In `.env.template`, add these as **commented-out** entries with short explanations. The webhook line must be commented out with an example like `# MC_ALERT_WEBHOOK_URL=https://example.com/your-webhook` to show the possibility without enabling it.

## Escalating sustained scraper failures

Individual API failures in `mc/downloads.py` are logged at ERROR and the Mojang endpoint is known to be flaky, so do not alert on every blip. In `mc/update.py` (`download_version_if_required` retry loop and `get_most_recent_update_thread`), count consecutive failures to obtain a download link or to download; after a threshold (default 6 consecutive failures, constant at module level, optionally `MC_ALERT_SCRAPE_FAILURE_THRESHOLD`) log one CRITICAL such as "Could not check for or download a server update after N consecutive attempts", then reset or re-alert only after the cooldown. A success resets the counter. Also make sure genuinely critical conditions already logged at CRITICAL still reach the handler unchanged (server process died, update failed, backup failures that are already logged critically). Do not otherwise rewrite existing log levels.

## Documentation

Add an "Admin alerts" section to `README.md`: what it is, what triggers it (CRITICAL logs and sustained update failures), the two sinks, that no webhook is configured by default (in-game message only), how to enable the webhook, an example of the JSON payload, how it can be pointed at Discord/Teams/Slack-style webhooks or a custom receiver, the cooldown behavior, and the env var table. Keep it concise.

## Testing and constraints

- Unit tests (`tests/test_alerts.py`, pytest): handler ignores records below the level; unset webhook means no HTTP calls and no errors; payload shape; truncation and newline stripping of the in-game message; cooldown/dedupe and `repeat_count`; the in-game sender raising is swallowed; webhook failure does not recurse or raise; invalid URL disables the sink; sustained-failure counter escalation. Mock `requests`; no real network calls. Avoid real sleeps (inject time or use a tiny cooldown).
- The system Python lacks `dotenv` and `requests`; if tests cannot import `mc` for that reason, use stub packages on `PYTHONPATH` in the scratchpad (outside the repo), do not install anything or add stubs to the repo. Run the whole suite, not just the new tests.
- Do not run the real server or touch real data dirs.
- Match existing style (`_log`, `str | None` hints, no new heavy dependencies; `requests` is already a dependency).
- Do NOT commit or push. Leave changes in the working tree.
- Other uncommitted work exists in the tree (startup commands: `mc/startup_commands.py`, `startup_commands.template.txt`, edits in `mc/server_runtime.py`, `run_mc_server.py`, `.env.template`, `README.md`, `.gitignore`). Build on top of it; do not revert or rewrite it, and keep your edits to shared files minimal and additive.
