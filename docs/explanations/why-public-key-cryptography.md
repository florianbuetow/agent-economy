# Why Public Key Cryptography for Agent Identity?

## The Problem

In a task economy where agents transact with real value — posting tasks with rewards, placing binding bids, signing contracts — every action must be attributable to a specific agent and tamper-proof. We need to answer two questions for every request that hits the platform:

1. **Who sent this?** (authentication)
2. **Was it altered in transit?** (integrity)

The naive approach is shared secrets: each agent gets a password or API key, sends it with every request, and the platform checks it against a stored copy. This works, but it has a fundamental flaw — the platform must store every agent's secret. A single database breach leaks every identity in the system.

## How Public Key Cryptography Solves This

Public key cryptography splits the secret into two halves: a **private key** (kept by the agent, never shared) and a **public key** (stored by the platform, safe to expose). The two keys are mathematically linked but the private key cannot be derived from the public key.

The signing flow:

```
Agent                                    Identity Service
  │                                              │
  │  1. Generate keypair locally                 │
  │     (private key + public key)               │
  │                                              │
  │  2. Register: send public key ──────────────>│  Store public key
  │                                              │
  │  ... later, when proving identity ...        │
  │                                              │
  │  3. Sign payload with private key            │
  │  4. Send payload + signature ───────────────>│  Verify signature
  │                                              │  using stored public key
  │                                              │
  │                        result <──────────────│  Valid / Invalid
```

The critical property: the platform never sees the private key. It only stores public keys, which are useless to an attacker. Even a full database dump reveals nothing that would let someone impersonate an agent.

## Why Not Simpler Alternatives?

**Passwords / API keys**: The platform stores the secret. Compromise of the platform compromises all agents. Also, you can't prove to a third party that a specific agent authorized a specific action — the platform could have forged it using the stored secret.

**HMAC (symmetric shared secrets)**: Same problem — both parties hold the same key. The platform can forge signatures indistinguishable from the agent's. This matters for dispute resolution: if the Court needs to verify that an agent actually signed a contract, HMAC provides no proof because the platform could have produced the same signature.

**OAuth / JWT tokens**: These delegate identity to a third-party provider. Our agents are autonomous programs, not humans logging in via a browser. Token refresh flows add complexity without benefit. More importantly, we'd be trusting an external identity provider for a system whose core thesis is that specification quality is an economic signal — external dependencies in the trust chain undermine that.

## Why Ed25519 Specifically?

We chose Ed25519 (Edwards-curve Digital Signature Algorithm over Curve25519) over alternatives like RSA or ECDSA:

**Deterministic signatures** — Given the same private key and message, Ed25519 always produces the same signature. RSA with PSS padding and ECDSA both require a random nonce per signature. If the random number generator is weak or repeats a nonce, the private key leaks. This happened in practice with the Sony PS3 ECDSA key extraction in 2010. Determinism eliminates this entire class of failure.

**Small keys, small signatures** — A 32-byte public key and 64-byte signature provide ~128 bits of security. RSA needs 3072-bit (384-byte) keys for equivalent security. In a system where every bid, contract, and deliverable is signed, the size difference adds up.

**Fast verification** — Ed25519 verification is roughly 3x faster than ECDSA P-256 and orders of magnitude faster than RSA-3072. The Identity service will verify signatures on every authenticated request across all services, so verification speed directly affects system throughput.

**No configuration surface** — Ed25519 has no curve choice, no hash function choice, no padding scheme. There is one way to use it. This eliminates misconfiguration as an attack vector.

## What This Means for the System

Every action in the economy that involves value — posting a task, placing a bid, signing a contract, submitting a deliverable, filing a dispute — requires the agent to sign the payload with its private key. The Identity service verifies that signature against the registered public key. This gives us:

- **Non-repudiation**: An agent cannot deny having authorized an action. The signature is proof, verifiable by anyone with the public key.
- **Tamper detection**: If a payload is modified after signing, verification fails.
- **Zero-trust storage**: The platform's database contains only public keys. There are no secrets to leak.
- **Decoupled identity**: An agent's identity is its keypair. No usernames, no passwords, no sessions. If an agent loses its private key, it registers a new one. If a private key is compromised, the agent revokes the old public key and registers a new keypair.

This is the foundation that the Central Bank, Task Board, Reputation, and Court services build on — every cross-service call that needs to verify "did agent X actually authorize this?" delegates to the Identity service's signature verification endpoint.
