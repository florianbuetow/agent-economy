# E2E Test Plan for Agent Economy UI

**Date:** 2026-03-02
**Approach:** Playwright for Python + pre-seeded SQLite fixture database
**Total tests:** 251

## Architecture

- **Test runner:** pytest with `@pytest.mark.e2e` marker
- **Browser automation:** Playwright async API (Chromium)
- **Server:** FastAPI UI service started as subprocess per session, pointed at fixture DB
- **Fixture DB:** Pre-seeded SQLite with complete economy state (agents at various reputation levels, tasks at every lifecycle stage, events, escrow, feedback, rulings)
- **DB mutation:** Tests INSERT/UPDATE rows directly to simulate lifecycle progression, then assert UI updates via page refresh or SSE

## Fixture Database Strategy

The fixture DB contains:
- 10+ agents (mix of workers and posters, varying reputation/earnings)
- Tasks at every lifecycle stage (posted, bidding, contracted, delivered, reviewing, disputed, settled)
- Event history (50+ events of various types)
- Escrow records, feedback records, court claims/rulings
- Bank accounts with balances

Tests that need state changes (e.g., advancing a task from bidding to contracted) mutate the fixture DB directly and either refresh or wait for SSE updates.

## Test Sections

### Section 1: Infrastructure (no test cases — setup only)
- Playwright fixture with browser launch/teardown
- FastAPI server fixture with fixture DB
- DB seeding helpers
- Page object models for landing, observatory, task pages
- SSE event injection helper
- DB mutation helpers

### Section 2: Landing Page (58 tests, L01–L58)

| Sub | Area | Tests |
|-----|------|-------|
| 2.1 | Navigation & routing | L01–L04 |
| 2.2 | Top ticker | L05–L10 |
| 2.3 | Hero section | L11–L14 |
| 2.4 | KPI strip | L15–L23 |
| 2.5 | Exchange board | L24–L33 |
| 2.6 | How It Works | L34–L36 |
| 2.7 | Market story | L37–L41 |
| 2.8 | Leaderboard | L42–L52 |
| 2.9 | Bottom ticker | L53–L55 |
| 2.10 | Live updates | L56–L58 |

### Section 3: Observatory Page (81 tests, O01–O81)

| Sub | Area | Tests |
|-----|------|-------|
| 3.1 | Navigation & layout | O01–O05 |
| 3.2 | Vitals bar | O06–O14 |
| 3.3 | GDP panel sections | O15–O30 |
| 3.4 | Feed filter buttons | O31–O40 |
| 3.5 | Feed items | O41–O50 |
| 3.6 | Feed pause/resume | O51–O55 |
| 3.7 | SSE live events | O56–O63 |
| 3.8 | Leaderboard tabs | O64–O72 |
| 3.9 | Bottom ticker | O73–O75 |
| 3.10 | Periodic refresh | O76–O78 |
| 3.11 | Edge cases | O79–O81 |

### Section 4: Task Lifecycle Page (98 tests, T01–T98)

| Sub | Area | Tests |
|-----|------|-------|
| 4.1 | Task selection & routing | T01–T06 |
| 4.2 | Phase strip | T07–T12 |
| 4.3 | Phase 1: Post | T13–T18 |
| 4.4 | Phase 2: Bid | T19–T27 |
| 4.5 | Phase 3: Contract | T28–T33 |
| 4.6 | Phase 4: Deliver | T34–T39 |
| 4.7 | Phase 5: Review | T40–T46 |
| 4.8 | Phase 6: Dispute & ruling | T47–T56 |
| 4.9 | Phase 7: Settlement | T57–T62 |
| 4.10 | Event feed panel | T63–T68 |
| 4.11 | Step navigation controls | T69–T78 |
| 4.12 | Keyboard shortcuts | T79–T84 |
| 4.13 | Auto-play | T85–T88 |
| 4.14 | Boundary guards | T89–T93 |
| 4.15 | DB mutation live update | T94–T96 |
| 4.16 | Feedback display | T97–T98 |

Note: Task lifecycle page is currently 100% hardcoded demo. Tests define the target behavior for when it becomes API-driven. Tests will initially fail, which is expected.

### Section 5: Cross-Page & Edge Cases (14 tests, X01–X14)

| Sub | Area | Tests |
|-----|------|-------|
| 5.1 | Empty DB | X01–X04 |
| 5.2 | Single-agent economy | X05–X07 |
| 5.3 | Cross-page navigation | X08–X10 |
| 5.4 | Browser edge cases | X11–X14 |

## Key Decisions

1. Tests run against a real FastAPI server (not mocked) for true E2E coverage
2. Fixture DB is copied fresh per test session to avoid cross-test contamination
3. Task lifecycle tests define target API-driven behavior — they will fail until task.js is refactored
4. SSE tests use real EventSource connections, injecting events via DB inserts
5. Tests focus on functional correctness (data display, interactions), not visual/CSS
