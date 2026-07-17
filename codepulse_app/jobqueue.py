"""Bounded job queue with a worker pool.

Reviews and generations used to each spawn their own unbounded thread, so N
simultaneous requests meant N concurrent subprocesses all competing for one
local GPU (or a cloud rate limit). This is a small fixed-size worker pool: at
most ``workers`` jobs run at once and the rest wait in FIFO order, so the box
stays responsive under load and runs stay predictable.

It is deliberately generic (submit any callable) so it has no dependency on
the server module and can be unit-tested in isolation.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    fn: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    submitted_at: float = field(default_factory=time.monotonic)


class JobQueue:
    def __init__(self, workers: int = 2, name: str = "job") -> None:
        self.workers = max(1, int(workers))
        self.name = name
        self._q: queue.Queue[Job | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._started = False
        # Optional hook(job_id, phase) where phase in {"start","end","error"}.
        self.on_event: Callable[[str, str], None] | None = None

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            for i in range(self.workers):
                t = threading.Thread(
                    target=self._run, name=f"code-doctor-{self.name}-worker-{i}", daemon=True
                )
                t.start()
                self._threads.append(t)

    def submit(self, job_id: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """Enqueue a job. Starts the pool lazily on first use."""
        if not self._started:
            self.start()
        self._q.put(Job(id=job_id, fn=fn, args=args, kwargs=kwargs))
        return job_id

    def _emit(self, job_id: str, phase: str) -> None:
        if self.on_event:
            try:
                self.on_event(job_id, phase)
            except Exception:  # noqa: BLE001 - telemetry must never break a worker
                pass

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:  # shutdown sentinel
                self._q.task_done()
                return
            with self._lock:
                self._active.add(job.id)
            self._emit(job.id, "start")
            try:
                job.fn(*job.args, **job.kwargs)
                self._emit(job.id, "end")
            except Exception:  # noqa: BLE001 - one bad job must not kill the worker
                self._emit(job.id, "error")
            finally:
                with self._lock:
                    self._active.discard(job.id)
                self._q.task_done()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            active = sorted(self._active)
        return {
            "workers": self.workers,
            "active": len(active),
            "activeIds": active,
            "queued": self._q.qsize(),
        }

    def stop(self, drain: bool = False) -> None:
        """Signal workers to exit. With drain, wait for pending jobs first."""
        if drain:
            self._q.join()
        for _ in self._threads:
            self._q.put(None)
        for t in self._threads:
            t.join(timeout=5)
        with self._lock:
            self._threads.clear()
            self._started = False
