# Optional Xbox Live broadcast (MCXboxBroadcast) managed by the startup script

Status: plan only, not started.

Goal: make the server show up in the Minecraft "Friends" tab on Xbox, PlayStation, Switch and mobile, so players join with one click instead of DNS tricks or a bridge app. Optional, off by default. When enabled, `run_mc_server.py` handles everything: download, update, configure, run and restart the broadcaster, with no separate setup or management.

## Background (researched 2026-10-01)

- Tool: MCXboxBroadcast "Standalone" (https://github.com/MCXboxBroadcast/Broadcaster), GPL-3.0. It signs in to a Microsoft/Xbox account and publishes a joinable Xbox Live (NetherNet) session, then transfers players who join to a configured Bedrock server address. Players add that account as a friend and see the server in Friends.
- It is a Java program: `java -jar MCXboxBroadcastStandalone.jar`. First run prints a device-code login ("open https://www.microsoft.com/link and enter the code ..."). Config is `config.yml` (target server address and port); auth tokens and session cache are stored next to `config.yml`. Restart required after config changes.
- Releases are numbered builds (e.g. "Build 157", Sep 2026), roughly weekly to bi-weekly, with 5 assets per release (file names not yet verified).
- Project disclaimer: it emulates client features that may be against TOS, so use a dedicated alt account, never a personal one. Whether the alt account must own Minecraft is NOT documented; likely not, but untested.
- Not verified yet: required Java version, exact asset names, full `config.yml` schema, how it behaves across server restarts, console/PlayStation/Switch visibility, account limits.

## Decision: download releases, do not use a submodule or fork

- It ships as a prebuilt JAR in GitHub Releases. A submodule or fork means building from source (JDK, Gradle) on the user's machine, which is heavy and fragile and has nothing to do with this project's purpose.
- Pulling releases matches how this project already handles Mojang's server (check upstream, download, validate, swap) and gives automatic updates for free.
- Releases are numbered builds, so "newer" is a plain integer comparison. Keep the last few JARs so a bad build can be rolled back.
- GPL-3.0: running the downloaded JAR as a separate process is fine. Do not copy its source into this repo and do not commit the JAR. Mention the license and link in the README.
- A fork would only make sense if we need to patch it. Revisit only if upstream breaks or is abandoned; the downloader should take a configurable repo and asset name so pointing at a fork is a config change.

## Step 0: Verification spike (do first, scratch setup only)

1. Fetch the latest release via the GitHub API (`https://api.github.com/repos/MCXboxBroadcast/Broadcaster/releases/latest`) and record asset names, the build/tag format, and checksums if provided.
2. Determine the required Java version from release notes, README or the JAR manifest (`Class-Version`), and check how a user would install it (e.g. Temurin) on Windows.
3. Run it by hand against a scratch server with a throwaway Microsoft account: confirm the login flow, the config schema, that the friend can see and join the session, that joining works across a server restart, and whether the alt account needs Minecraft ownership. Test an Xbox/mobile client at minimum; PlayStation/Switch if available.
4. Note anything that makes unattended operation hard (token expiry, forced re-login, rate limits).

Write findings at the top of this file before building.

## Step 1: Downloader and updater (`mc/xbox_broadcast.py`)

Mirror the structure of `mc/downloads.py` and `mc/update.py` and reuse `mc/versions.py`-style ordering logic (integer build numbers):

- Directory: `<data dir>/xbox_broadcast/` with `releases/<build>/MCXboxBroadcastStandalone.jar` and a `work/` directory that holds `config.yml`, auth tokens and cache (must persist across updates; never inside a release folder). Overridable via `MC_XBOX_BROADCAST_DIR`.
- Check for a newer release on a slow schedule (e.g. once a day with jitter), using the GitHub API with a `User-Agent`, timeout, broad `RequestException` handling, and tolerance for rate limiting (unauthenticated limit is 60/hour; fine). Never downgrade. Keep the newest N builds (default 3), delete older ones.
- Download validation as for the Mojang server: stream to `.partial`, rename on success, check it is a valid zip/JAR, check `Content-Length`, and verify a published checksum if one exists. Never replace the working JAR until the new one is fully verified.
- Make repo and asset name configurable (`MC_XBOX_BROADCAST_REPO`, default `MCXboxBroadcast/Broadcaster`; asset name pattern default from step 0).
- Apply an update by restarting the broadcaster process (step 2), not by restarting Minecraft.

## Step 2: Process management

- Check Java on startup when enabled (`java -version` via `shutil.which("java")`). If missing or too old, log a clear ERROR with install instructions and disable the feature for this run; never crash the Minecraft server.
- Generate/refresh `work/config.yml` from our settings: target address `127.0.0.1` and the server's port (read from the active `server.properties`, key `server-port`, default 19132), plus a configurable session name/message. Only rewrite keys we own; preserve any manual edits. Exact schema comes from step 0.
- Run the JAR as a subprocess (`java -jar ... `, cwd = `work/`), capture stdout/stderr into the app's logging on a dedicated logger (e.g. `xbox`), and keep it running across Minecraft restarts (updates, daily restart, crashes), unless step 0 shows it must be restarted with the server.
- Supervision: restart on exit with exponential backoff (cap e.g. 10 minutes), log CRITICAL after N consecutive failures (this reaches admin alerts if enabled). Stop it cleanly on shutdown (terminate, then kill after a timeout) so no orphaned `java` process is left on Ctrl+C or `stop`.
- **No login during normal runs.** The normal startup path never starts a login flow and never waits on one. If the feature is enabled but setup has not been completed (no saved auth tokens in `work/`), log one clear ERROR such as "Xbox broadcast is enabled but not set up. Run `python run_mc_server.py --setup-xbox` once" and skip starting the broadcaster; Minecraft itself starts normally. Setup does not depend on webhooks or any alert system. If saved tokens later stop working (should be rare, tokens refresh on their own), log a CRITICAL telling the user to re-run the setup command; this reaches admin alerts only if they happen to be enabled, but nothing depends on that.
- Wire into `run_mc_server.py::main()`: start after the Minecraft server starts; add the stop to the shutdown paths. Keep changes small and additive.

## Step 2b: Guided one-time setup (`--setup-xbox`)

A single, explicit, interactive command run once by the person setting this up, and not needed again afterwards: `python run_mc_server.py --setup-xbox` (handled early in `main()` before any server start; exits when done). It must work for someone who has never seen the tool. Steps, each printing a clear heading and plain-language explanation:

1. Explain what is about to happen, that a dedicated alt Microsoft account is strongly recommended (never a personal one, ban risk), and ask for confirmation to continue.
2. Check for Java; if missing or too old, print exactly what to install and where to get it, then stop (re-run after installing).
3. Download and validate the latest broadcaster build (the step 1 downloader), reporting progress.
4. Generate `work/config.yml` from `server.properties` (server port) and the configured session name.
5. Run the JAR in the foreground, relaying its output. When the device-code login appears, show a prominent boxed block with the URL (`https://www.microsoft.com/link`) and the code, and say to sign in with the alt account. Detect success (tokens saved in `work/` and/or a success line in the output; exact signal from step 0) and report it. Handle timeout/denial with a clear message and an option to retry.
6. Print the remaining manual steps: add the alt account as a friend from each player's account, set `MC_XBOX_BROADCAST=true` in `.env`, and start the server normally. Offer to write the env var into `.env` if the file exists (ask first; never overwrite other lines).
7. Exit. Setup is idempotent: re-running detects existing tokens and offers to keep them or sign in again (this is also the recovery path if login ever needs redoing).

## Step 3: Configuration (`mc/config.py`, `.env.template`)

All optional, commented out in `.env.template`:

- `MC_XBOX_BROADCAST` default `false`. Nothing is downloaded, checked or started unless true.
- `MC_XBOX_BROADCAST_DIR`, `MC_XBOX_BROADCAST_REPO` (see above).
- `MC_XBOX_BROADCAST_KEEP_BUILDS` default 3.
- `MC_XBOX_BROADCAST_SESSION_NAME` (display name/description in Friends, if the tool supports it).
- `MC_JAVA_PATH` optional override for the `java` executable.

## Step 4: Documentation and tests

- `README.md` section "Xbox Live broadcast (console crossplay discovery)": what it does, why an alt account, Java requirement, the TOS/ban disclaimer, the unverified points (Minecraft ownership, PlayStation/Switch visibility), and the GPL-3.0 note with a link. **Include clear step-by-step setup instructions for the guided path**: 1) create a free alt Microsoft account (and what to do about the gamertag), 2) install Java, 3) run `python run_mc_server.py --setup-xbox` and follow the prompts, with an example of what the screen looks like and what the sign-in page asks, 4) confirm `MC_XBOX_BROADCAST=true` in `.env`, 5) start the server, 6) how each player adds the alt account as a friend and finds the server under Friends on each platform, 7) what to do if it stops working (re-run setup), plus troubleshooting for the common failures (no Java, login code expired, friend can't see the server). Setup is described as a one-time task.
- Unit tests (pytest; no network, no real Java): release JSON parsing and build ordering (never downgrade), asset selection, download validation with fake JARs in temp dirs, retention of N builds, config generation preserving user edits, `server-port` parsing, supervisor backoff and CRITICAL escalation with an injected clock and a fake process, device-code detection from sample output, and "disabled means nothing happens". Also test the setup flow with fakes: missing Java stops with instructions, device-code extraction and success detection from sample output, idempotent re-run with existing tokens, the not-set-up normal-start path logging the ERROR and not starting the process, and `.env` writing that preserves other lines.
- Match existing style; no new third-party dependencies.

## Out of scope / notes

- Not a Realm and cannot become one; this only provides friend-list discovery via an Xbox Live session.
- Does not manage the alt account or Java installation for the user.
- The whole feature depends on an unofficial tool that could break when Microsoft changes things; the supervisor, clear logging and the "disabled by default" design are the mitigation.
- Do not commit or start implementation until asked. Do all spike work on a scratch server.
