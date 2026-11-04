# Qwen3-0.6B classification prompt diagnostic — 2026-11-04

The measured classification experiment showed the configured LLM worker winning
one auction but repeatedly producing a solution rejected by `LLMSolver`. One
existing seed was reproduced against the configured local profile without changing
the model, temperature, token limit, parser, seed, or acceptance rule.

## Baseline request

- Seed: `Route five support tickets (batch A)`
- Model: `qwen3-0.6b`
- Temperature: `0.2`
- Maximum completion tokens: `1024`
- Finish reason: `stop`
- Usage: 144 prompt tokens, 944 completion tokens, including 820 reasoning tokens

Raw response content:

```json
{"labels":{"<id>":"billing","<id>":"feature_request","<id>":"feature_request","<id>":"account","<id>":"billing"},"rationale":"Input [a1] indicates a billing issue with two charges, [a2] describes a feature request for an export button functionality problem, [a3] asks for adding a dark mode to the dashboard (feature request), [a4] points to an account management issue with password reset email problems, and [a5] requests changing payment methods (billing)."}
```

The request completed normally; it did not exhaust the token limit. JSON duplicate
keys collapse the literal `<id>` entries to one mapping, so the existing parser
correctly rejected the incomplete label set. A diagnostic `/no_think` request used
55 completion tokens (one reasoning token) but still emitted the literal `<id>`
and a malformed nested rationale, so disabling reasoning was not a repair.

## Minimal repair

The static placeholder example was removed. `build_prompt()` now renders the
current task's exact label and rationale key lists. The parser and its requirement
that every input ID have an allowed label are unchanged.

## Repaired request

The same seed and configured request completed with:

- Finish reason: `stop`
- Usage: 236 prompt tokens, 657 completion tokens, including 519 reasoning tokens
- Parser result: accepted

Raw response content:

```json
{
  "labels": {
    "a1": "billing",
    "a2": "feature_request",
    "a3": "feature_request",
    "a4": "account",
    "a5": "billing"
  },
  "rationale": [
    "I was charged twice for the March invoice indicates a billing issue.",
    "The export button not working is a feature request to improve functionality.",
    "Adding a dark mode to the dashboard is a feature request for user experience.",
    "Forgot password and reset email issue relates to account management.",
    "Switching payment method is a billing request to update payment settings."
  ]
}
```

The model still classified `a2` as `feature_request` rather than the private
reference `bug`, yielding four correct labels out of five. That meets this seed's
published `min_correct=4` threshold. The rationale array is not the requested
object, but the existing parser deliberately treats non-object rationale as absent;
label identity and allowed-label validation remain strict.
