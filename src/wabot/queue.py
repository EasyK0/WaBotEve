"""One durable reminder worker; timestamps and retry scheduling live in SQLite."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Protocol

from .models import GRACE_SECONDS, POLL_SECONDS, Timer
from .transport import (
    Delivery,
    ExpiredDeliveryError,
    PermanentDeliveryError,
    TransientDeliveryError,
    UnknownDeliveryError,
)

logger = logging.getLogger(__name__)


class ReminderRepository(Protocol):
    async def claim_next_due(self, now: int) -> Timer | None: ...

    async def update_delivery(
        self,
        timer_id: int,
        status: str,
        *,
        sent_at: int | None = None,
        message_id: str | None = None,
        next_attempt_at: int | None = None,
        last_error: str | None = None,
    ) -> None: ...

    async def recover_sending(self) -> list[int]: ...


class ReminderSender(Protocol):
    async def send(self, timer: Timer, *, delayed: bool) -> Delivery: ...


class DeliveryQueue:
    def __init__(
        self,
        repository: ReminderRepository,
        sender: ReminderSender,
        *,
        clock: Callable[[], float] = time.time,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.repository = repository
        self.sender = sender
        self.clock = clock
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._recovered = False
        self._running = False

    def stop(self) -> None:
        """Stop after the current request and its database update have finished."""
        self._stop.set()

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("The reminder worker is already running.")
        self._running = True
        logger.info("Reminder worker started")
        try:
            while not self._stop.is_set():
                try:
                    processed = await self.run_once()
                except Exception as exc:
                    # Exception text and tracebacks may contain credentials or request data.
                    logger.error("Reminder worker operation failed (%s)", type(exc).__name__)
                    processed = False
                if processed:
                    continue
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("Reminder worker stopped")

    async def run_once(self) -> bool:
        """Claim and process at most one timer, returning whether a timer was claimed."""
        async with self._lock:
            if not self._recovered:
                recovered = await self.repository.recover_sending()
                self._recovered = True
                for timer_id in recovered:
                    logger.warning(
                        "Timer ID: %s delivery_unknown after restart; no automatic retry",
                        timer_id,
                    )
            timer = await self.repository.claim_next_due(int(self.clock()))
            if timer is None:
                return False
            try:
                await self._deliver(timer)
            except Exception as exc:
                logger.error(
                    "Timer ID: %s delivery state could not be persisted (%s); "
                    "sending records require recovery",
                    timer.id,
                    type(exc).__name__,
                )
                raise
            return True

    async def _deliver(self, timer: Timer) -> None:
        now = int(self.clock())
        late_by = now - timer.due_at
        if late_by > GRACE_SECONDS:
            await self.repository.update_delivery(
                timer.id, "skipped_expired", last_error="Reminder is over five minutes late."
            )
            logger.info("Timer ID: %s skipped_expired", timer.id)
            return

        try:
            delivery = await self.sender.send(timer, delayed=late_by >= 30)
        except ExpiredDeliveryError as exc:
            await self.repository.update_delivery(timer.id, "skipped_expired", last_error=str(exc))
            logger.info("Timer ID: %s skipped_expired before POST", timer.id)
        except PermanentDeliveryError as exc:
            await self.repository.update_delivery(timer.id, "failed", last_error=str(exc))
            logger.warning("Timer ID: %s failed: %s", timer.id, exc)
        except TransientDeliveryError as exc:
            delay = (5, 15, 30, 60)[min(max(timer.attempt_count - 1, 0), 3)]
            if exc.retry_after is not None:
                delay = max(delay, exc.retry_after)
            next_attempt_at = math.ceil(self.clock() + delay)
            if next_attempt_at > timer.due_at + GRACE_SECONDS:
                await self.repository.update_delivery(
                    timer.id,
                    "skipped_expired",
                    last_error="No retry is possible within the five-minute delivery window.",
                )
                logger.warning("Timer ID: %s skipped_expired after transient failure", timer.id)
            else:
                await self.repository.update_delivery(
                    timer.id,
                    "retry",
                    next_attempt_at=next_attempt_at,
                    last_error=str(exc),
                )
                logger.warning("Timer ID: %s retry at %s: %s", timer.id, next_attempt_at, exc)
        except asyncio.CancelledError:
            # A forced cancellation can occur after Discord accepted the request.
            await asyncio.shield(
                self.repository.update_delivery(
                    timer.id, "delivery_unknown", last_error="Delivery was interrupted."
                )
            )
            logger.warning("Timer ID: %s delivery_unknown after interruption", timer.id)
            raise
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, UnknownDeliveryError)
                else "Unexpected delivery failure; outcome is unknown."
            )
            await self.repository.update_delivery(timer.id, "delivery_unknown", last_error=error)
            logger.warning("Timer ID: %s delivery_unknown: %s", timer.id, error)
        else:
            await self.repository.update_delivery(
                timer.id,
                "sent",
                sent_at=int(self.clock()),
                message_id=delivery.message_id,
                last_error=delivery.warning,
            )
            logger.info("Timer ID: %s sent; message_id=%s", timer.id, delivery.message_id)
            if delivery.warning:
                logger.warning("Timer ID: %s %s", timer.id, delivery.warning)
