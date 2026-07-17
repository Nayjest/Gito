"""Bounded job queue with a worker pool (Direction 4)."""
from __future__ import annotations

import threading
import time

from codepulse_app.jobqueue import JobQueue


def test_submitted_job_runs():
    q = JobQueue(workers=1, name="t")
    done = threading.Event()
    q.submit("j1", lambda: done.set())
    assert done.wait(timeout=2)
    q.stop()


def test_concurrency_is_bounded_by_worker_count():
    q = JobQueue(workers=2, name="t")
    active = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def work():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        release.wait(timeout=2)
        with lock:
            active -= 1

    for i in range(6):
        q.submit(f"j{i}", work)
    # Give workers a moment to pick up the first batch.
    time.sleep(0.3)
    stats = q.stats()
    assert stats["active"] == 2  # never more than the worker count
    assert stats["queued"] >= 3  # the rest wait
    release.set()
    q.stop(drain=True)
    assert peak == 2


def test_one_failing_job_does_not_kill_the_worker():
    q = JobQueue(workers=1, name="t")
    ran_after = threading.Event()

    def boom():
        raise RuntimeError("kaboom")

    q.submit("bad", boom)
    q.submit("good", lambda: ran_after.set())
    assert ran_after.wait(timeout=2)  # worker survived the exception
    q.stop()


def test_event_hook_fires_start_and_end():
    q = JobQueue(workers=1, name="t")
    events = []
    q.on_event = lambda job_id, phase: events.append((job_id, phase))
    done = threading.Event()
    q.submit("j", lambda: done.set())
    done.wait(timeout=2)
    time.sleep(0.1)
    q.stop()
    assert ("j", "start") in events
    assert ("j", "end") in events


def test_stats_shape():
    q = JobQueue(workers=3, name="t")
    s = q.stats()
    assert s == {"workers": 3, "active": 0, "activeIds": [], "queued": 0}
