# MP-07 Gateway config and Court integrity fixture repair

Fresh parent discovery2376 reports one valid-config failure (required board_authority absent) and
four Court write-integrity setup errors (task publication fixture lacks acceptance window). Add an
explicit platform public-key path and deadline actor to the valid configuration construction; this
is a config-only test and does not read a key or start services. Add the explicit3600-second test
window and exact created_at+86400 bidding cutoff to the Court integrity task setup. Preserve all
configuration assertions and Court ruling-delete/status-update event/FK rollback assertions.

No production changes, new defaults, normal runtime writes, raw status bypass, or fabricated native
operation histories. The Court integrity suite uses open-task publication as its existing prerequisite
for Court-domain persistence tests; it makes no end-to-end adjudication or settlement claim.
