# MP-07 current-runtime pragma fixture reconciliation

The full discovery reports six failures in test_mp06_schema: two version3-as-future controls and
four runtime admission/journal/postcondition cases seeded with frozen v2. Update only future version
literal4 and current-runtime fixture/catalog expectations to frozen v3; rename the current-runtime
case accordingly. The invalid-pragma input cases must likewise start from a current catalog, even
though their input validation already happens first. Frozen v0/v1 classification, column order,
semantic corruption, bootstrap rollback and nonmutation assertions remain unchanged.

Preserve every original assertion, exception class/message, journal check and closed-connection
proof. Run the entire file, not only the six failures. This is fixture reconciliation after the
reviewed v3 contract, not a relaxation of admission or an automatic old-schema migration.
