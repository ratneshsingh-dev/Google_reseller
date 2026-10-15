"""
Group commit for hot documents.

Many requests at once all need to change the SAME document (a partner's quota record,
its plan counter). One at a time that is a queue of compare-and-swap writes in which
every writer but one loses and retries, so 30 requests take ~30 round trips and can run
out of retries.

update_document() instead lets the requests that arrive while a write is in flight
share the next one: a single thread applies every waiting update, in arrival order, to
one copy of the document and saves it with ONE compare-and-swap. Each caller still gets
exactly the result it would have had running alone (its own post-update snapshot, or its
own exception, e.g. "quota exceeded"), and the check-then-write stays atomic. Across
servers the compare-and-swap still arbitrates; the loser re-reads and re-applies.
"""

from __future__ import annotations

import copy
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.repositories.firestore_client import BaseStore

logger = get_logger(__name__)

MAX_ATTEMPTS = 30
WAIT_TIMEOUT_SECONDS = 300.0


class ContentionError(RuntimeError):
    """The document is too busy: every attempt to save it lost to another writer."""


class _Item:
    __slots__ = ("op", "event", "done", "value", "exc")

    def __init__(self, op: Callable[[dict], Any]) -> None:
        self.op = op
        self.event = threading.Event()
        self.done = False
        self.value: Any = None
        self.exc: Optional[BaseException] = None

    def finish(self, value: Any = None, exc: Optional[BaseException] = None) -> None:
        self.value, self.exc, self.done = value, exc, True
        self.event.set()


class _Queue:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: List[_Item] = []
        self.active = False       # a thread is currently draining this document's queue


_queues: Dict[Tuple[int, str, str], _Queue] = {}
_queues_lock = threading.Lock()


def _queue_for(store: BaseStore, collection: str, doc_id: str) -> _Queue:
    key = (id(store), collection, doc_id)
    with _queues_lock:
        queue = _queues.get(key)
        if queue is None:
            queue = _queues[key] = _Queue()
        return queue


def update_document(
    store: BaseStore,
    collection: str,
    doc_id: str,
    op: Callable[[dict], Any],
    missing: Optional[dict] = None,
) -> Any:
    """Apply `op(data)` to the document atomically and return what it returns.

    `op` receives a private copy of the document, mutates it, and returns the caller's
    result; raising rejects only this caller's update. It must be a pure function of the
    data (it can be re-run after a lost compare-and-swap). If the document does not exist
    it is created from a copy of `missing`; with missing=None a KeyError is raised.
    """
    queue = _queue_for(store, collection, doc_id)
    item = _Item(op)
    with queue.lock:
        queue.pending.append(item)
        lead = not queue.active
        if lead:
            queue.active = True

    if lead:
        # Process the first batch (which contains this caller) right here, so this request
        # is answered as soon as its own write lands; any later arrivals are drained by a
        # background thread rather than delaying this response.
        with queue.lock:
            batch, queue.pending = queue.pending, []
        try:
            _process(store, collection, doc_id, batch, missing)
        finally:
            with queue.lock:
                more = bool(queue.pending)
                if not more:
                    queue.active = False
        if more:
            threading.Thread(
                target=_drain, args=(store, collection, doc_id, queue, missing),
                daemon=True, name=f"drain-{collection}-{doc_id}",
            ).start()

    if not item.event.wait(WAIT_TIMEOUT_SECONDS):
        raise ContentionError(f"Timed out waiting to update {collection}/{doc_id}")
    if item.exc is not None:
        raise item.exc
    return item.value


def _drain(store, collection, doc_id, queue: _Queue, missing) -> None:
    try:
        while True:
            with queue.lock:
                if not queue.pending:
                    queue.active = False
                    return
                batch, queue.pending = queue.pending, []
            _process(store, collection, doc_id, batch, missing)
    except BaseException:
        # Leave the queue usable and release whoever is still waiting.
        with queue.lock:
            stranded, queue.pending = queue.pending, []
            queue.active = False
        for item in stranded:
            item.finish(exc=RuntimeError("update was not completed"))
        raise


def _process(store, collection, doc_id, batch: List[_Item], missing) -> None:
    """Save one batch. Always finishes every item, whatever happens."""
    try:
        _save_batch(store, collection, doc_id, batch, missing)
    except BaseException as exc:  # a store error must reach the callers, never strand them
        for item in batch:
            if not item.done:
                item.finish(exc=exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
        if not isinstance(exc, Exception):
            raise
    finally:
        for item in batch:
            if not item.done:
                item.finish(exc=RuntimeError("update was not completed"))


def _save_batch(store, collection, doc_id, batch: List[_Item], missing) -> None:
    for attempt in range(MAX_ATTEMPTS):
        data, version = store.get_versioned(collection, doc_id)
        if data is None and missing is None:
            for item in batch:
                item.finish(exc=KeyError(f"{collection}/{doc_id} not found"))
            return

        working = copy.deepcopy(data if data is not None else missing)
        outcomes: List[Tuple[_Item, Any, Optional[BaseException]]] = []
        applied = False
        for item in batch:
            trial = copy.deepcopy(working)
            try:
                value = item.op(trial)
            except Exception as exc:        # this caller only; the others still apply
                outcomes.append((item, None, exc))
                continue
            working = trial
            outcomes.append((item, value, None))
            applied = True

        if not applied or store.set_if_version(collection, doc_id, working, version if data is not None else None):
            if len(batch) > 1:
                logger.info("group_commit", collection=collection, doc=doc_id, updates=len(batch),
                            applied=sum(1 for _, _, e in outcomes if e is None), attempt=attempt + 1)
            for item, value, exc in outcomes:
                item.finish(value, exc)
            return

        time.sleep(random.uniform(0.005, 0.05) * (attempt + 1))

    raise ContentionError(f"Could not update {collection}/{doc_id}: too much contention")
