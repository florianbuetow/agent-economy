# MP-07 public duplicate-bid error boundary

The reconciled live E2E suite retained the original duplicate-bid assertion and
exposed `409 bid_exists` leaking from Gateway to the public Task Board. Both the
public API specification and acceptance-test specification require
`bid_already_exists`. The native-operation amendment does not change this code.

TaskBiddingCoordinator now maps only Gateway `409 bid_exists` to public
`409 bid_already_exists`, preserving message and details. Other codes and
statuses propagate unchanged. The Gateway's internal vocabulary and storage
tests remain unchanged.

A new six-case unit test captured one real failure and five controls before the
repair, then all six passed. The two original router duplicate-bid expectations
were restored to `bid_already_exists`: an earlier fixture conversion had
incorrectly changed them to the Gateway code. The sequential and concurrent
duplicate-bid router tests now pass against their real disposable Gateway.
This restores the original observable contract rather than updating the live
assertion to the implementation.

Original source and router test snapshots are preserved in
`/private/tmp/agent-economy-mp07-bidding-before-error-map.py` and
`/private/tmp/agent-economy-mp07-router-bids-before-error-restore.py`.
Red, focused green and router evidence is in
`/private/tmp/agent-economy-mp07-bid-error-*.log`.
Fresh component and root gates remain required; T-122/T-123 remain open.
