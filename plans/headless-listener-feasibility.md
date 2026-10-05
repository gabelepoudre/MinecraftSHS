# Headless listener: feasibility study

Status: feasibility study done on 2026-10-05, nothing implemented in the repo. Answers the open questions in
`headless-listener.md`. Experiments ran against a throwaway BDS **1.26.52.3** (protocol 2193, the same as 1.26.51) in a
scratch directory. The server was bound to loopback, `enable-lan-visibility=false`, on a new world, and has since been
deleted. Evidence is tagged **[verified]** (experiment here), **[sources]** (docs or other people's reports) or
**[unverified]**.

## Verdict and recommendation

**The protocol side works. The "no Microsoft account" side does not, so don't build it as specified.**

- **NetherNet does not kill the idea.** RakNet still works in 1.26.52 behind `transport=raknet`, but it is deprecated
  and reportedly removed in 26.60, so the client has to speak NetherNet. Mojang documents NetherNet's HTTP signalling,
  and a loopback client is simple: one HTTP POST, then WebRTC data channels. I wrote a **~220-line Python prototype**
  (aiortc + cryptography) that joins a NetherNet BDS with no account and prints `[MCSHS-EVENT]` lines for chat, joins
  and leaves, and deaths with killer names. [verified]
- **The account-free premise is false.** `online-mode` has no server-side LAN exemption:
  - With `online-mode=true`, BDS rejects a self-signed login even from 127.0.0.1 (`not_authenticated`), on both
    transports. [verified]
  - The "remote clients always require Xbox Live" sentence in the docs describes the vanilla *client*, not something
    the server enforces.
  - With `online-mode=false`, BDS accepts any self-signed identity, with any name and an empty xuid. [verified]
  - BDS **refuses to start** with `allow-list=true` and `online-mode=false`: "Using an allowlist without online
    authentication can be dangerous and is not allowed." [verified]
  - So an internet-reachable server in offline mode lets anyone with a third-party client (bedrock-protocol is on npm)
    join under any name, with no allowlist possible. Not acceptable for this server.

**Recommendation:**

1. **Don't ship an offline-mode listener.** Keep `online-mode=true`.
2. If deaths and chat become worth it, the realistic route is the minimal client **plus real Xbox authentication with
   a bot account**. That means a device-code login, the XBL/XSTS tokens, and the Minecraft multiplayer token, used in
   both the NetherNet `a=identity` and the Login packet. Auth is the largest and most change-prone part, and it brings
   back the second account that this idea set out to avoid. At that point it beats the "Node + bedrock-protocol"
   option in `listener-bot-feasibility.md` only on footprint, not on robustness.
3. The Node route got better since the earlier study: **bedrock-protocol 3.60.1 already supports NetherNet**,
   including BDS HTTP signalling ([verified]: offline over loopback). So if a listener is ever built, building it on
   bedrock-protocol with a bot account is still the lower-effort path. A hand-written Python client is viable, but its
   version tracking becomes our job.

**Follow-up test (2026-10-05): a free Microsoft account cannot get a Minecraft multiplayer token.** With
bedrock-protocol 3.60.1 against a throwaway online-mode BDS 1.26.52.3, an old alt account that does not own Minecraft
completed the Microsoft device-code sign-in, then got `401 Unauthorized` from
`/multiplayer/bedrock/authentication` ("Ensure that you are able to sign-in to Minecraft with this account"). The client
never reached the server. Most likely cause: the account does not own Bedrock (an Xbox profile issue is less likely,
since the Xbox token step succeeded). So a listener bot needs an account that owns Minecraft. The broadcaster never
calls this endpoint, so this result does not apply to it.

**Follow-up tests (2026-10-05): no auth bypass at startup or without internet.** Throwaway BDS 1.26.52.3, RakNet,
loopback, `allow-list=false` (note: this version's default `server.properties` ships `allow-list=true`, and BDS will not
start with that plus `online-mode=false`).

- Control: with `online-mode=false`, unsigned bedrock-protocol logins join immediately.
- Startup race: with `online-mode=true`, 74 unsigned logins fired every 300 ms from before launch to 35 s after. 71 were
  kicked with `not_authenticated` and 3 timed out before the port opened. A login already in flight when the port opened
  was rejected 180 ms after "Server started". No window.
- No internet: online-mode BDS opens HTTPS connections to Microsoft (150.171.x.x:443) right after start. With outbound
  traffic blocked by a Windows firewall rule for the exe, it logs "Waiting for Minecraft services...", answers no logins
  at all (clients time out), then after about 67 s logs "Could not connect to Minecraft services. This is required to
  accept connections in online mode." and stops itself. It fails closed.
- **Operational note for this repo:** if the live machine has no internet when the server starts, BDS shuts itself
  down after about a minute. `maintain_loop` will treat that as a crash and restart it repeatedly until the connection
  returns, which shows up as repeated `server_crash` events and CRITICAL alerts.

## Answers to the open questions

### 1. NetherNet

- **Is RakNet still accepted?** Yes, in 1.26.52.3 with `transport=raknet` (allowed values `raknet` and `nethernet`;
  the shipped default is `nethernet`). A RakNet client connected, logged in and received TextPackets. [verified] At
  start-up the console prints:
  ```
  ERROR] ================ TRANSPORT TYPE ERROR  ===================
  ERROR] Your current connection type is not set to NetherNet. In this release, NetherNet is the only supported transport type.
  ERROR] Players will not be able to connect to your game without NetherNet.
  ERROR] To switch, set 'transport=nethernet' in server.properties.
  ```
  So vanilla clients presumably need NetherNet now: the live server has to run NetherNet, and a listener has to use it
  too, since a server has only one transport. [verified: message; unverified: real-client behaviour]
- **Is RakNet going away?** The 26.60.22 preview changelog says "RakNet is deprecated" and turns the warning into an
  error. Third-party reports say 26.60 removes RakNet entirely. Another report says preview 1.26.60.29 still allowed
  `raknet` on Oct 4. [sources] Plan for NetherNet only.
- **Is NetherNet documented?** Yes. Mojang's "NetherNet HTTP Signaling — Partner Onboarding Guide"
  (bedrock-protocol-docs) covers `GET /v1/join` (server info), `POST /v1/join/{networkId}` (SDP offer in, answer
  out), host-only ICE with no STUN or TURN, DTLS, SCTP, the two data channels `ReliableDataChannel` and
  `UnreliableDataChannel`, a 1-byte "remaining segments" header per message, and the `a=identity` assertion.
  [sources]
- **What it looks like on BDS** [verified, netstat and console]:
  - With `transport=nethernet` and `server-ip=127.0.0.1`, BDS logs `Accepting clients on 127.0.0.1:19242` and listens
    on TCP 127.0.0.1:19242 only.
  - It still opens UDP 7551 on 0.0.0.0, even with LAN visibility off.
  - It binds gameplay UDP sockets on every interface address (LAN, Tailscale, Hyper-V) on the server port.
  - It signs in to a Microsoft signalling service over outbound 443 in both transport modes ("Signed in to signaling
    service successfully").
  - `server-ip` exists only for NetherNet (documented in `bedrock_server_how_to.html`).
- **What a client must do (implementable)** [verified, Python prototype]:
  - Create a WebRTC peer connection with the two data channels.
  - Add a session-level `a=identity` line to the offer, then POST it.
  - Set the answer and send Bedrock batches on the reliable channel.
  - An offer **without** `a=identity` is rejected: the body is `37` (identity not allowed). A **self-signed** identity
    is accepted in offline mode. The identity is a base64 JSON with an ES384 JWT (`iss: "self"`, `cpk` = client key)
    plus a detached ES384 JWS over the offer's DTLS fingerprints.
  - The server's answer carries its own `a=identity`. The prototype ignores it, which is fine on loopback.
- **Surprise that makes it smaller:** over NetherNet, BDS still sends `ServerToClientHandshake`, but the stream stays
  **plaintext** after it. DTLS already encrypts, and bedrock-protocol sets `disableEncryption` for NetherNet. So the
  AES/ECDH layer is not needed. [verified: enabling AES made the server go silent; without it, login completed]

### 2. Auth and online-mode

- **Do loopback or LAN clients skip Xbox auth?** Only when `online-mode=false`, and then everyone does.
  `online-mode=true` kicked a self-signed client on 127.0.0.1 (`disconnect reason=not_authenticated`) over RakNet and
  over NetherNet. On NetherNet, signalling succeeded and the Login was rejected. [verified] The rejection prints
  nothing to the console. [verified]
- **Do remote clients still authenticate with online-mode=false?** No sign that the server checks. bedrock-protocol's
  offline mode and similar bots are used against remote offline servers. A real remote join was not tested, because the
  test server stayed loopback-only. [unverified by experiment; the prudent assumption is "no"]
- **Do remote players still get xuids?** Unverified (needs a real account). Sources say xuids need online mode.
  [sources]
- **Can a client impersonate a name?** Yes: names are free-form in offline mode. Bots joined as any name, and the
  console shows `Player connected: <name>, xuid: ` with an empty xuid. [verified] **The allowlist cannot mitigate
  it**, because BDS will not start with an allowlist in offline mode: it logs the error above and hangs before opening
  any socket. [verified] `permissions.json` is keyed by xuid, so an impersonator probably wouldn't inherit operator
  status. [unverified]
- **Is a self-signed chain rejected with online-mode=true?** Yes, with `not_authenticated`. [verified]
- Rejected mitigation: "kick any empty-xuid connection that isn't the listener" from the console. It fails open, leaves
  a window before the kick, can't tell the real listener from an impostor using its name (the console shows no IP),
  and depends on real players still getting xuids in offline mode (unverified).

### 3. Minimum handshake

- **Packets sent by the working Python client** [verified]:
  - `RequestNetworkSettings`
  - `Login` (offline Certificate/Token form)
  - `ClientToServerHandshake`
  - `ResourcePackClientResponse(completed)` after `ResourcePacksInfo`, and again after `ResourcePackStack`
  - `ClientCacheStatus(false)`
  - `RequestChunkRadius(2)`
  - Echoes of `NetworkStackLatency` when the server asks for one

  It did **not** send `SetLocalPlayerAsInitialized`, `ServerboundLoadingScreen` or any movement. It still got
  `PlayStatus player_spawn` and every broadcast TextPacket. Whether `ClientCacheStatus` and `RequestChunkRadius` are
  strictly needed: [unverified]
- **Login quirk:** `DeviceOS: 7` in the client-data JWT got "Connection Request invalid. readNoHeader failed! packetId:
  1". `1` and `12` work. A tiny transparent 64×64 skin is accepted. [verified]
- **Player list and slot:** yes. The client is in `list` ("There are 2/10 players online: Chatty, PyListener") and
  takes a slot. The console prints `Player connected`, `Player Spawned` and `Player disconnected` lines with an empty
  xuid, which `events.py` would log as joins and leaves unless filtered. [verified]
- **Spectator:** `gamemode spectator <bot>` from the console works and TextPackets keep arriving. It doesn't hide the
  bot from `list`. [verified]
- **Invulnerability:** a client that never sends `ServerboundLoadingScreen` (end) is invulnerable. `/damage` gives
  "Could not apply damage to X" and mobs don't kill it. After sending it, damage applies. `/kill` always works, and a
  dead, un-respawned listener still receives TextPackets. [verified]
- **Idle timeout:** with `player-idle-timeout=1`, passive RakNet clients that never sent input stayed connected for
  over 3 minutes. [verified on RakNet; NetherNet unverified]

### 4. Stability across versions

- **Protocol must match exactly.** 2169 got `outdated_client`, 2194 and 2300 got `outdated_server`, 2193 succeeded.
  [verified] The asymmetric kick reason makes it possible to **binary-search** the server's protocol in about 10 quick
  connection attempts.
- **Ping as the version source:** with LAN visibility off, the RakNet pong had **no server-info string** (33 bytes) and
  `GET /v1/join` returned 200 with an **empty body**. [verified] Reports and Mojang's docs show `/v1/join` returning
  `{"name","protocol","version",...}`, probably when visibility is on. [sources; unverified here] Use the ping when it
  answers and the binary search otherwise.
- **How often our subset changed** (minecraft-data, 38 protocol versions from 1.19.30 to 1.26.51, about 3 years):
  - Changes we don't need to care about: `resource_packs_info` changed often, but we don't parse it. `disconnect`
    changed twice (log-only for us).
  - Changes that would break a minimal client:
    - `request_chunk_radius` (1.19.80)
    - `TextPacket` (1.21.0, and 1.21.130 where the `category` byte was added; minecraft-data also remodelled it at
      1.26.0)
    - the Login JWT format (1.21.90 `Certificate`/`AuthenticationType`/`Token`; offline OIDC-style token from
      protocol 944)
    - `ServerboundLoadingScreen` (new in 1.21.20)
    - `resource_pack_client_response` (1.26.40)
    - the transport itself (NetherNet default in 1.26.50)
  - So roughly **2–3 breaking changes a year** in the part we touch, compared with about 6–8 protocol bumps a year.
  - Echoing the version fixes the number but not layout changes, and those fail silently (kick or no events). Mojang's
    per-release protocol docs make the diffs easy to spot. [verified from minecraft-data; sources]

### 5. Implementation language and size

- **Python prototype** (scratch only, now deleted): **~220 non-comment lines**. It covers HTTP signalling, the
  identity, ES384 JWTs, the login, deflate batches and segmenting, and TextPacket decoding. Dependencies are
  **aiortc + cryptography**. aiortc pulls in PyAV, about 90 MB in a venv and mostly unused for data channels; it
  installed fine on Python 3.14. [verified]
  - There is no light Python WebRTC alternative. A from-scratch ICE, DTLS 1.2 and SCTP stack is not sensible.
  - RakNet would be stdlib-only (plus about 300 lines of RakNet and AES-GCM via `cryptography`), but RakNet is being
    removed.
- **Adding real Xbox auth** (needed per §2): device-code OAuth, XBL user token, XSTS, and the Minecraft multiplayer
  token. bedrock-protocol needs `prismarine-auth` plus `oidc.js` (159 lines) for this. Estimate +200–300 Python lines,
  a token cache and a setup command. These endpoints change outside Mojang's protocol docs. [sources, estimate]
- **Node alternative:** `bedrock-protocol` 3.60.1 with `nethernet` and werift (pure JS). NetherNet support, offline
  HTTP signalling via `tools/vanillaClient.js`, and Xbox auth come built in. [verified offline]

### 6. What it sees

Every client gets these as TextPackets. The examples are exact, from our probes. [verified]

| Event | type | message | parameters |
|---|---|---|---|
| Join / leave | translation | `§e%multiplayer.player.joined` / `§e%multiplayer.player.left` | `[name]` |
| Player chat | chat (authored) | `hello from Victim` | `source_name: "Victim§r"` (note the trailing `§r`) |
| `/say` | announcement | `[Server] console say test` | `source_name: "Server"` |
| `/me` (console) | chat | `* Server waves` | `source_name: ""` |
| `/tellraw @a` | json_whisper | `{"rawtext":[{"text":"tellraw test"}]}\n` | |
| `/kill` | translation | `death.attack.generic` | `[victim]` |
| Zombie / skeleton | translation | `death.attack.mob` | `[victim, "%entity.zombie.name"]` / `"%entity.skeleton.name"` |
| Named mob | translation | `death.attack.mob` | `[victim, "Bob the Zombie"]` |
| By a player | translation | `death.attack.player` | `[victim, killer]` |
| Fall, lava, drown, void, fire, explosion, starve | translation | `death.attack.fall` / `.lava` / `.drown` / `.outOfWorld` / `.inFire` / `.explosion` / `.starve` | `[victim]` |

- A projectile kill by a skeleton through `/damage ... projectile entity` still reported `death.attack.mob`. A real
  skeleton arrow may differ. [unverified]
- A naturally spawned zombie killed a bot, and that was reported the same way.
- `/tell` to another player and `xuid` fields were empty in offline mode.
- **The console prints none of this.** No deaths and no chat appear on stdout. The only death-related console line was
  command output (`Killed Victim`). [verified]
- **Achievements:** none can be triggered with bots, and none appeared in any console output or TextPacket during
  these runs. Whether achievement announcements reach other players or the console is **unverified**. The earlier
  study's sources say Bedrock doesn't broadcast them. Capture this on the live server the next time someone earns
  one.

## Implementation sketch, if ever built (with a bot account)

1. Supervised subprocess, like the planned Xbox broadcaster: `python -m mc.listener` (or Node with bedrock-protocol).
2. Discover the protocol from `GET http://127.0.0.1:<port>/v1/join`, falling back to binary search on
   `outdated_client`/`outdated_server`.
3. Use the auth token cache, then do NetherNet signalling with a real `a=identity` (multiplayer token), log in, and
   answer the resource packs.
4. Skip `ServerboundLoadingScreen` (stays invulnerable) and `SetLocalPlayerAsInitialized`. The wrapper puts the bot in
   spectator mode through stdin.
5. Decode `TextPacket` only. Map `death.*` to `player_death {victim, cause, killer}`, and chat to `chat {player,
   message}` (strip `§r`).
6. Print `[MCSHS-EVENT] {...}`, which the app passes to `events.emit(..., source="listener")`. Filter the bot's own
   join and leave lines in `events.py` by name.
7. Back off for hours on `outdated_*` or a parse failure, and log at WARNING, not CRITICAL.

Size: about 220 lines (protocol) + 200–300 lines (auth) + supervision. Dependencies: aiortc (+PyAV) and cryptography.

## Risks

- **Security (blocking):** an offline-mode server is open to name-spoofing clients from anywhere, and the allowlist
  can't be combined with it.
- **Transport churn:** NetherNet is weeks old in BDS. iOS clients currently fail its TLS handshake (BDS-23108), and
  the signalling or identity rules may change. There is UDP 7551 and multi-interface binding even when configured for
  loopback.
- **Version coupling:** exact protocol match on every bump, plus 2–3 layout breaks a year in the touched subset, found
  only by events going quiet.
- **Gameplay side effects:** it takes a slot, shows in `list` and the console, and counts as "someone online" for
  idle, backup and sleep logic.
- **Privacy:** chat logging needs to be disclosed to players.
- **Dependency weight:** aiortc and PyAV (~90 MB) for a log feature.

## Sources

- Mojang, NetherNet HTTP Signaling — Partner Onboarding Guide: https://mojang.github.io/bedrock-protocol-docs/guides/nether-net-onboarding-guide/
- Mojang protocol docs: https://mojang.github.io/bedrock-protocol-docs/
- `bedrock_server_how_to.html` and `server.properties` comments shipped in BDS 1.26.52.3 (`transport`, `server-ip`,
  `server-udp-ports`, the `online-mode` "remote (non-LAN)" sentence)
- Bedrock Edition Preview 26.60.22 ("RakNet is deprecated"): https://minecraft.wiki/w/Bedrock_Edition_Preview_26.60.22
- NetherNet overview: https://minecraft.wiki/w/NetherNet
- itzg/docker-minecraft-bedrock-server #673 (NetherNet default in 1.26.51.1, BDS-23108): https://github.com/itzg/docker-minecraft-bedrock-server/issues/673
- minepanel #324 (RakNet still allowed in preview 1.26.60.29 on Oct 4) and #321 (expected removal in 1.26.60): https://github.com/Ketbome/minepanel/issues/324, https://github.com/Ketbome/minepanel/pull/321
- playit.gg thread (TCP signalling on server-port, `/v1/join` JSON, UDP 7551): https://discuss.playit.gg/t/minecraft-bedrock-tunnel-after-1-26-51-update-nethernet-transport/5814
- PrismarineJS bedrock-protocol 3.60.1 (NetherNet, `tools/vanillaClient.js`, `disableEncryption` for NetherNet) and node-nethernet 1.1.2 (identity format, 10 000-byte segments): https://github.com/PrismarineJS/bedrock-protocol, https://github.com/PrismarineJS/node-nethernet
- minecraft-data bedrock protocol definitions 1.19.30–1.26.51 (packet layout history)
- Online-mode and xuid notes: https://minecraft.fandom.com/wiki/Server.properties
