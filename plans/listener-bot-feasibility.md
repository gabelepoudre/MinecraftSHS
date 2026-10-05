# Listener bot: can the Xbox broadcaster also log deaths and chat?

Status: feasibility analysis only (researched 2026-10-05). Nothing implemented.

## Summary and recommendation

**No, the broadcaster cannot simply also be the listener bot.** MCXboxBroadcast never joins the Bedrock server as a player. It hosts a tiny fake server over NetherNet, lets the joining friend's client log in to *it*, then sends a `TransferPacket` pointing at our BDS address. Its only contact with BDS is a RakNet unconnected ping for the MOTD and player count. It has no plugin mechanism in the Standalone build. Making it a listener would mean adding a whole client stack (auth chain, login, resource packs, start game, keep-alive and movement) inside a fork. You don't want a fork, and upstream would be unlikely to accept something so far outside the project's purpose (unverified, not asked).

**A separate listener bot is technically feasible, but no option "just works" without being tied to an external release cycle.** BDS accepts only clients on its exact protocol version, and our wrapper auto-updates BDS the day Mojang ships. So after every protocol bump (about 5 to 8 a year, counting the hotfixes that change protocol), the bot cannot connect until its protocol library ships support and we pick it up. The best library catches up in 0 to 1 days (gophertunnel). The easiest library to auto-update catches up in 2 to 7 days (bedrock-protocol). These are the same dependency-on-upstream properties that ruled out Endstone/LeviLamina, only milder: a broken bot loses events, while a broken loader loses the server.

**Recommendation:**
1. **Ship the Xbox broadcast and the phase-1 event log as planned. Accept "no death or chat tracking" for now.** This is the only option that fully meets the "just works, no external release coupling" priority.
2. If deaths become worth it, build an **optional, off-by-default bot using Node + bedrock-protocol**, installed at runtime with `npm install bedrock-protocol@^3` (or latest) so our releases are not tied to it. Run it as a supervised subprocess that prints `[MCSHS-EVENT] {...}` lines, like the planned add-on. Use a **second** alt account. Treat its outages after Mojang updates as expected and self-healing, and do not alert on them as CRITICAL.
3. Don't count on achievements from any of these routes. Bedrock does not announce achievements to other players, so a listener bot never sees them.

## Options

| Option | Feasible? | Effort | Breaks on Mojang protocol bump? | Release coupling | Notes |
|---|---|---|---|---|---|
| (a) Extension to MCXboxBroadcast | **No** | n/a | n/a | n/a | No plugin/extension loader in Standalone. The "extension" JAR is a *Geyser* extension, not an extension point of the broadcaster. |
| (b) Fork MCXboxBroadcast and add a client | Possible | High (Java; client login and auth flow on CloudburstMC Protocol + MinecraftAuth) | Yes, and both halves break at once | Worst: we would own a GPL fork and rebase onto weekly upstream builds | Rejected (user dislikes forks). One component, one account, but double the failure surface. |
| (c) Separate bot, **same** account as the broadcaster | Probably works (unverified) | Same as (d) | Yes | Same as (d) | Needs its own device-code login anyway (separate token cache). Puts all ban/TOS risk on one account. Players would see the friend account "in game". No real benefit. |
| (d1) Node bot, `bedrock-protocol` (PrismarineJS, MIT) | **Yes** | Low–medium (~150 lines JS) plus Node install and supervision in Python | Yes; bot is offline until a new library release | Low if installed at runtime from npm; 2–7 day gap per update | Proven by `mc-bedrock-chatlog` (offline mode). Auto-picks the server version via ping. Auth via prismarine-auth device code with a cached token. |
| (d2) Go bot, `gophertunnel` (MIT) | Yes | Medium; we must build and ship a binary | Yes; supports **only one** protocol version at a time | **High**: we must rebuild and release after every bump (0–1 day upstream lag + our lag) | Fastest upstream, but puts us on Mojang's schedule. |
| (d3) Java bot, CloudburstMC Protocol + MinecraftAuth | Yes | High; Protocol is a codec library with no ready-made client or auth | Yes | High (we build the JAR); Protocol has no tagged releases (snapshots) | Codecs land *before* Mojang releases (26.50 codec Aug 27, release Sep 15). Reuses the broadcaster's Java runtime. |
| (d4) Python bot | No maintained library | Very high | Yes | High | We would write a Bedrock client ourselves. Not realistic. |
| (e) Do nothing for deaths | Yes | None | No | None | Phase-1 log (joins/leaves/lifecycle) is unaffected. |

## Details

### 1. How MCXboxBroadcast works

- Java (Gradle), GPL-3.0. Modules: `core`, `bootstrap/standalone` (two classes: `StandaloneMain`, `StandaloneLoggerImpl`) and a Geyser extension bootstrap. Releases ship `MCXboxBroadcastStandalone.jar`, `MCXboxBroadcastExtension.jar` and a Pterodactyl egg JSON.
- Libraries: CloudburstMC Protocol (`org.cloudburstmc.protocol.bedrock`), CloudburstMC netty-transport-raknet, NetherNet/libdatachannel (WebRTC), MinecraftAuth (5.0.2 since build 155).
- Join flow (`core/.../nethernet/RedirectPacketHandler.java`): it handles `RequestNetworkSettings`, `Login`, `ClientCacheStatus` and `ResourcePackClientResponse`. It sends `NetworkSettings`, `PlayStatus`, `ResourcePacksInfo/Stack`, `StartGame` etc., then a **`TransferPacket` to the configured IP and port**. It is server-side only and **never connects to BDS as a client**.
- BDS contact (`core/.../ping/PingUtil.java`): RakNet **unconnected ping only**, used to sync the MOTD and player count, with an optional web-ping fallback.
- Cadence and breakage, from the releases list: builds 149–159 in about 2 months (Aug 4 – Oct 3, 2026). Protocol updates came the same day as Mojang (149 "Update to 26.40" on Aug 4, the day 26.40 released; 154 "Update to 26.50" on Sep 15, the day 26.50 released). The 1.26.45 bump was released (150, Aug 20), reverted (151, Aug 23) and re-applied (152, Aug 28). Other changes: friend-system rewrite (159), WebRTC library swap (155). So it is itself protocol-coupled, because it must speak the joining client's protocol, and it is actively maintained.
- Extension points: none found in Standalone (no ServiceLoader or plugins folder).

### 2. One account broadcasting and playing at once

- Joining BDS as a client and hosting the NetherNet session are separate token uses. MinecraftAuth/prismarine-auth each keep their own device token, so both should be able to sign in. Plain protocol-library clients don't create an Xbox multiplayer session or presence of their own, so they shouldn't overwrite the broadcaster's session. **Unverified**: no source found that confirms or rules this out. It needs a 10-minute spike.
- Recommendation regardless: use a **second** alt account for the bot. Nobody needs to friend it. It keeps a bot ban or lockout from also taking down friend-joining. It also keeps the friend-list account from showing up as a player in the server.
- **Unverified:** whether an account that does not own Bedrock can complete login to an online-mode BDS. Applies to both alt accounts.

### 3. Per-option operational facts (for d1, the realistic one)

- **Protocol updates.** bedrock-protocol releases against Mojang releases: 26.20 (May 5) → 3.56.0 May 9; 26.30 (Jun 16) → 3.57.0 Jun 18; 26.40 (Aug 4) → 3.58.0 Aug 8; 26.50/26.51 (Sep 15) → 3.60.0 Sep 22, plus fix 3.60.1 the same day. That is a gap of **2–7 days** each time. Hotfix protocol bumps (26.44, 26.45) got separate releases (3.58.2 Aug 15, 3.59.0 Sep 5). gophertunnel for comparison: v1.56.x May 5, v1.57.0 Jun 16, v1.58.0 Aug 5, v1.62.0 Sep 16, so 0–1 days. But it supports only the latest version, and we would have to rebuild.
- **What happens on a bump:** BDS rejects the bot as an outdated client. The supervisor should back off for hours rather than minutes. If we auto-install at runtime, it should run `npm update` daily and on each BDS update, then retry. Events in that window are lost, with no backfill possible.
- **Auth:** device-code login once, through a `--setup-listener` command just like `--setup-xbox`. Tokens are cached in a folder (prismarine-auth `profilesFolder`) and refreshed automatically. The Microsoft refresh token is long-lived and sliding, so it needs re-setup only if it is unused for a long time or revoked (based on general MSA behaviour; not verified for this library).
- **Death messages:** BDS sends them to every client as a `TextPacket` of type `translation`, key `death.*` (e.g. `death.attack.mob`, `death.fell.accident.generic`), with parameters (victim, and killer name or entity key). mc-bedrock-chatlog maps ~25 such keys. This requires gamerule `showDeathMessages` true (the default). The exact killer parameter format (e.g. `%entity.zombie.name`) is **unverified**, so capture real packets first.
- **Chat:** `TextPacket` type `chat` with `source_name` and `message`. Whispers (`/tell`) to other players and most command output are not visible. Joins/leaves arrive as `%multiplayer.player.joined/left`, which duplicates the console. Logging chat is a privacy decision, so tell the players.
- **Achievements:** Bedrock never broadcasts other players' achievement unlocks (no `announceAdvancements` equivalent), so a listener **cannot** see them. The phase-1 console achievement regex is also unverified and is likely never to match.
- **Slot and visibility:** the bot takes a `max-players` slot and appears in the player list and in the console `Player connected` lines (filter by its xuid in `events.py`). It needs spectator mode, which the wrapper can apply itself via console stdin (`gamemode spectator <bot>`), so no op is needed. It loads and ticks chunks where it stands, so park it somewhere harmless. It may count for or against sleep and other "players online" logic, and the planned "skip backup / idle restart when nobody is online" ideas must exclude it. **Unverified:** whether `player-idle-timeout` kicks a client that sends no input.
- **Feeding the event log:** the bot prints one line per event, `[MCSHS-EVENT] {"type":"player_death",...}`, to stdout. Python supervises it like the broadcaster (backoff, logger) and passes the lines through the same parser planned for the add-on, then `events.emit(..., source="listener")`. No new Python dependencies, but it adds **Node.js** as a user-installed prerequisite (Java is already needed for the broadcaster).

### 4. Candid assessment against "just works, not tied to an external package"

- Every listener option depends on a third-party Bedrock protocol implementation, and every one goes blind after each Mojang protocol bump until that implementation updates. That cannot be avoided: BDS refuses mismatched clients.
- Runtime install of bedrock-protocol from npm removes the coupling to **our** release schedule, but not to theirs. It also adds supply-chain trust in an auto-updated npm package (and its auth dependency), plus a Node runtime.
- Counter-arguments to building it at all:
  - It is a player-shaped component that changes gameplay (player list, slot, chunk loading, sleep and idle logic) to get a log.
  - Account and TOS risk on another alt.
  - Chat logging has privacy implications.
  - Its data has gaps after each update.
  - For a small friends' server, death stats are nice to have, and the broadcaster plus the phase-1 log already deliver the core value.
- Things that might change this later: Mojang moving chat or death events to stable Script API in a way that doesn't flag achievements (not the case today), or BDS adding console output for deaths. Neither is announced.

## Sources

- MCXboxBroadcast repo and README: https://github.com/MCXboxBroadcast/Broadcaster
- Releases (builds 149–159, dates and notes): https://api.github.com/repos/MCXboxBroadcast/Broadcaster/releases
- RedirectPacketHandler (TransferPacket, server-side only): https://github.com/MCXboxBroadcast/Broadcaster/blob/master/core/src/main/java/com/rtm516/mcxboxbroadcast/core/nethernet/RedirectPacketHandler.java
- PingUtil (unconnected RakNet ping): https://github.com/MCXboxBroadcast/Broadcaster/blob/master/core/src/main/java/com/rtm516/mcxboxbroadcast/core/ping/PingUtil.java
- bedrock-protocol (MIT; supports 1.16.201–1.26.51): https://github.com/PrismarineJS/bedrock-protocol, history: https://github.com/PrismarineJS/bedrock-protocol/blob/master/HISTORY.md, releases: https://github.com/PrismarineJS/bedrock-protocol/releases
- gophertunnel (one version at a time) and tags: https://github.com/Sandertv/gophertunnel, https://github.com/Sandertv/gophertunnel/tags
- CloudburstMC Protocol commits (26.50 codec Aug 27, 2026): https://github.com/CloudburstMC/Protocol/commits/3.0
- mc-bedrock-chatlog (death `death.*` translation keys, joins via `%multiplayer.player.joined`, offline mode, manual spectator): https://github.com/niker/mc-bedrock-chatlog
- Mojang release dates: https://minecraft.wiki/w/Bedrock_Edition_26.20, https://minecraft.wiki/w/Bedrock_Edition_26.30, https://minecraft.wiki/w/Bedrock_Edition_26.40, https://minecraft.wiki/w/Bedrock_Edition_26.50
- TextPacket structure: https://minecraft.wiki/w/Bedrock_Edition_protocol/Packets, https://mojang.github.io/bedrock-protocol-docs/latest/
- Bedrock does not announce achievements in chat: https://feedback.minecraft.net/hc/en-us/community/posts/360071970811-Show-Achievements-in-chat-Minecraft-Bedrock-Parity

Unverified items to spike before any build: same-account simultaneous broadcast and play; non-owning account login to BDS; exact death-message parameters; idle-timeout behaviour for a passive client; MSA refresh-token lifetime with prismarine-auth.
