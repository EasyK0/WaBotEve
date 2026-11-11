import asyncio
from dataclasses import replace

import pytest

from wabot.models import Timer
from wabot.queue import DeliveryQueue
from wabot.transport import (
    Delivery,
    ExpiredDeliveryError,
    PermanentDeliveryError,
    TransientDeliveryError,
    UnknownDeliveryError,
)


def make_timer(**changes):
    values = dict(
        id=1,
        interaction_id="100",
        guild_id="10",
        channel_id="20",
        author_id="30",
        author_name="Pilot",
        system="kino",
        payout_at=0,
        due_at=1380,
        created_at=1,
        next_attempt_at=1380,
    )
    return Timer(**(values | changes))


class MemoryRepository:
    """A durable-state fake: separate worker instances retain the same rows."""

    def __init__(self, *timers):
        self.rows = {timer.id: timer for timer in timers}

    async def recover_sending(self):
        recovered = []
        for timer in list(self.rows.values()):
            if timer.status == "sending":
                self.rows[timer.id] = replace(timer, status="delivery_unknown")
                recovered.append(timer.id)
        return recovered

    async def claim_next_due(self, now):
        for timer in self.rows.values():
            if (
                timer.status in {"pending", "retry"}
                and timer.due_at <= now
                and timer.next_attempt_at <= now
            ):
                timer = replace(timer, status="sending", attempt_count=timer.attempt_count + 1)
                self.rows[timer.id] = timer
                return timer
        return None

    async def update_delivery(self, timer_id, status, **changes):
        assert self.rows[timer_id].status == "sending"
        self.rows[timer_id] = replace(self.rows[timer_id], status=status, **changes)


class Sender:
    def __init__(self, error=None, warning=None):
        self.calls = []
        self.error = error
        self.warning = warning

    async def send(self, timer, *, delayed):
        self.calls.append((timer, delayed))
        if self.error is not None:
            raise self.error
        return Delivery("500", self.warning)


def test_delivers_at_due_time_and_never_repeats():
    async def scenario():
        now = [1379]
        repository, sender = MemoryRepository(make_timer()), Sender()
        worker = DeliveryQueue(repository, sender, clock=lambda: now[0])
        assert not await worker.run_once()
        now[0] = 1380
        assert await worker.run_once()
        assert not await worker.run_once()
        row = repository.rows[1]
        assert (row.status, row.sent_at, row.message_id) == ("sent", 1380, "500")
        assert row.attempt_count == 1
        assert len(sender.calls) == 1
        assert sender.calls[0][1] is False
        assert len(repository.rows) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "late,status,delayed",
    [
        (29, "sent", False),
        (30, "sent", True),
        (120, "sent", True),
        (300, "sent", True),
        (301, "skipped_expired", None),
        (360, "skipped_expired", None),
    ],
)
def test_restart_and_delay_boundaries(late, status, delayed):
    async def scenario():
        repository, sender = MemoryRepository(make_timer()), Sender()
        worker = DeliveryQueue(repository, sender, clock=lambda: 1380 + late)
        assert await worker.run_once()
        assert repository.rows[1].status == status
        assert len(repository.rows) == 1
        if delayed is None:
            assert sender.calls == []
        else:
            assert sender.calls[0][1] == delayed

    asyncio.run(scenario())


def test_retry_backoff_then_expiry_retains_record():
    async def scenario():
        now = [1380]
        repository = MemoryRepository(make_timer())
        sender = Sender(TransientDeliveryError("Temporary failure."))
        worker = DeliveryQueue(repository, sender, clock=lambda: now[0])
        for delay in [5, 15, 30, 60, 60, 60, 60]:
            assert await worker.run_once()
            row = repository.rows[1]
            assert row.status == "retry"
            assert row.next_attempt_at == now[0] + delay
            assert not await worker.run_once()
            now[0] += delay
        assert await worker.run_once()
        assert repository.rows[1].status == "skipped_expired"
        assert len(repository.rows) == 1
        assert not await worker.run_once()

    asyncio.run(scenario())


def test_rate_limit_wait_is_never_shortened():
    async def scenario():
        repository = MemoryRepository(make_timer())
        sender = Sender(TransientDeliveryError("Rate limited.", retry_after=45.5))
        worker = DeliveryQueue(repository, sender, clock=lambda: 1380)
        await worker.run_once()
        assert repository.rows[1].next_attempt_at == 1426
        assert repository.rows[1].status == "retry"

    asyncio.run(scenario())


def test_expired_during_channel_lookup_is_skipped():
    async def scenario():
        repository = MemoryRepository(make_timer())
        sender = Sender(ExpiredDeliveryError("Expired before POST."))
        worker = DeliveryQueue(repository, sender, clock=lambda: 1380)
        await worker.run_once()
        assert repository.rows[1].status == "skipped_expired"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error,status",
    [
        (PermanentDeliveryError("Forbidden."), "failed"),
        (UnknownDeliveryError("Timeout."), "delivery_unknown"),
        (RuntimeError("sensitive details"), "delivery_unknown"),
    ],
)
def test_terminal_errors_never_retry(error, status, caplog):
    async def scenario():
        repository, sender = MemoryRepository(make_timer()), Sender(error)
        worker = DeliveryQueue(repository, sender, clock=lambda: 1380)
        await worker.run_once()
        assert repository.rows[1].status == status
        assert not await worker.run_once()
        assert len(sender.calls) == 1
        assert "sensitive details" not in str(repository.rows[1].last_error)

    asyncio.run(scenario())
    assert "Timer ID: 1" in caplog.text
    assert "sensitive details" not in caplog.text


def test_restart_recovers_sending_without_a_second_post(caplog):
    async def scenario():
        repository = MemoryRepository(make_timer(status="sending", attempt_count=1))
        sender = Sender()
        worker = DeliveryQueue(repository, sender, clock=lambda: 1380)
        assert not await worker.run_once()
        assert repository.rows[1].status == "delivery_unknown"
        assert sender.calls == []

    asyncio.run(scenario())
    assert "Timer ID: 1" in caplog.text


def test_warning_persists_on_success(caplog):
    async def scenario():
        repository = MemoryRepository(make_timer())
        sender = Sender(warning="Mention Everyone permission was lost.")
        await DeliveryQueue(repository, sender, clock=lambda: 1380).run_once()
        assert repository.rows[1].status == "sent"
        assert repository.rows[1].last_error == sender.warning

    asyncio.run(scenario())
    assert "Mention Everyone permission was lost." in caplog.text


def test_concurrent_run_once_calls_deliver_serially():
    async def scenario():
        repository = MemoryRepository(make_timer(), make_timer(id=2, interaction_id="101"))
        active = 0
        peak = 0

        class YieldingSender:
            async def send(self, timer, *, delayed):
                nonlocal active, peak
                active += 1
                peak = max(active, peak)
                await asyncio.sleep(0)
                active -= 1
                return Delivery(str(timer.id))

        worker = DeliveryQueue(repository, YieldingSender(), clock=lambda: 1380)
        assert await asyncio.gather(worker.run_once(), worker.run_once()) == [True, True]
        assert peak == 1
        assert all(row.status == "sent" for row in repository.rows.values())

    asyncio.run(scenario())


def test_stop_finishes_inflight_delivery_before_worker_exits():
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        repository = MemoryRepository(make_timer(), make_timer(id=2, interaction_id="101"))

        class BlockingSender:
            async def send(self, timer, *, delayed):
                started.set()
                await finish.wait()
                return Delivery("500")

        worker = DeliveryQueue(repository, BlockingSender(), clock=lambda: 1380)
        task = asyncio.create_task(worker.run())
        await started.wait()
        worker.stop()
        assert not task.done()
        finish.set()
        await asyncio.wait_for(task, timeout=1)
        assert repository.rows[1].status == "sent"
        assert repository.rows[2].status == "pending"

    asyncio.run(scenario())


def test_database_failure_after_send_is_not_retried(caplog):
    async def scenario():
        class BrokenRepository(MemoryRepository):
            async def update_delivery(self, *args, **kwargs):
                raise OSError("database unavailable")

        repository, sender = BrokenRepository(make_timer()), Sender()
        worker = DeliveryQueue(repository, sender, clock=lambda: 1380)
        with pytest.raises(OSError):
            await worker.run_once()
        assert repository.rows[1].status == "sending"
        assert not await worker.run_once()
        restarted = DeliveryQueue(repository, sender, clock=lambda: 1380)
        assert not await restarted.run_once()
        assert repository.rows[1].status == "delivery_unknown"
        assert len(sender.calls) == 1

    asyncio.run(scenario())
    assert "Timer ID: 1" in caplog.text
