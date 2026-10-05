"""
Runs background work (provisioning jobs, domain actions) on a bounded thread pool.

Each unit of work gets its own thread and its own event loop. The Google client
libraries and the Firestore client make blocking network calls; isolating them
per thread means one slow Google call never freezes the web server or the other
jobs, and up to `job_concurrency` jobs progress in parallel.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Optional, Set

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_pending: Set[Future] = set()
_pending_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                workers = max(1, get_settings().job_concurrency)
                _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
                logger.info("job_executor_started", workers=workers)
    return _executor


def submit(make_coro: Callable[[], Awaitable[Any]], name: str = "job") -> Future:
    """Queue `make_coro()` to run in a worker thread. Returns immediately."""
    def _run() -> Any:
        return asyncio.run(make_coro())

    future = _pool().submit(_run)
    with _pending_lock:
        _pending.add(future)

    def _done(f: Future) -> None:
        with _pending_lock:
            _pending.discard(f)
        exc = f.exception()
        if exc:
            logger.error("background_job_crashed", job=name, error=str(exc))

    future.add_done_callback(_done)
    return future


async def run_in_worker(make_coro: Callable[[], Awaitable[Any]]) -> Any:
    """Run a coroutine that makes blocking calls on its own thread and await the result."""
    return await asyncio.to_thread(lambda: asyncio.run(make_coro()))


def pending_count() -> int:
    with _pending_lock:
        return len(_pending)


def wait_until_idle(timeout: float = 30.0) -> bool:
    """Block until all submitted work has finished (used by tests)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pending_count() == 0:
            return True
        time.sleep(0.02)
    return False


def reset() -> None:
    """Drop the pool (tests only) so a new job_concurrency setting takes effect."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=True)
        _executor = None
