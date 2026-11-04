# Independent rendered accessibility check

Checked 2026-11-04 against the source hashes recorded in
`source-sha256.txt`. The disposable browser fixture did not touch the normal UI
runtime or its data.

## Result

- The current 390-pixel Home rendering is 390 pixels wide. Navigation, exchange
  metrics, the five-step explanation and both leaderboards reflow without
  page-wide horizontal scrolling. The inspected artifact is
  `home-phone-390-current.png`.
- The normal, muted and dim text tokens measure 15.67:1, 6.70:1 and 7.53:1
  against the body. Dim text measures 7.06:1 on cards. The cyan focus color
  measures 12.56:1 against the body. These exceed the requested 4.5:1 text and
  3:1 control/focus thresholds.
- A keyboard-only posting attempt followed the focus path from Review time to
  Input files and Post task. Leaving Specification blank produced the visible
  and announced `All fields are required.` error. Title, reward and all four
  durations remained unchanged. Focus stayed on Post task with a stable
  2-pixel cyan outline at 12.56:1 contrast. The artifact is
  `posting-keyboard-error-retained.png`.
- Chromium CDP page-scale emulation reached exactly 2.0 at a 1440 x 900 desktop
  layout viewport, yielding a 720 x 450 visual viewport. The task orientation
  and current state remained readable and the layout had no horizontal
  overflow. The artifact is `task-desktop-page-scale-200.png`.
- A separate 720 x 450 layout-viewport run covers the reflow equivalent and is
  retained as `home-layout-720-current.png`.

## Commands and exits

The scratch test used the existing disposable UI Playwright fixtures:

```text
cwd: services/ui
uv run pytest /tmp/test_ui_accessibility_review.py -q \
  --rootdir=/Users/flo/Developer/github/agent-economy/services/ui \
  --basetemp=/tmp/ate-ui-accessibility-review-final
exit: 0 (1 passed in 4.25s)
```

The scratch file lived under `/tmp`; only the evidence in this directory is
retained. Pytest reported one harmless unknown-marker warning because the
scratch file was collected outside the service test tree. The fixture and test
ran successfully.

## Exact limitation

`Emulation.setPageScaleFactor(2)` is Chromium's real page-scale emulation and
reports `visualViewport.scale === 2`. It is not the browser chrome's saved
per-origin desktop zoom preference. The separate 720 x 450 layout run proves
the equivalent reflow behavior. This check therefore supplies rendered page
scale plus equivalent reflow evidence without claiming that the browser's UI
zoom menu was operated.

Machine-readable measurements and focus state are in `review.json`.
