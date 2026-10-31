---
status: accepted
date: 2026-10-29
---

# HTTP by default; stdio only for debugging

## Context and Problem Statement

An MCP client starts a stdio server as a fresh process for each session, while a Streamable HTTP server runs once and serves every client. crickey's cache, its spacing between Statsguru requests (D9) and its pause after a block (D11) protect Statsguru only if they last across sessions. How should crickey run, and how does it keep web pages on the same machine out?

## Decision Drivers

* The cache, request spacing and block pause carry across sessions and clients.
* Only the user's own MCP clients reach crickey.
* Setup stays simple for friends who run the Docker image (D24).

## Considered Options

* Streamable HTTP on 127.0.0.1, one process per person, no authentication
* stdio only
* stdio as the Docker image's default
* A bearer token for local use
* Starting automatically at login

## Decision Outcome

Chosen option: "Streamable HTTP on 127.0.0.1, one process per person, no authentication", because it's the only option where the cache, request spacing and block pause carry across sessions, and binding to localhost already keeps other machines out.

* Everyone runs Streamable HTTP on 127.0.0.1, started by hand: natively, or in Docker with the port published only on the host's loopback address.
* One long-running process per person serves all their clients.
* stdio is kept for debugging.
* There's no authentication: binding to localhost, plus the SDK's Host and Origin checks (R12), keeps web pages out. An instance other machines can reach needs a token ([ADR 0043](0043-anyone-can-host-their-own-crickey.md)).

### Consequences

* Good, because every client shares one cache, one request spacing and one block pause.
* Good, because local use needs no secret.
* Bad, because crickey has to be running before clients connect, and started again after a reboot.

## Pros and Cons of the Options

### stdio only

* Good, because clients start and stop the server themselves.
* Bad, because each session is a fresh process, so the cache, request spacing and block pause reset every session.

### stdio as the Docker image's default

* Bad, because everyone who uses the image gets the same resets as with stdio only.

### A bearer token for local use

* Bad, because localhost plus the Host and Origin checks is enough; only an instance other machines can reach needs one.

### Starting automatically at login

* Neutral, because starting by hand is enough.

## More Information

* The README's "Start it" section has the commands, and R12 the SDK's Host and Origin checks.
