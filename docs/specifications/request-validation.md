# Request Validation Specification

## Overview

Services validate incoming requests using public key infrastructure (PKI). An agent signs a request with its private key, producing a certificate. The receiving service verifies the certificate using the agent's public key. If the decrypted certificate matches the original request payload, the request is authentic.

## How It Works

1. **Agent signs a request** — The agent uses its private key to sign the request payload, producing a certificate.
2. **Service receives request + certificate** — The request body and the certificate are sent together.
3. **Service verifies the certificate** — The service calls `validate_certificate` on the `BaseAgent` class, which decrypts the certificate using the agent's public key.
4. **Match check** — If the decrypted certificate equals the request payload, the request was signed by that agent's private key. The request is valid.

## Implementation

### Platform Agent Instantiation

Each service that needs to verify requests instantiates a `PlatformAgent`. When instantiated correctly, the platform agent is loaded with its public and private key. This is the only setup required — once the platform agent is available, the service can verify any request signed by agents.

### `validate_certificate` Method

The `BaseAgent` class exposes a `validate_certificate` method (to be implemented if not already present). This method:

1. Takes the request payload and the certificate as input.
2. Decrypts the certificate using the agent's public key.
3. Compares the decrypted result to the original request payload.
4. Returns whether they match.

### Key Points

- **Agent ID does not matter** — Validation depends entirely on whether the certificate was signed with the agent's private key. Identity is proven cryptographically, not by ID lookup.
- **Any service can verify** — As long as the service has the platform agent instantiated with the correct keys, it can verify incoming signed requests.
- **Simple PKI** — This is standard public/private key verification. Sign with private key, verify with public key, compare payloads.

## Example: Bank Deposit

1. An agent creates a deposit request.
2. The agent signs the request with its private key, producing a certificate.
3. The agent sends the request + certificate to the Central Bank.
4. The Central Bank has a `PlatformAgent` instantiated with the platform keys.
5. The bank calls `validate_certificate(request, certificate)` using the agent's public key.
6. If the decrypted certificate matches the request, the deposit is processed.
