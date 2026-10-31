# MP-07 Board read fixture reconciliation

The two Board read suites must keep their original read/filter/pagination/count/not-found assertions.
Their fixtures omit explicit acceptance windows, declare bidding cutoffs inconsistent with creation,
and seed bids/accepted tasks through retired generic writers. Repair only those prerequisite histories.

Keep explicit fixed fixture terms and derive the varying-created_at fixture's cutoff by one-day UTC
arithmetic. Scope the Gateway task business clock to2026-02-28T15:00:00Z (after all test publications,
before every fixture bidding cutoff), independently of real JWT validity. Publish with an explicit
3600-second acceptance window. Submit bids through actual native operations using worker actor,
open/version0 and an explicit50 reward offer. Create the status-filter's accepted fixture with a
registered worker, real native bid, and real native acceptance of the100 reward task. Assert setup
responses and target states; no direct SQL/state override or fabricated operation history.

All existing API assertions remain unchanged. Whole-suite baseline and final runs cover both read
modules. These fixtures prove Gateway read behavior over actual native state, not external Bank or
Court delivery. Production code and unrelated write/constraint test suites remain outside this tranche.
