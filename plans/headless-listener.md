# Headless listener: a minimal, local-only protocol client for deaths and chat

Status: idea, feasibility study pending. Nothing implemented. Follows on from `listener-bot-feasibility.md`, which
assumed a full protocol library (and its release lag) plus a Microsoft bot account.

## The idea

BDS prints no deaths or chat to stdout, add-ons lock achievements, and loaders (Endstone) lag every release. But every
connected client receives death messages and chat as `TextPacket`s. Instead of a full bot built on a big protocol
library, write a deliberately tiny client that:

- connects only from the same machine (loopback), never over the internet;
- does just enough of the handshake to be sent text packets: transport, `RequestNetworkSettings`, `Login`, the
  encryption handshake, resource pack responses, and whatever the server needs before it sends chat;
- decodes only `TextPacket` (chat, and translation messages with `death.*` keys and their parameters) and ignores every
  other packet without parsing it;
- prints `[MCSHS-EVENT] {...}` lines that the app feeds through `events.emit`, like the console parser.

Why it could "just work" where the libraries lag: they model every packet, and most packets change each release. This
client depends only on the handshake and `TextPacket`. The server's ping reply announces its exact protocol version, so
the client can echo it instead of hardcoding one; most Mojang updates should then need no change at all. Mojang
publishes official protocol docs per release (github.com/Mojang/bedrock-protocol-docs), which makes the rare real
changes easy to spot.

No bot account: Microsoft's server-properties docs say `online-mode` only exempts LAN clients from Xbox Live
authentication; remote clients always authenticate. If that holds, an unauthenticated loopback client can join while
internet players still authenticate. Must be verified carefully, since getting it wrong opens the server.

## Open questions (for the feasibility study)

1. **NetherNet.** Reports from mid-September 2026 (BDS 1.26.51.x) say NetherNet is now the default transport. Does BDS
   still accept RakNet? Is there a setting? If RakNet is going away, can a minimal client speak NetherNet, and is it
   documented? This could kill the idea, so answer it first.
2. **Auth and online-mode.** With `online-mode=false`: do loopback/LAN clients join without Xbox auth while remote
   clients still must authenticate? Do remote players still get xuids in the console? Can LAN clients impersonate a
   real player's name, and does an allowlist mitigate it? Is `online-mode=true` with a self-signed chain rejected?
3. **Minimum handshake.** Which packets must the client send to start receiving `TextPacket`s? Does it have to fully
   spawn (`SetLocalPlayerAsInitialized`) and so appear in the player list and take a slot? Can it stay invisible
   (spectator via console command)? Does `player-idle-timeout` kick a passive client?
4. **Stability across versions.** How often have the login/handshake and `TextPacket` layouts changed historically
   (e.g. the TextPacket category restructure)? Can the protocol version be taken from the ping reliably?
5. **Implementation language and size.** Python (matching this repo, stdlib + `cryptography` at most?) vs a small
   vendored library. What would a RakNet (or NetherNet) client, batching/compression, ECDH + AES encryption and JWT
   login cost in lines and dependencies?
6. **What it sees.** Exact death message keys and parameters (victim, killer), chat, joins/leaves; whether achievement
   announcements reach other players (the user has seen achievements in the server console and in nearby players' chat).

## Constraints

- Feasibility only until the study is reviewed.
- Testing may run a throwaway BDS in a temp directory bound to localhost only (not LAN), never the live world.
