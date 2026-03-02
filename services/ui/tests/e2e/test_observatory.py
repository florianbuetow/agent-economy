"""Observatory page E2E tests (tickets O01-O81)."""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from .fixtures.seed_db import extend_seed_data
from .helpers.db_helpers import (
    create_escrow,
    get_writable_db_connection,
    insert_event,
    wait_for_sse_update,
)
from .pages.observatory import ObservatoryPage

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page


def _ensure_extended_seed_data(db_path: Path) -> None:
    """Ensure extended E2E seed data has been applied to the fixture DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        agent_count = conn.execute("SELECT COUNT(*) FROM identity_agents").fetchone()[0]
        if agent_count < 10:
            extend_seed_data(conn)
    finally:
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def _ensure_observatory_seed_data(e2e_db_path: Path) -> None:
    """Autouse fixture to guarantee observatory tests run on extended fixture data."""
    _ensure_extended_seed_data(e2e_db_path)


def _now_iso() -> str:
    """Return current UTC timestamp in project ISO format."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request_json(e2e_page: Page, e2e_server: str, path: str) -> dict[str, Any]:
    """GET JSON payload from the running E2E server."""
    resp = e2e_page.request.get(f"{e2e_server}{path}")
    assert resp.status == 200
    return resp.json()


def _js_round(value: float) -> int:
    """Match JavaScript Math.round() semantics for non-negative values."""
    return int(value + 0.5)


def _parse_int(text: str) -> int:
    """Extract the first integer-like value from text."""
    match = re.search(r"-?\d[\d,]*", text)
    assert match is not None, f"Expected integer value in text: {text!r}"
    return int(match.group(0).replace(",", ""))


def _vital_value(vitals: dict[str, dict[str, str]], label: str) -> str:
    """Get a vital value by label with a useful assertion message."""
    assert label in vitals, f"Missing vital label: {label}. Found: {sorted(vitals)}"
    return vitals[label]["value"]


def _open_observatory(
    observatory: ObservatoryPage,
    e2e_page: Page,
    *,
    wait_for_feed: bool = False,
) -> None:
    """Open observatory page and wait for key sections to load."""
    observatory.navigate()
    e2e_page.wait_for_function(
        "document.querySelectorAll('.vital-item').length >= 7",
    )
    e2e_page.wait_for_function(
        "document.querySelectorAll('#filter-btns .feed-btn').length >= 11",
    )
    if wait_for_feed:
        e2e_page.wait_for_function("document.querySelectorAll('.feed-item').length > 0")


def _gdp_detail_map(observatory: ObservatoryPage) -> dict[str, str]:
    """Read GDP panel detail rows as {label: value}."""
    rows = observatory.gdp_panel.locator(".gdp-detail-row").all()
    details: dict[str, str] = {}
    for row in rows:
        label = (row.locator(".gdp-detail-label").text_content() or "").strip()
        value = (row.locator(".gdp-detail-value").text_content() or "").strip()
        if label:
            details[label] = value
    return details


def _insert_runtime_event(
    e2e_db_path: Path,
    *,
    event_source: str,
    event_type: str,
    summary: str,
    payload: dict[str, Any],
    task_id: str | None = None,
    agent_id: str | None = None,
) -> int:
    """Insert a runtime event into the E2E DB."""
    conn = get_writable_db_connection(str(e2e_db_path))
    try:
        return insert_event(
            conn,
            event_source=event_source,
            event_type=event_type,
            summary=summary,
            payload=payload,
            task_id=task_id,
            agent_id=agent_id,
            timestamp=_now_iso(),
        )
    finally:
        conn.close()


def _insert_board_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    poster_id: str,
    reward: int,
    status: str,
    created_at: str,
    worker_id: str | None,
    approved_at: str | None,
) -> None:
    """Insert a board task row with explicit fields used by metrics/agents refresh tests."""
    created_dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    bidding_deadline = (created_dt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    execution_deadline = (created_dt + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    review_deadline = (created_dt + timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO board_tasks ("
        "task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, escrow_id, worker_id, accepted_bid_id, dispute_reason, ruling_id, "
        "worker_pct, ruling_summary, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, ruled_at, expired_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
        ")",
        (
            task_id,
            poster_id,
            f"Runtime {task_id}",
            "Runtime inserted task for E2E refresh tests",
            reward,
            status,
            86_400,
            604_800,
            172_800,
            bidding_deadline,
            execution_deadline,
            review_deadline,
            f"esc-{task_id}",
            worker_id,
            None,
            None,
            None,
            None,
            None,
            created_at,
            None,
            None,
            approved_at,
            None,
            None,
            None,
            None,
        ),
    )
    conn.commit()


def _wait_for_vital_int_at_least(
    observatory: ObservatoryPage,
    e2e_page: Page,
    *,
    label: str,
    minimum: int,
    timeout_seconds: float,
) -> int:
    """Poll vitals until value is at least the expected minimum."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        vitals = observatory.get_vitals()
        if label in vitals:
            current = _parse_int(vitals[label]["value"])
            if current >= minimum:
                return current
        e2e_page.wait_for_timeout(400)
    current_text = observatory.get_vitals().get(label, {}).get("value", "")
    raise AssertionError(
        f"Timed out waiting for vital {label!r} >= {minimum}, current text={current_text!r}"
    )


def _agents_for_observatory(e2e_page: Page, e2e_server: str) -> list[dict[str, Any]]:
    """Reconstruct observatory agent model from API response."""
    data = _request_json(
        e2e_page,
        e2e_server,
        "/api/agents?sort_by=total_earned&order=desc&limit=50",
    )
    result: list[dict[str, Any]] = []
    for agent in data["agents"]:
        stats = agent["stats"]
        earned = int(stats["total_earned"])
        spent = int(stats["total_spent"])
        result.append(
            {
                "name": agent["name"],
                "role": "worker" if earned >= spent else "poster",
                "tc": int(stats["tasks_completed_as_worker"]),
                "tp": int(stats["tasks_posted"]),
                "earned": earned,
                "spent": spent,
            }
        )
    return result


def _read_leaderboard_rows(e2e_page: Page) -> list[dict[str, str]]:
    """Read leaderboard rows using current observatory DOM selectors."""
    rows = e2e_page.locator("#lb-scroll .lb-row").all()
    result: list[dict[str, str]] = []
    for row in rows:
        rank = (row.locator(".lb-rank").text_content() or "").strip()
        name = (row.locator(".lb-name").text_content() or "").strip()
        value = (row.locator(".lb-amount").text_content() or "").strip()
        result.append({"rank": rank, "name": name, "value": value})
    return result


def _o15_expected_section_labels(observatory: ObservatoryPage) -> list[str]:
    """Read section labels from GDP panel."""
    return [
        text.strip()
        for text in observatory.gdp_panel.locator(".gdp-section-label").all_text_contents()
    ]


def _o30_expected_distribution(metrics: dict[str, Any]) -> list[int]:
    """Compute expected reward distribution percentages exactly as frontend rounding does."""
    rd = metrics["labor_market"]["reward_distribution"]
    total = rd["0_to_10"] + rd["11_to_50"] + rd["51_to_100"] + rd["over_100"]
    if total == 0:
        return [0, 0, 0, 0]
    return [
        round((rd["0_to_10"] / total) * 100),
        round((rd["11_to_50"] / total) * 100),
        round((rd["51_to_100"] / total) * 100),
        round((rd["over_100"] / total) * 100),
    ]


def _check_o15(
    observatory: ObservatoryPage,
    _metrics: dict[str, Any],
    _details: dict[str, str],
) -> None:
    assert _o15_expected_section_labels(observatory) == [
        "Economy Output",
        "GDP / Agent",
        "Economy Phase",
        "Labor Market",
        "Reward Distribution",
    ]


def _check_o16(
    observatory: ObservatoryPage,
    metrics: dict[str, Any],
    _details: dict[str, str],
) -> None:
    output = observatory.gdp_panel.locator(".gdp-section").first
    big = (output.locator(".gdp-big").text_content() or "").strip()
    unit = (output.locator(".gdp-unit").text_content() or "").strip()
    assert _parse_int(big) == metrics["gdp"]["total"]
    assert "total GDP" in unit


def _check_o17(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    assert f"{metrics['gdp']['rate_per_hour']:.1f}" in details["Rate"]


def _check_o18(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    assert _parse_int(details["Last 24h"]) == metrics["gdp"]["last_24h"]


def _check_o19(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    assert _parse_int(details["Last 7d"]) == metrics["gdp"]["last_7d"]


def _check_o20(
    observatory: ObservatoryPage,
    metrics: dict[str, Any],
    _details: dict[str, str],
) -> None:
    section = observatory.gdp_panel.locator(".gdp-section").nth(1)
    value_locator = section.locator("div[style*='font-size:20px']")
    big = (value_locator.text_content() or "").strip()
    assert _parse_int(big) == _js_round(float(metrics["gdp"]["per_agent"]))


def _check_o21(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    assert _parse_int(details["Active"]) == metrics["agents"]["active"]


def _check_o22(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    assert _parse_int(details["Registered"]) == metrics["agents"]["total_registered"]


def _check_o23(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    assert _parse_int(details["With completed"]) == metrics["agents"]["with_completed_tasks"]


def _check_o24(
    observatory: ObservatoryPage,
    metrics: dict[str, Any],
    _details: dict[str, str],
) -> None:
    phase = (observatory.gdp_panel.locator(".gdp-phase-badge").text_content() or "").strip()
    assert phase == metrics["economy_phase"]["phase"].upper()


def _check_o25(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    trend = metrics["economy_phase"]["task_creation_trend"]
    assert trend in details["Task creation"]


def _check_o26(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    disputed = metrics["tasks"]["disputed"]
    completed = max(metrics["tasks"]["completed_all_time"], 1)
    expected = f"{(disputed / completed) * 100:.1f}%"
    assert details["Dispute rate"] == expected


def _check_o27(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    expected = f"{metrics['labor_market']['avg_bids_per_task']:.1f}"
    assert details["Avg bids / task"] == expected


def _check_o28(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    expected = f"{metrics['labor_market']['acceptance_latency_minutes']:.0f} min"
    assert details["Accept latency"] == expected


def _check_o29(
    _observatory: ObservatoryPage,
    metrics: dict[str, Any],
    details: dict[str, str],
) -> None:
    expected = f"{metrics['tasks']['completion_rate'] * 100:.0f}%"
    assert details["Completion rate"] == expected


def _check_o30(
    observatory: ObservatoryPage,
    metrics: dict[str, Any],
    _details: dict[str, str],
) -> None:
    actual = [
        _parse_int(text)
        for text in observatory.gdp_panel.locator(".dist-row .dist-pct").all_text_contents()
    ]
    assert actual == _o30_expected_distribution(metrics)


_O15_O30_CHECKS = {
    "O15": _check_o15,
    "O16": _check_o16,
    "O17": _check_o17,
    "O18": _check_o18,
    "O19": _check_o19,
    "O20": _check_o20,
    "O21": _check_o21,
    "O22": _check_o22,
    "O23": _check_o23,
    "O24": _check_o24,
    "O25": _check_o25,
    "O26": _check_o26,
    "O27": _check_o27,
    "O28": _check_o28,
    "O29": _check_o29,
    "O30": _check_o30,
}


def _check_o64(
    observatory: ObservatoryPage,
    _e2e_page: Page,
    _workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    assert observatory.get_active_tab() == "workers"


def _check_o65(
    _observatory: ObservatoryPage,
    e2e_page: Page,
    _workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    assert len(_read_leaderboard_rows(e2e_page)) > 0


def _check_o66(
    _observatory: ObservatoryPage,
    e2e_page: Page,
    _workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    rows = _read_leaderboard_rows(e2e_page)
    ranks = [_parse_int(row["rank"]) for row in rows]
    assert ranks == list(range(1, len(rows) + 1))


def _check_o67(
    _observatory: ObservatoryPage,
    e2e_page: Page,
    workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    rows = _read_leaderboard_rows(e2e_page)
    assert workers
    assert rows
    assert rows[0]["name"].startswith(workers[0]["name"])


def _switch_to_posters_and_wait(observatory: ObservatoryPage, e2e_page: Page) -> None:
    observatory.click_tab("posters")
    e2e_page.wait_for_function(
        "document.querySelectorAll('#lb-scroll .lb-row').length > 0",
    )


def _check_o68(
    observatory: ObservatoryPage,
    e2e_page: Page,
    _workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    _switch_to_posters_and_wait(observatory, e2e_page)
    assert observatory.get_active_tab() == "posters"


def _check_o69(
    observatory: ObservatoryPage,
    e2e_page: Page,
    _workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    _switch_to_posters_and_wait(observatory, e2e_page)
    assert len(_read_leaderboard_rows(e2e_page)) > 0


def _check_o70(
    observatory: ObservatoryPage,
    e2e_page: Page,
    _workers: list[dict[str, Any]],
    posters: list[dict[str, Any]],
) -> None:
    _switch_to_posters_and_wait(observatory, e2e_page)
    rows = _read_leaderboard_rows(e2e_page)
    assert posters
    assert rows
    assert rows[0]["name"].startswith(posters[0]["name"])


def _check_o71(
    observatory: ObservatoryPage,
    e2e_page: Page,
    _workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    _switch_to_posters_and_wait(observatory, e2e_page)
    values = [row["value"] for row in _read_leaderboard_rows(e2e_page)]
    assert values
    assert all("\u00a9" in value for value in values)


def _check_o72(
    observatory: ObservatoryPage,
    e2e_page: Page,
    workers: list[dict[str, Any]],
    _posters: list[dict[str, Any]],
) -> None:
    observatory.click_tab("posters")
    observatory.click_tab("workers")
    e2e_page.wait_for_function(
        "document.querySelectorAll('#lb-scroll .lb-row').length > 0",
    )
    rows = _read_leaderboard_rows(e2e_page)
    assert workers
    assert rows
    assert rows[0]["name"].startswith(workers[0]["name"])


_O64_O72_CHECKS = {
    "O64": _check_o64,
    "O65": _check_o65,
    "O66": _check_o66,
    "O67": _check_o67,
    "O68": _check_o68,
    "O69": _check_o69,
    "O70": _check_o70,
    "O71": _check_o71,
    "O72": _check_o72,
}


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O01", id="O01"),
        pytest.param("O02", id="O02"),
        pytest.param("O03", id="O03"),
        pytest.param("O04", id="O04"),
        pytest.param("O05", id="O05"),
    ],
)
def test_o01_o05_navigation_layout(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Navigation and primary layout assertions."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page, wait_for_feed=True)

    if ticket == "O01":
        assert "/observatory.html" in e2e_page.url
        assert e2e_page.title() == "ATE Observatory — Agent Task Economy"
    elif ticket == "O02":
        assert observatory.vitals_bar.is_visible()
        assert observatory.vitals_bar.locator(".vital-item").count() >= 7
    elif ticket == "O03":
        assert observatory.gdp_panel.is_visible()
        assert observatory.gdp_panel.locator(".gdp-section").count() >= 5
    elif ticket == "O04":
        assert observatory.feed_scroll.is_visible()
        assert observatory.pause_btn.is_visible()
        assert e2e_page.locator("#filter-btns").is_visible()
    elif ticket == "O05":
        assert e2e_page.locator("#lb-scroll").is_visible()
        assert observatory.bottom_ticker.is_visible()
        assert observatory.live_dot.is_visible()
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O06", id="O06"),
        pytest.param("O07", id="O07"),
        pytest.param("O08", id="O08"),
        pytest.param("O09", id="O09"),
        pytest.param("O10", id="O10"),
        pytest.param("O11", id="O11"),
        pytest.param("O12", id="O12"),
        pytest.param("O13", id="O13"),
        pytest.param("O14", id="O14"),
    ],
)
def test_o06_o14_vitals_bar(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Vitals bar values must match pre-seeded API data."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page)
    metrics = _request_json(e2e_page, e2e_server, "/api/metrics")
    vitals = observatory.get_vitals()

    if ticket == "O06":
        expected_labels = {
            "Active Agents",
            "Open Tasks",
            "Completed (24h)",
            "GDP (Total)",
            "GDP / Agent",
            "Unemployment",
            "Escrow Locked",
        }
        assert expected_labels.issubset(set(vitals))
    elif ticket == "O07":
        assert _parse_int(_vital_value(vitals, "Active Agents")) == metrics["agents"]["active"]
    elif ticket == "O08":
        assert _parse_int(_vital_value(vitals, "Open Tasks")) == metrics["tasks"]["open"]
    elif ticket == "O09":
        completed_text = _vital_value(vitals, "Completed (24h)")
        assert _parse_int(completed_text) == metrics["tasks"]["completed_24h"]
    elif ticket == "O10":
        assert _parse_int(_vital_value(vitals, "GDP (Total)")) == metrics["gdp"]["total"]
    elif ticket == "O11":
        expected_delta = f"\u2191{metrics['gdp']['rate_per_hour']:.1f}/hr"
        assert vitals["GDP (Total)"]["delta"] == expected_delta
    elif ticket == "O12":
        expected = _js_round(float(metrics["gdp"]["per_agent"]))
        assert _parse_int(_vital_value(vitals, "GDP / Agent")) == expected
    elif ticket == "O13":
        expected = f"{metrics['labor_market']['unemployment_rate'] * 100:.1f}%"
        assert _vital_value(vitals, "Unemployment") == expected
    elif ticket == "O14":
        escrow_text = _vital_value(vitals, "Escrow Locked")
        assert "\u00a9" in escrow_text
        assert _parse_int(escrow_text) == metrics["escrow"]["total_locked"]
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O15", id="O15"),
        pytest.param("O16", id="O16"),
        pytest.param("O17", id="O17"),
        pytest.param("O18", id="O18"),
        pytest.param("O19", id="O19"),
        pytest.param("O20", id="O20"),
        pytest.param("O21", id="O21"),
        pytest.param("O22", id="O22"),
        pytest.param("O23", id="O23"),
        pytest.param("O24", id="O24"),
        pytest.param("O25", id="O25"),
        pytest.param("O26", id="O26"),
        pytest.param("O27", id="O27"),
        pytest.param("O28", id="O28"),
        pytest.param("O29", id="O29"),
        pytest.param("O30", id="O30"),
    ],
)
def test_o15_o30_gdp_panel(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """GDP panel sections and values."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page)
    metrics = _request_json(e2e_page, e2e_server, "/api/metrics")
    details = _gdp_detail_map(observatory)
    check = _O15_O30_CHECKS[ticket]
    check(observatory, metrics, details)


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O31", id="O31"),
        pytest.param("O32", id="O32"),
        pytest.param("O33", id="O33"),
        pytest.param("O34", id="O34"),
        pytest.param("O35", id="O35"),
        pytest.param("O36", id="O36"),
        pytest.param("O37", id="O37"),
        pytest.param("O38", id="O38"),
        pytest.param("O39", id="O39"),
        pytest.param("O40", id="O40"),
    ],
)
def test_o31_o40_feed_filters(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Feed filter buttons and behavior."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page, wait_for_feed=True)

    if ticket == "O31":
        labels = [
            text.strip() for text in e2e_page.locator("#filter-btns .feed-btn").all_text_contents()
        ]
        assert labels == [
            "ALL",
            "TASK",
            "BID",
            "PAYOUT",
            "CONTRACT",
            "ESCROW",
            "SUBMIT",
            "REP",
            "DISPUTE",
            "RULING",
            "CANCEL",
            "AGENT",
        ]
    elif ticket == "O32":
        assert observatory.get_active_filter() == "ALL"
    elif ticket in {"O33", "O34", "O35", "O36", "O37", "O38", "O39"}:
        ticket_to_filter = {
            "O33": "TASK",
            "O34": "BID",
            "O35": "PAYOUT",
            "O36": "CONTRACT",
            "O37": "ESCROW",
            "O38": "DISPUTE",
            "O39": "AGENT",
        }
        filter_name = ticket_to_filter[ticket]
        observatory.click_filter(filter_name)
        e2e_page.wait_for_timeout(100)
        assert observatory.get_active_filter() == filter_name
        items = observatory.get_feed_items()
        assert items
        assert all(item["badge"] == filter_name for item in items)
    elif ticket == "O40":
        observatory.click_filter("TASK")
        task_count = len(observatory.get_feed_items())
        observatory.click_filter("ALL")
        all_count = len(observatory.get_feed_items())
        assert all_count >= task_count
        assert all_count > 0
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O41", id="O41"),
        pytest.param("O42", id="O42"),
        pytest.param("O43", id="O43"),
        pytest.param("O44", id="O44"),
        pytest.param("O45", id="O45"),
        pytest.param("O46", id="O46"),
        pytest.param("O47", id="O47"),
        pytest.param("O48", id="O48"),
        pytest.param("O49", id="O49"),
        pytest.param("O50", id="O50"),
    ],
)
def test_o41_o50_feed_items(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Feed item rendering and seeded content."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page, wait_for_feed=True)

    if ticket == "O41":
        assert len(observatory.get_feed_items()) > 0
    elif ticket == "O42":
        assert len(observatory.get_feed_items()) <= 50
    elif ticket == "O43":
        first = observatory.get_feed_items()[0]
        assert first["badge"]
        assert first["text"]
        assert first["time"]
    elif ticket == "O44":
        latest = _request_json(e2e_page, e2e_server, "/api/events?limit=1")["events"][0]
        assert observatory.get_feed_items()[0]["text"] == latest["summary"]
    elif ticket == "O45":
        items = observatory.get_feed_items()
        assert any("Judy received salary" in item["text"] for item in items)
    elif ticket == "O46":
        times = [item["time"] for item in observatory.get_feed_items()]
        assert times
        assert all(time_text.endswith("ago") for time_text in times)
    elif ticket == "O47":
        observatory.click_filter("TASK")
        items = observatory.get_feed_items()
        assert items
        assert all(item["badge"] == "TASK" for item in items)
    elif ticket == "O48":
        observatory.click_filter("AGENT")
        items = observatory.get_feed_items()
        assert items
        assert all(item["badge"] == "AGENT" for item in items)
    elif ticket == "O49":
        observatory.click_filter("RULING")
        items = observatory.get_feed_items()
        assert items
        assert all(item["badge"] == "RULING" for item in items)
    elif ticket == "O50":
        classes = observatory.feed_scroll.locator(".feed-item").first.get_attribute("class") or ""
        assert "highlight" in classes
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O51", id="O51"),
        pytest.param("O52", id="O52"),
        pytest.param("O53", id="O53"),
        pytest.param("O54", id="O54"),
        pytest.param("O55", id="O55"),
    ],
)
def test_o51_o55_pause_resume(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
    e2e_db_path: Path,
) -> None:
    """Pause/resume controls and interaction with live feed updates."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page, wait_for_feed=True)

    if ticket == "O51":
        button_text = (observatory.pause_btn.text_content() or "").strip()
        assert not observatory.is_paused()
        assert "Pause" in button_text
    elif ticket == "O52":
        observatory.click_pause()
        button_text = (observatory.pause_btn.text_content() or "").strip()
        assert observatory.is_paused()
        assert "Resume" in button_text
    elif ticket == "O53":
        observatory.click_pause()
        observatory.click_pause()
        assert not observatory.is_paused()
    elif ticket == "O54":
        observatory.click_pause()
        assert observatory.is_paused()
        before_count = len(observatory.get_feed_items())
        summary = f"O54 paused event {int(time.time())}"
        _insert_runtime_event(
            e2e_db_path,
            event_source="board",
            event_type="task.created",
            summary=summary,
            payload={"title": "Paused path"},
            task_id="t-task6",
            agent_id="a-alice",
        )
        e2e_page.wait_for_timeout(2_000)
        after_items = observatory.get_feed_items()
        assert len(after_items) == before_count
        assert all(summary not in item["text"] for item in after_items)
    elif ticket == "O55":
        if observatory.is_paused():
            observatory.click_pause()
        summary = f"O55 resumed event {int(time.time())}"
        _insert_runtime_event(
            e2e_db_path,
            event_source="board",
            event_type="task.created",
            summary=summary,
            payload={"title": "Resume path"},
            task_id="t-task6",
            agent_id="a-alice",
        )
        wait_for_sse_update(e2e_page, timeout=8_000)
        e2e_page.wait_for_function(
            f"document.querySelector('.feed-item .feed-text') && "
            f"document.querySelector('.feed-item .feed-text').textContent.includes('{summary}')",
        )
        top = observatory.get_feed_items()[0]
        assert top["text"] == summary
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O56", id="O56"),
        pytest.param("O57", id="O57"),
        pytest.param("O58", id="O58"),
        pytest.param("O59", id="O59"),
        pytest.param("O60", id="O60"),
        pytest.param("O61", id="O61"),
        pytest.param("O62", id="O62"),
        pytest.param("O63", id="O63"),
    ],
)
def test_o56_o63_sse_live_events(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
    e2e_db_path: Path,
) -> None:
    """SSE-driven live feed updates for mapped event types."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page, wait_for_feed=True)

    if ticket == "O56":
        assert observatory.live_dot.is_visible()
        return

    observatory.click_filter("ALL")
    if observatory.is_paused():
        observatory.click_pause()

    event_map = {
        "O57": ("board", "task.created", "TASK"),
        "O58": ("board", "bid.submitted", "BID"),
        "O59": ("bank", "escrow.locked", "ESCROW"),
        "O60": ("board", "task.approved", "PAYOUT"),
        "O61": ("reputation", "feedback.revealed", "REP"),
        "O62": ("board", "task.disputed", "DISPUTE"),
        "O63": ("board", "task.ruled", "RULING"),
    }
    source, event_type, expected_badge = event_map[ticket]
    summary = f"{ticket} live event {int(time.time())}"
    _insert_runtime_event(
        e2e_db_path,
        event_source=source,
        event_type=event_type,
        summary=summary,
        payload={"ticket": ticket},
        task_id="t-task6",
        agent_id="a-alice",
    )
    wait_for_sse_update(e2e_page, timeout=8_000)
    e2e_page.wait_for_function(
        f"document.querySelector('.feed-item .feed-text') && "
        f"document.querySelector('.feed-item .feed-text').textContent.includes('{summary}')",
    )
    top = observatory.get_feed_items()[0]
    assert top["badge"] == expected_badge
    assert summary in top["text"]


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O64", id="O64"),
        pytest.param("O65", id="O65"),
        pytest.param("O66", id="O66"),
        pytest.param("O67", id="O67"),
        pytest.param("O68", id="O68"),
        pytest.param("O69", id="O69"),
        pytest.param("O70", id="O70"),
        pytest.param("O71", id="O71"),
        pytest.param("O72", id="O72"),
    ],
)
def test_o64_o72_leaderboard_tabs(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Leaderboard tab switching and seeded ranking order."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page)
    observatory.click_tab("workers")
    e2e_page.wait_for_function("document.querySelectorAll('#lb-scroll .lb-row').length > 0")
    agents = _agents_for_observatory(e2e_page, e2e_server)
    workers = sorted(
        [agent for agent in agents if agent["role"] == "worker"],
        key=lambda agent: agent["tc"],
        reverse=True,
    )
    posters = sorted(
        [agent for agent in agents if agent["role"] == "poster"],
        key=lambda agent: agent["tp"],
        reverse=True,
    )
    check = _O64_O72_CHECKS[ticket]
    check(observatory, e2e_page, workers, posters)


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O73", id="O73"),
        pytest.param("O74", id="O74"),
        pytest.param("O75", id="O75"),
    ],
)
def test_o73_o75_bottom_ticker(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Bottom ticker content should be present and duplicated."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page)
    ticker_text = (observatory.bottom_ticker.text_content() or "").strip()

    if ticket == "O73":
        assert len(ticker_text) > 40
    elif ticket == "O74":
        assert "TASKS/ALL" in ticker_text
        assert "GDP/TOTAL" in ticker_text
        assert "ESCROW/LOCK" in ticker_text
    elif ticket == "O75":
        assert ticker_text.count("GDP/TOTAL") >= 2
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O76", id="O76"),
        pytest.param("O77", id="O77"),
        pytest.param("O78", id="O78"),
    ],
)
def test_o76_o78_periodic_refresh(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
    e2e_db_path: Path,
) -> None:
    """Periodic refresh checks (metrics polling and agents polling)."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page)

    if ticket == "O76":
        initial_open = _parse_int(_vital_value(observatory.get_vitals(), "Open Tasks"))
        task_id = f"t-o76-{int(time.time())}"
        now_iso = _now_iso()
        conn = get_writable_db_connection(str(e2e_db_path))
        try:
            _insert_board_task(
                conn,
                task_id=task_id,
                poster_id="a-alice",
                reward=77,
                status="open",
                created_at=now_iso,
                worker_id=None,
                approved_at=None,
            )
        finally:
            conn.close()
        updated_open = _wait_for_vital_int_at_least(
            observatory,
            e2e_page,
            label="Open Tasks",
            minimum=initial_open + 1,
            timeout_seconds=16,
        )
        assert updated_open == initial_open + 1
    elif ticket == "O77":
        initial_locked = _parse_int(_vital_value(observatory.get_vitals(), "Escrow Locked"))
        conn = get_writable_db_connection(str(e2e_db_path))
        try:
            create_escrow(
                conn,
                escrow_id=f"esc-o77-{int(time.time())}",
                payer_account_id="a-alice",
                amount=33,
                task_id=f"t-o77-{int(time.time())}",
                status="locked",
                created_at=_now_iso(),
            )
        finally:
            conn.close()
        updated_locked = _wait_for_vital_int_at_least(
            observatory,
            e2e_page,
            label="Escrow Locked",
            minimum=initial_locked + 33,
            timeout_seconds=16,
        )
        assert updated_locked == initial_locked + 33
    elif ticket == "O78":
        observatory.click_tab("workers")
        conn = get_writable_db_connection(str(e2e_db_path))
        try:
            for index in range(5):
                task_id = f"t-o78-{int(time.time())}-{index}"
                _insert_board_task(
                    conn,
                    task_id=task_id,
                    poster_id="a-alice",
                    reward=55 + index,
                    status="approved",
                    created_at=_now_iso(),
                    worker_id="a-dave",
                    approved_at=_now_iso(),
                )
        finally:
            conn.close()
        e2e_page.wait_for_function(
            "(() => {"
            " const el = document.querySelector('#lb-scroll .lb-row .lb-name');"
            " return !!el && el.textContent.trim().startsWith('Dave');"
            "})()",
            timeout=40_000,
        )
        rows = _read_leaderboard_rows(e2e_page)
        assert rows
        assert rows[0]["name"].startswith("Dave")
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("O79", id="O79"),
        pytest.param("O80", id="O80"),
        pytest.param("O81", id="O81"),
    ],
)
def test_o79_o81_edge_cases(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
    e2e_db_path: Path,
) -> None:
    """Edge-case observatory behavior."""
    observatory = ObservatoryPage(e2e_page, e2e_server)
    _open_observatory(observatory, e2e_page, wait_for_feed=True)
    observatory.click_filter("ALL")
    if observatory.is_paused():
        observatory.click_pause()

    if ticket == "O79":
        summary = f"O79 unknown event {int(time.time())}"
        _insert_runtime_event(
            e2e_db_path,
            event_source="board",
            event_type="custom.unknown",
            summary=summary,
            payload={"note": "fallback mapping"},
            task_id="t-task6",
            agent_id="a-alice",
        )
        wait_for_sse_update(e2e_page, timeout=8_000)
        top = observatory.get_feed_items()[0]
        assert top["badge"] == "TASK"
        assert top["text"] == summary
    elif ticket == "O80":
        conn = get_writable_db_connection(str(e2e_db_path))
        try:
            for index in range(40):
                insert_event(
                    conn,
                    event_source="board",
                    event_type="task.created",
                    summary=f"O80 burst {index}",
                    payload={"index": index},
                    task_id="t-task6",
                    agent_id="a-alice",
                    timestamp=_now_iso(),
                )
        finally:
            conn.close()
        e2e_page.wait_for_function(
            "document.querySelectorAll('.feed-item').length >= 80",
            timeout=20_000,
        )
        assert e2e_page.locator(".feed-item").count() == 80
    elif ticket == "O81":
        observatory.click_filter("BID")
        before = observatory.get_feed_items()
        summary = f"O81 off-filter task event {int(time.time())}"
        _insert_runtime_event(
            e2e_db_path,
            event_source="board",
            event_type="task.created",
            summary=summary,
            payload={"off_filter": True},
            task_id="t-task6",
            agent_id="a-alice",
        )
        e2e_page.wait_for_timeout(2_000)
        after = observatory.get_feed_items()
        assert observatory.get_active_filter() == "BID"
        assert all(item["badge"] == "BID" for item in after)
        assert len(after) == len(before)
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")
