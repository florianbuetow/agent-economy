T-114 strict token test/migration plan (temporary preparation only)

Evidence artifacts
- Shared raw vectors: /tmp/test_mp03_token_contract.py
- Identity ASGI vectors: /tmp/test_mp03_identity_tokens.py
- Shared current baseline: /tmp/agent-economy-mp03-token-shared-baseline.log (exit 0, 40 passed)
- Shared required contract: /tmp/agent-economy-mp03-token-shared-red.log (exit 1, 35 failed)
- Identity current baseline: /tmp/agent-economy-mp03-token-identity-baseline.log (exit 0, 20 passed)
- Identity required contract: /tmp/agent-economy-mp03-token-identity-red.log (exit 1, 13 failed / 21 already pass)

Current demonstrated behavior
1. Shared verification authenticates raw Ed25519 bytes, then crashes on non-object headers and returns non-object payloads.
2. Shared verification admits missing/wrong alg, typ, kid, iat, and exp, including boolean/string/float variants; boolean exp is misclassified as an expired integer.
3. Shared verification accepts no expiry, exact expiry, future iat, exp <= iat, and a locally verified platform token whose kid names another platform identity.
4. Identity returns 500 for authenticated non-object headers; required result is 400 invalid_jws.
5. Identity already returns 400 for non-object payloads, missing/wrong alg, missing/invalid kid, non-string typ, string/float iat, and string/float exp. Preserve these passing cases.
6. Identity admits missing/wrong string typ, missing/bool iat, missing exp, exp <= iat, exact expiry, and future iat. Boolean exp is 401 token_expired instead of 400 invalid_jws.
7. Strict-shaped signed controls pass through both current paths with frozen time.

Migration sequence
1. Land the raw Ed25519 fixture and current/required vectors in repo tests without calling production create_jws for malformed cases.
2. Add one typed service-auth protected-header parser plus TokenPolicy. Require an exact object/profile, JSON integers excluding bool, exp > iat, bounded lifetime, future-iat/skew checks, and now >= exp + skew expiry.
3. Make shared verify_jws return typed verified header + object payload and typed admission exceptions; preserve signature verification over received compact bytes.
4. Move Identity to the shared parser/verifier: inspect typed kid, fetch the key, authenticate, then map malformed=400, expired=401, not-yet-valid=401, signature mismatch=current valid:false/403 policy.
5. Bind local platform verification to the registered platform agent_id so an authentic token with another kid is forbidden.
6. Add required signer configuration (positive TTL, non-negative skew, Identity maximum lifetime) and remove optional/no-expiry production issuance.
7. Introduce TokenContext and target-explicit signing/verification in a separate interface step, then add wrong aud/action/resource route tests with no side effects. Do not cite a missing interface or TypeError as current proof of wrong-audience admission.
8. Run identical vectors through shared, Identity, Central Bank, Task Board, Court, and Reputation adapters; retain already-correct cases and reconcile only demonstrated mismatches.

Scope note
- These temporary suites do not test wrong aud/resource because TokenContext and target-explicit signing do not yet exist.
- They prove header/payload/profile/time/local-kid gaps only.
