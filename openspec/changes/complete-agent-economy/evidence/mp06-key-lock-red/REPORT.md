# MP-06 key-lock pathname replacement red proof

Date: 2026-10-15

Status: meaningful fail-first evidence only. This does not claim MP-06 implementation or
completion.

## Contract under test

The public `service_auth.signing.load_keypair(handle, keys_dir)` path must reject a key lock whose
canonical pathname is replaced after the original lock inode is opened. The MP-06 batch snapshot
will depend on this same primitive while holding multiple key locks, so continuing on the unlinked
inode would split synchronization between the snapshot and a concurrent process locking the
replacement pathname.

The test uses only a pytest temporary directory and generated disposable Ed25519 material. It
replaces the disposable `<handle>.lock` immediately before the shared `flock` call, while retaining
the already-open descriptor. The hook proves that the opened descriptor initially names the
original inode and that the replacement path names a different inode.

## Result

The suite collected three tests. The two controls passed:

- an ordinary generated pair loads through the public API;
- a corrupt disposable private key raises `corrupt_private`, after which an independent
  nonblocking exclusive acquisition proves that exception cleanup released the shared lock.

The replacement regression failed with `DID NOT RAISE KeyMaterialError`. The public load returned a
valid pair even though it acquired `flock` on the unlinked original inode while the canonical path
named a different unlocked inode. This is the intended red condition and is a behavioral failure,
not a collection, fixture, environment, or prerequisite failure.

The smallest implementation change is to make the existing lock-open primitive compare the opened
descriptor's regular-file device/inode identity with a fresh `lstat(lock_path)` after open and again
after `flock`, rejecting a mismatch as `KeyMaterialError(reason="unsafe_path")`. The descriptor must
still close and any acquired lock must still release on every rejection path.

## Verification

See [red.log](red.log) for the exact frozen-environment command and output: **1 failed, 2 passed**.
The evidence test also passed scoped `ruff check` and `ruff format --check`. No library, product,
repository test, configuration, ordinary key, or Git state was changed by this proof.
