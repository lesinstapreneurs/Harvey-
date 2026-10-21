"""Harvey remembers what it changed and what went wrong.

`actions` says that something happened and `runs` says how a discovery run
ended, but neither answers the two questions you ask after leaving an
autonomous agent running overnight. These tests pin the two behaviours that
make the journal worth having: a modification can be read back with its before
and after, and a repeating failure collapses onto one row with a count instead
of burying everything else.
"""

import pytest
import pytest_asyncio

from harvey.state import StateManager, _fingerprint


@pytest_asyncio.fixture
async def state(tmp_path):
    state = StateManager(str(tmp_path / "journal.db"))
    await state.init_db()
    return state


async def seed_signal(state, code="SERP_RANK"):
    """Register a signal so there is a row for set_signal_status to change."""
    await state.upsert_signal_code(
        code, label="Search rank", description="Where the business ranks.",
    )


def raise_connection_error(n):
    raise ConnectionError(f"provider refused request {n} at /api/v2/search")


# ── modifications ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_is_readable_back_with_before_and_after(state):
    await state.record_change(
        "user", "signal.status", "RUNS_ADS: proposed -> confirmed",
        target="signal:RUNS_ADS",
        before={"status": "proposed"}, after={"status": "confirmed"},
    )
    (entry,) = await state.journal_entries(kind="change")
    assert entry["actor"] == "user"
    assert entry["target"] == "signal:RUNS_ADS"
    assert '"proposed"' in entry["before_json"]
    assert '"confirmed"' in entry["after_json"]


@pytest.mark.asyncio
async def test_confirming_a_signal_records_the_switch(state):
    """Which signals are live decides what Harvey spends money on."""
    await seed_signal(state)
    await state.set_signal_status("SERP_RANK", "confirmed")
    changes = await state.journal_entries(kind="change")
    assert any(c["target"] == "signal:SERP_RANK"
               and "confirmed" in c["after_json"] for c in changes)


@pytest.mark.asyncio
async def test_a_signal_already_in_that_state_records_nothing(state):
    await seed_signal(state)
    await state.set_signal_status("SERP_RANK", "confirmed")
    before = len(await state.journal_entries(kind="change"))
    await state.set_signal_status("SERP_RANK", "confirmed")
    assert len(await state.journal_entries(kind="change")) == before


@pytest.mark.asyncio
async def test_kill_switch_is_remembered(state):
    await state.set_setting("sending_paused", "paused manually")
    changes = await state.journal_entries(kind="change")
    assert any(c["target"] == "setting:sending_paused" for c in changes)


@pytest.mark.asyncio
async def test_journalling_cannot_break_the_mutation_it_records(state, monkeypatch):
    """A broken journal must not give a state change a new way to fail."""
    async def boom(*a, **kw):
        raise RuntimeError("journal table is gone")

    monkeypatch.setattr(state, "record_change", boom)
    await state.set_setting("sending_paused", "paused manually")      # must not raise
    assert await state.get_setting("sending_paused") == "paused manually"


# ── failures ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_same_failure_collapses_onto_one_row(state):
    for i in range(5):
        try:
            raise_connection_error(i)
        except ConnectionError as exc:
            entry = await state.record_error("scout", "search", exc)
    assert entry["occurrences"] == 5
    assert len(await state.open_errors()) == 1


@pytest.mark.asyncio
async def test_distinct_failures_stay_distinct(state):
    try:
        raise_connection_error(1)
    except ConnectionError as exc:
        await state.record_error("scout", "search", exc)
    try:
        raise TimeoutError("smtp handshake timed out")
    except TimeoutError as exc:
        await state.record_error("sender", "send", exc)
    assert len(await state.open_errors()) == 2


@pytest.mark.asyncio
async def test_fingerprint_ignores_the_variable_parts_of_a_message(state):
    """Otherwise every retry looks like a brand new bug."""
    prints = set()
    for i in range(3):
        try:
            raise_connection_error(i)
        except ConnectionError as exc:
            prints.add(_fingerprint("scout", "search", exc))
    assert len(prints) == 1


@pytest.mark.asyncio
async def test_same_error_from_a_different_agent_is_a_different_row(state):
    try:
        raise_connection_error(1)
    except ConnectionError as exc:
        assert (_fingerprint("scout", "search", exc)
                != _fingerprint("writer", "search", exc))


@pytest.mark.asyncio
async def test_recurrence_count_is_the_back_off_signal(state):
    try:
        raise_connection_error(1)
    except ConnectionError as exc:
        assert await state.error_recurrences("scout", "search", exc) == 0
        await state.record_error("scout", "search", exc)
        await state.record_error("scout", "search", exc)
        assert await state.error_recurrences("scout", "search", exc) == 2


@pytest.mark.asyncio
async def test_resolving_hides_it_and_a_recurrence_brings_it_back(state):
    try:
        raise_connection_error(1)
    except ConnectionError as exc:
        entry = await state.record_error("scout", "search", exc)
    assert await state.resolve_error(entry["fingerprint"], "added a key")
    assert await state.open_errors() == []

    try:
        raise_connection_error(2)
    except ConnectionError as exc:
        reopened = await state.record_error("scout", "search", exc)
    assert reopened["resolved_at"] is None
    assert reopened["occurrences"] == 2
    assert len(await state.open_errors()) == 1


@pytest.mark.asyncio
async def test_resolving_an_unknown_fingerprint_is_not_an_error(state):
    assert await state.resolve_error("deadbeefdeadbeef") is False


@pytest.mark.asyncio
async def test_error_keeps_its_traceback_and_context(state):
    try:
        raise_connection_error(3)
    except ConnectionError as exc:
        entry = await state.record_error(
            "scout", "search", exc, context={"query": "roofers denver"}
        )
    assert "ConnectionError" in entry["traceback"]
    assert "roofers denver" in entry["context_json"]


@pytest.mark.asyncio
async def test_open_errors_lists_the_most_persistent_first(state):
    try:
        raise_connection_error(1)
    except ConnectionError as exc:
        for _ in range(3):
            await state.record_error("scout", "search", exc)
    try:
        raise TimeoutError("smtp handshake timed out")
    except TimeoutError as exc:
        await state.record_error("sender", "send", exc)
    rows = await state.open_errors()
    assert [r["occurrences"] for r in rows] == [3, 1]


# ── the heartbeat loop feeds the journal ───────────────────────────────

@pytest.mark.asyncio
async def test_a_failing_agent_lands_in_the_journal(state):
    from harvey.main import _run_agents

    async def failing():
        raise_connection_error(7)

    for _ in range(3):
        await _run_agents([("scout", failing())], state)

    (entry,) = await state.open_errors()
    assert entry["occurrences"] == 3
    assert entry["actor"] == "scout"
    assert entry["action"] == "agent.run"


@pytest.mark.asyncio
async def test_the_loop_still_runs_when_no_state_is_given(state):
    """_run_agents keeps working for callers that pass no state."""
    from harvey.main import _run_agents

    async def failing():
        raise_connection_error(7)

    outcomes = await _run_agents([("scout", failing())])
    assert isinstance(outcomes["scout"], ConnectionError)


@pytest.mark.asyncio
async def test_a_broken_journal_never_ends_a_cycle(state, monkeypatch):
    from harvey.main import _run_agents

    async def boom(*a, **kw):
        raise RuntimeError("journal unavailable")

    monkeypatch.setattr(state, "record_error", boom)

    async def failing():
        raise_connection_error(7)

    outcomes = await _run_agents([("scout", failing())], state)   # must not raise
    assert isinstance(outcomes["scout"], ConnectionError)
