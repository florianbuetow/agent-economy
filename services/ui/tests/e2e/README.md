# UI browser fixture boundary

The Playwright suite starts one isolated UI process and a disposable seeded
SQLite database. It does not start Task Board, Gateway or Bank. Production task
reads still require Task Board; `_server_app.py` installs
`SeededTaskBoardReader` only inside this child test process so the browser sees
the same public wire shape.

The adapter reads core task fields and lifecycle version from the disposable
seed, computes the current public bid count, and returns the exact eight-field
`financial_effect` object required by MP-08. It does not run or imitate the
production receipt classifier. Paid, refunded and split visual stories are an
explicit fixture declaration in `_board_read_adapter.py`; they prove rendering
of an authoritative wire projection, not authentic Bank validation. The real
Gateway → signed TaskDbClient → TaskManager → Task Board route → UserAgent → UI
proof remains in `tests/integration/test_mp08_real_task_read_boundary.py`.

Every other browser story derives only whether the seeded escrow row is
actually locked. Missing escrow evidence produces `attention_required` with an
absent receipt, even when the task's business status is terminal or disputed.
Browser assertions must therefore use MP-08 labels such as `PAID`, `SPLIT`,
`LOCKED`, and `SETTLEMENT NEEDS ATTENTION`; task status alone must never make a
story appear paid.
