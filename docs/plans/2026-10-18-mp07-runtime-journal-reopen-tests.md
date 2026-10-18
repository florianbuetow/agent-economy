# MP-07 runtime journal reopen review tests

## Scope

This test-only T-181/T-182 slice turns the independent filesystem review into executable public-store
proofs. It does not prescribe private helper names or an exact owner-digest field. It tests what a
caller can observe when opening, loading or initially creating records in a real temporary journal.
No production, default configuration, existing test, runtime data or Git state is changed.

## Required behavior

1. Every persisted intent is bound to the complete owner scope. A canonical record copied from a
   journal with the same registered agent ID but another environment, profile, service URL set or key
   fingerprint fails during reopen without changing the copied bytes.
2. Reopen performs a strict global scan. Invalid JSON, invalid semantic hashes, filename/identity
   conflicts and duplicate operation IDs across tasks or slots fail before the store becomes usable.
3. After acquiring the identity lease, reopen removes only crash-left private regular staging files
   matching the exact journal staging pattern, then fsyncs the intent directory. It never promotes a
   staging file. Unknown names, public-mode entries and symlinks remain hard failures and are not
   removed.
4. A descriptor opened during directory traversal is closed if its immediate `fstat` or post-open
   `stat` fails. Private directory mode is checked on the opened descriptor, the latest path and every
   later verification, so chmod races fail with `unsafe_mode`.
5. Record reads compare stable identity and metadata after reading. Rewriting the same inode during a
   read fails closed even when the replacement bytes are still valid canonical JSON.
6. Initial intent publication is exclusive. If another writer installs an exact winner, create reuses
   that inode and bytes. If it installs changed terms, create reports `slot_conflict` and preserves the
   winner inode and bytes.
7. A primary write or file-fsync error remains the causal storage failure even if descriptor-close and
   staging-unlink cleanup also fail. Cleanup failures are attached with exception notes, all opened
   descriptors are closed, and no staging path remains.

## Test map

`agents/tests/unit/test_runtime_intent_reopen.py` uses only the public runtime owner, intent and store
interfaces for journal operations. Low-level functions are fault-injected only to create precise
filesystem races and failures:

- four cross-owner copy/reopen cases;
- duplicate preexisting operation identity and two corrupt startup-scan cases;
- known staging cleanup with directory-fsync observation, plus unknown/unsafe entry controls;
- three post-open descriptor failures and two chmod race cases;
- one same-inode record rewrite;
- exact and conflicting concurrent initial winners;
- primary write and primary fsync failures accompanied by two cleanup failures.

The initial red must distinguish accepted unsafe state from fixture failure. All test-created leaked
descriptors are explicitly closed after observation so the red suite itself does not contaminate later
tests. No skip, xfail, retry, sleep or suppression is allowed.

## Verification boundary

Capture frozen product hashes before the new tests, then run the focused file and record each executed
failure/control. Run scoped Ruff, format, Mypy and codespell and freeze the final test/note hashes.
Existing runtime-intent tests, agent component CI and root CI remain parent gates after implementation;
this slice makes no green implementation claim.

## Captured red evidence

The frozen pre-implementation run collects 21 cases with no skips or warnings: 20 fail and the
unknown-entry preservation control passes. The failures classify as four accepted cross-owner
records, one accepted duplicate operation ID, two records admitted without content validation, one
unhandled known staging file, two unsafe staging entries misclassified as generic corruption, three
leaked post-open descriptors, two accepted directory-mode races, one accepted same-inode rewrite,
two nonexclusive initial-publication races, and two primary errors whose cleanup failures are not
attached as notes.

Every failure reaches the intended public store behavior or race assertion. The exact-winner case
shows the newly inserted winner inode was replaced; the changed-winner case shows its bytes were
overwritten. Descriptor tests close the observed leaked descriptor after recording the failure, so
the red run leaves no intentional leak. The product modules remained at the hashes in
`/private/tmp/agent-economy-mp07-runtime-reopen-product-sha256.log` throughout capture.

## Second review tranche

The original 21 cases remain unchanged. Against the frozen second-pass product they all pass. Seven
additional cases cover four later review findings:

- constructing a store with `root_directory=Path("/")` must fail with `unsafe_path`; the test replaces
  `os.open` with a sentinel and never enters the store, proving it performs no filesystem-root write;
- a private regular `.owner.json.<24 hex>.tmp` is removed under the identity lease, the identity
  directory is fsynced, and `owner.json` remains byte-identical;
- public-mode or symlinked owner staging and an unknown identity-directory entry are rejected and
  preserved rather than removed or promoted;
- swapping the public `intents` directory after the record read begins must fail with `unsafe_path`,
  even though the held directory descriptor and record inode remain valid;
- the final descriptor popped by `open_root_directory` is closed when its second `fstat` fails. The
  test permits either translated `RuntimeIntentError` or the injected `OSError`, checks the precise
  second inspection, then verifies `EBADF` and closes an observed leak before failing.

The expanded file collects 28 cases: the 21 original controls pass and exactly seven new cases fail.
The failures are one accepted filesystem root, one unreconciled private owner staging file, two
accepted unsafe owner staging entries, one accepted unknown identity entry, one popped-descriptor
leak, and one accepted post-read directory swap. No failure is a collection or fixture error.

The frozen second-pass product hashes are `170fb29b…` for the store, `5de9b565…` for filesystem
helpers, `6b4cf3c7…` for models, and `c3f2f30f…` for acknowledgment handling. Runtime configuration's
matching filesystem-root rejection is covered in the parent's separate configuration lane.
