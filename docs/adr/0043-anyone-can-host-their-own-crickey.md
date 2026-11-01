---
status: accepted
date: 2026-10-29
---

# Anyone can host their own crickey in the cloud

## Context and Problem Statement

crickey runs on localhost by default ([ADR 0020](0020-http-by-default-stdio-for-debugging.md)), so only one machine can use it. A user who wants crickey on every device needs an instance they can reach over the internet, which the localhost defaults don't allow and which must not be open to anyone else. How can a user host their own crickey?

## Decision Drivers

* crickey stays for personal use (D6).
* The local defaults stay as they are.
* An instance other machines can reach is never open without a secret.

## Considered Options

* Settings for a self-hosted instance: bind address, public URL and a token, with the platform's TLS
* Local use only
* OAuth
* TLS inside crickey

## Decision Outcome

Chosen option: "Settings for a self-hosted instance: bind address, public URL and a token, with the platform's TLS", because a static token is enough for one person's instance, and container platforms already provide TLS.

* `CRICKEY_HOST` (`--host`) sets the bind address.
* `CRICKEY_URL` (`--url`) names the instance's public URLs, which the Host and Origin checks then accept as well as localhost; otherwise they'd answer a cloud hostname with 421. One setting covers both checks, because without CORS no other web page can call crickey anyway.
* With `CRICKEY_TOKEN` set, every request but `/health` needs `Authorization: Bearer <token>`. The token comes only from the environment, so it stays out of process lists, and needs at least 32 characters.
* crickey refuses to start without a token when another machine could reach it: a bind address other than loopback, or a URL other than localhost. The image's 0.0.0.0 doesn't count, because Docker publishes it on the host's loopback.
* `/health` stays open for the platform's health checks.
* TLS is the hosting platform's.

### Consequences

* Good, because one person can use their crickey from any device.
* Bad, because a hosted instance is still one person's: their devices share its request spacing and block pause (D9, D11), and Cricinfo may challenge a datacenter IP more often than a home one.

## Pros and Cons of the Options

### Local use only

* Bad, because a user who wants crickey on every device couldn't have it.

### OAuth

* Bad, because a static token is enough for one person's hosted instance.

### TLS inside crickey

* Bad, because the hosting platform terminates TLS.

## More Information

* The README's "Host it in the cloud" section has the setup.
