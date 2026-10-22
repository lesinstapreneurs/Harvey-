"""Prospects the Writer can't use must not stop Harvey from prospecting.

The Writer only drafts for verified or risky addresses; a guessed, invalid or
missing email leaves the prospect 'new'. The heartbeat used to count every
'new' prospect, so a single guessed address made it choose write_campaign
every cycle, the Writer had nothing to do, and Scout never ran again.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio

from harvey.main import decide_next_action
from harvey.models.prospect import Prospect
from harvey.state import StateManager

CONFIG = SimpleNamespace(channels=SimpleNamespace(email=SimpleNamespace(
    provider="gmail", require_approval=True)))


def config(enabled=True, provider="gmail", require_approval=True):
    return SimpleNamespace(channels=SimpleNamespace(email=SimpleNamespace(
        enabled=enabled, provider=provider, require_approval=require_approval)))


# A pipeline with drafts waiting AND prospects left to work on: the shape that
# exposes a stall, because a draft outranks both writing and prospecting.
BUSY = {"prospects": {"new": 50}, "writable_prospects": 50,
        "draft_campaigns": 2, "open_conversations": 0}


@pytest_asyncio.fixture
async def state(tmp_path):
    state = StateManager(str(tmp_path / "stall.db"))
    await state.init_db()
    return state


async def add(state, n, email, email_status):
    await state.add_prospect(Prospect(
        first_name=f"P{n}", last_name="Doe", title="Owner", company=f"Co{n}",
        email=email, email_status=email_status,
    ))


@pytest.mark.asyncio
async def test_summary_counts_only_prospects_the_writer_can_use(state):
    await add(state, 1, "a@one.test", "verified")
    await add(state, 2, "b@two.test", "risky")
    await add(state, 3, "c@three.test", "guess")
    await add(state, 4, "d@four.test", "invalid")
    await add(state, 5, "", "")
    summary = await state.get_state_summary()
    assert summary["prospects"]["new"] == 5
    assert summary["writable_prospects"] == 2


@pytest.mark.asyncio
async def test_guessed_emails_alone_lead_back_to_prospecting(state):
    for n in range(3):
        await add(state, n, f"p{n}@co{n}.test", "guess")
    summary = await state.get_state_summary()
    assert await decide_next_action(None, state, CONFIG, summary=summary) == "prospect"


async def age_all(state, days):
    """Pretend every prospect was found ``days`` ago."""
    import aiosqlite
    from datetime import datetime, timedelta, timezone
    then = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(state.db_path) as db:
        await db.execute("UPDATE prospects SET created_at = ?", (then,))
        await db.commit()


@pytest.mark.asyncio
async def test_a_day_of_unusable_prospects_pauses_spending_and_says_why(state):
    # With no usable email at all (say, no verifier key), prospecting every
    # cycle would burn search and verifier credits on people Harvey can
    # never email. A day's worth pauses it, and status explains why.
    from harvey.main import PROSPECT_BACKLOG, unusable_backlog_notice
    for n in range(PROSPECT_BACKLOG):
        await add(state, n, f"p{n}@co{n}.test", "guess")
    summary = await state.get_state_summary()
    assert await decide_next_action(None, state, CONFIG, summary=summary) == "idle"
    notice = unusable_backlog_notice(summary)
    assert str(PROSPECT_BACKLOG) in notice and "verifier" in notice and "about a day" in notice


@pytest.mark.asyncio
async def test_the_pause_never_becomes_permanent(state):
    # Nothing re-checks a guessed address, so an all-time count would stop
    # Scout for good. Once the guesses are a day old, prospecting resumes.
    from harvey.main import PROSPECT_BACKLOG, unusable_backlog_notice
    for n in range(PROSPECT_BACKLOG + 10):
        await add(state, n, f"p{n}@co{n}.test", "guess")
    await age_all(state, days=2)
    summary = await state.get_state_summary()
    assert await decide_next_action(None, state, CONFIG, summary=summary) == "prospect"
    assert unusable_backlog_notice(summary) == ""


@pytest.mark.asyncio
async def test_old_guesses_do_not_stop_a_mixed_setup_returning_to_prospecting(state):
    for n in range(25):
        await add(state, n, f"p{n}@co{n}.test", "guess")
    await age_all(state, days=3)
    await add(state, 100, "v@verified.test", "verified")
    summary = await state.get_state_summary()
    assert await decide_next_action(None, state, CONFIG, summary=summary) == "write_campaign"
    [verified] = [p for p in await state.get_prospects_by_status("new") if p.email_status == "verified"]
    await state.update_prospect_status(verified.id, "queued")  # what the Writer does
    summary = await state.get_state_summary()
    assert await decide_next_action(None, state, CONFIG, summary=summary) == "prospect"


@pytest.mark.asyncio
async def test_no_backlog_notice_while_things_are_moving(state):
    from harvey.main import unusable_backlog_notice
    await add(state, 1, "p1@co1.test", "guess")
    assert unusable_backlog_notice(await state.get_state_summary()) == ""
    await add(state, 2, "p2@co2.test", "verified")
    for n in range(3, 30):
        await add(state, n, f"p{n}@co{n}.test", "guess")
    assert unusable_backlog_notice(await state.get_state_summary()) == ""


@pytest.mark.asyncio
async def test_a_deliverable_prospect_is_still_written_for(state):
    await add(state, 1, "p1@co1.test", "guess")
    await add(state, 2, "p2@co2.test", "verified")
    summary = await state.get_state_summary()
    assert await decide_next_action(None, state, CONFIG, summary=summary) == "write_campaign"


@pytest.mark.asyncio
async def test_a_summary_without_the_new_count_keeps_the_old_rule():
    # Callers that build their own summary (older tests, scripts) still work.
    summary = {"prospects": {"new": 3}, "draft_campaigns": 0, "open_conversations": 0}
    assert await decide_next_action(None, None, CONFIG, summary=summary) == "write_campaign"


# ── blocked sending must not stop the pipeline that fills it ───────────

@pytest.mark.asyncio
async def test_sending_on_still_deploys_drafts(state):
    """The ordinary case: a draft that can go out wins, as before."""
    assert await decide_next_action(None, state, config(), BUSY) == "send_campaign"


@pytest.mark.asyncio
async def test_a_disabled_email_channel_does_not_stall_the_loop(state):
    """`channels.email.enabled: false` left drafts winning every cycle.

    There is no send_campaign branch that runs an agent, so Harvey did no
    prospecting and no writing until someone turned sending back on.
    """
    assert await decide_next_action(None, state, config(enabled=False),
                                    BUSY) == "write_campaign"


@pytest.mark.asyncio
async def test_the_kill_switch_does_not_stall_the_loop(state):
    """`harvey sending pause` stops outbound, not the funnel behind it.

    Pausing overnight used to mean no new prospects in the morning.
    """
    await state.set_setting("sending_paused", "paused manually")
    assert await decide_next_action(None, state, config(), BUSY) == "write_campaign"


@pytest.mark.asyncio
async def test_resuming_deploys_the_drafts_that_piled_up(state):
    await state.set_setting("sending_paused", "paused manually")
    assert await decide_next_action(None, state, config(), BUSY) == "write_campaign"
    await state.set_setting("sending_paused", "")
    assert await decide_next_action(None, state, config(), BUSY) == "send_campaign"


@pytest.mark.asyncio
async def test_blocked_sending_falls_all_the_way_to_prospecting(state):
    """With nothing left to write, the funnel keeps being filled."""
    await state.set_setting("sending_paused", "paused manually")
    quiet = {**BUSY, "prospects": {"new": 0}, "writable_prospects": 0}
    assert await decide_next_action(None, state, config(), quiet) == "prospect"


@pytest.mark.asyncio
async def test_a_reply_still_outranks_everything_while_paused(state):
    await state.set_setting("sending_paused", "paused manually")
    busy = {**BUSY, "open_conversations": 3}
    assert await decide_next_action(None, state, config(), busy) == "handle_replies"


@pytest.mark.asyncio
async def test_held_instantly_is_still_guarded(state):
    assert await decide_next_action(
        None, state, config(provider="instantly"), BUSY) == "write_campaign"


@pytest.mark.asyncio
async def test_no_state_means_no_settings_read(state):
    """decide_next_action is called without state in places; it must not crash."""
    assert await decide_next_action(None, None, config(), BUSY) == "send_campaign"
