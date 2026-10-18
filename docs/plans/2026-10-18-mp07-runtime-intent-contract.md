# MP-07 durable runtime intent contract

## Purpose and boundaries

The feeder and math worker have no durable pending-intent store. Their task map, winner/finalized
sets, decisions, bid amount, solution and execution history are process memory. Public Task Board
reads do not expose enough completed-operation history to reconstruct an original request. Exact
replay therefore requires retaining caller-owned identity, source version and terms before transport.

This document specifies the next test-first producer slice, not completed implementation. It covers
known native bid, acceptance, submission and approve/dispute intents. Posting recovery remains MP-05;
asset publication, remote commitment discovery, execution/answer checkpoints, rebuttals, feeder oracle
provenance and receipt-derived earnings remain their full MP-09/10/16/17/18 obligations. No in-memory
compatibility cache may be described as restart recovery. No private Gateway access is introduced.

## Filesystem ownership

Use a small filesystem journal in `agents/src/base_agent/runtime_intents.py`, with explicit config
loading in `runtime_config.py`. SQLite is not selected: the existing no-direct-SQL rule permits only
Gateway and the ratified UI read-only exception. Do not weaken the rule or add suppressions.

Require top-level runtime_state.environment_id, runtime_state.directory, runtime_state.diagnostic_limit
and runtime_state.max_record_bytes. Every value is explicit; limits are strict positive integers.
Resolve the directory
from the explicit config file into an absolute canonical path. Each identity gets a directory keyed
by its public-key fingerprint, one process-long exclusive identity lease, an immutable owner.json,
and per-slot intent files. Reuse the public maintenance-lock context manager with explicit exclusive,
nonblocking mode; do not import private key-store helpers. The configured root is the local ownership
scope; this is not a distributed lock across hosts or unrelated state directories.

Owner data binds schema version, environment, role, profile, handle, registered agent ID, key
fingerprint and every configured service URL exactly. No silent URL normalization or retargeting.
Feeder loops share one lease/store; WorkerFactory must retain its actual CLI profile name. Private
keys, signed tokens and arbitrary upstream bodies never enter the journal. Every intent is bound to
the complete owner manifest's canonical digest, not just the registered agent ID. Startup validates
every persisted record and operation-ID uniqueness before any replay.

Files are regular, non-symlink, private records under owned directories. Publish complete canonical
UTF-8 JSON via a same-directory private temporary file, file fsync, atomic publication/replacement,
and parent-directory fsync; re-read and validate the resulting record. Initial owner publication is
atomic and exclusive using a no-replace link of a complete fsynced temporary inode, so no partial
final owner file is exposed and a concurrently winning owner is never overwritten. A storage or fsync error prevents
mutation transport even if a new valid record may already be visible. Reopening must reuse the valid
surviving intent; it never allocates a replacement identity merely because an acknowledgment was lost.
Initial slot publication is also exclusive; a concurrent winner is never overwritten. Under the held
identity lease, cleanup may remove only exact recognized private staging files left by a crash, with
directory fsync and no promotion of their contents. Unknown or unsafe entries fail closed.

## Intent and replay ordering

Slots are bid, acceptance, submission and review-decision. The last slot freezes approve versus
dispute and the exact reason. Each record contains explicit schema/revision, task/slot/action,
source/target state, operation ID, expected lifecycle version, exact public SDK arguments, immutable
response-validation context, semantic hash, state/result and bounded diagnostics. Context includes
frozen parties, escrow, accepted reward and selected bid amount when the native acknowledgment hash
requires them. Context is separate from actual public SDK call arguments. Court generates a dispute
ID after transport; that ID cannot be required in the pre-transport context. A completed dispute
acknowledgment must bind its newly observed ID to the immutable operation's effect reference.

Always look up the existing slot FIRST. Validate owner/file/schema/hash, then dispatch its exact
frozen request without fetching current task state or recomputing a winner, bid, answer or reason.
Only a missing slot permits a fresh public task read, source eligibility check and decision. Commit
that new intent before any mutation call. Store methods do not call the network. Repeating an exact
create returns the same intent; changed terms under an existing slot are a local conflict.

The existing native SDK commands return TaskBoardCommandResponse with the actual HTTP status and
decoded body. The runtime consumes both; it never infers transport status from operation state.
HTTPStatusError retains actual non-success responses, and transport/contract failures remain uncertain.
Matching completed acknowledgments confirm completion even beside a later current task view. Store
the immutable completion without the mutable current projection. Once complete, later observations
cannot downgrade or replace that completion. A
matching claimed202 remains pending. Timeout/malformed/uncertain acknowledgment retains the intent.
Foreign pending ownership records both IDs and remains blocked/unconfirmed, never completed. Typed
definitive non-application records a terminal diagnostic without allocating a new identity. Integrity
conflicts require attention. Current target status alone is never completion evidence.

Record integrity covers revision, state, result and diagnostics as well as frozen semantics. Stored
terminal rejection requires normalized evidence that is revalidated on load; changing a pending
record's state alone cannot remove it from unfinished work. Native integer bounds and recursive type
checks apply to requests, results and financial effects, including nested boolean substitutions.

Revision/hash checks detect corruption and stale updates observed by an open store. They do NOT
prove detection of an older internally valid record restored across process restart without a trusted
external anchor. Broader restored-journal/remote-discovery recovery remains explicit later work.

The journal directory must be represented in recovery inventory before startup wiring is considered
complete. Absence before first use is distinct from an empty directory. A present journal must be
captured while all existing identity leases are held and the identity set remains stable. Restore
preserves journal bytes; it never silently rewrites owner environment or service URL bindings.

## Test-first implementation sequence

1. Define strict owner/intent/config shapes and add real-filesystem reds for reopen/exact identity,
   changed-slot rejection, wrong owner/URL/profile, second-process lease, malformed files/symlinks,
   private modes, and before/after-replace/fsync faults. Assert no transport after local storage failure.
2. Implement only that persistence boundary with existing stdlib/public lock primitives. Validate
   actual process reopen and exact surviving bytes, not a second fake journal implementation.
3. Add caller reds: frozen intent bypasses selection/review/network pre-read; all five native methods
   send original ID/version/terms; pending/foreign/error responses do not advance phases or earnings.
4. Add explicit TaskFeederConfig acceptance_window_seconds and forward it unchanged. It is distinct
   from acceptance_after_seconds. Selection timing/quorum/quality requirements must follow the actual
   MP-18/Q-16 contract and accepted decisions; do not invent a fallback or change policy silently.
5. Integrate one shared store at feeder startup and one bound store per worker profile; replay known
   unfinished intents with bounded per-item progress. Add process-kill/response-loss proofs appropriate
   to this identity slice, leaving complete solution/assets/oracle recovery visibly open.

MP-05 must separately prepare posting identity/task ID BEFORE transport and supply payload-bound
server replay. Do not journal only the ID returned by today's internally allocating SDK, since that
still loses the task on an ambiguous response. The public posting and compensation contract must be
settled before claiming posting or complete feeder recovery.
