"""Performance budgets (release plan §4b). Run manually per release:

    .venv/bin/python scripts/bench.py [--files 5000] [--runs 500]

Generates a synthetic repo and N fake runs in a temp data dir, times each
budget row with the real server/engine functions (no HTTP, no LLM), prints a
pass/fail table, and exits non-zero on any breach. Dependency-free.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from code_doctor_app import context_engine, server, store  # noqa: E402

BUDGETS = [
    ("preflight, {files}-file repo", 1.5),
    ("import graph build (cold), {files} files", 2.5),
    ("list_reviews with {runs} runs", 0.150),
    ("overview with {runs} runs", 0.150),
    ("SQLite appends, 8 threads x 25", 2.0),
]


def make_synthetic_repo(root: Path, file_count: int) -> Path:
    repo = root / "synthetic-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "bench@local"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Bench"], check=True)
    per_dir = 200
    for index in range(file_count):
        directory = repo / f"pkg{index // per_dir:03d}"
        directory.mkdir(exist_ok=True)
        module = directory / f"mod{index % per_dir:03d}.py"
        importee = f"pkg{(index + 1) // per_dir:03d}.mod{(index + 1) % per_dir:03d}"
        module.write_text(
            f"from {importee} import helper\n\n\ndef helper(value):\n    return value + {index}\n"
            if index + 1 < file_count
            else "def helper(value):\n    return value\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "synthetic"], check=True)
    # A working-tree change so preflight and the graph have a diff to look at.
    (repo / "pkg000" / "mod000.py").write_text(
        "def helper(value, extra):\n    return value\n", encoding="utf-8"
    )
    return repo


def seed_fake_runs(count: int) -> None:
    for index in range(count):
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:8]}"
        server.run_dir(run_id).mkdir(parents=True, exist_ok=True)
        server.atomic_write_json(
            server.meta_path(run_id),
            {
                "id": run_id,
                "status": "completed",
                "created_at": f"2026-01-01T{index % 24:02d}:00:00Z",
                "repo_path": "/bench/repo",
                "model": "bench",
                "stats": {"gate": "pass", "risk_score": index % 30, "total_issues": index % 5},
            },
        )


def timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=5000)
    parser.add_argument("--runs", type=int, default=500)
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="code-doctor-bench-"))
    data_dir = root / "code-doctor"
    server.DATA_DIR = data_dir
    server.RUNS_DIR = data_dir / "runs"
    server.AUDIT_LOG = data_dir / "audit.jsonl"
    server.REPOS_FILE = data_dir / "repos.json"
    server.POLICIES_FILE = data_dir / "policies.json"
    server.SUPPRESSIONS_FILE = data_dir / "suppressions.json"
    store.DB_PATH = data_dir / "code-doctor.db"
    server.RUNS_DIR.mkdir(parents=True)

    print(f"building synthetic repo ({args.files} files) …", file=sys.stderr)
    repo = make_synthetic_repo(root, args.files)
    print(f"seeding {args.runs} fake runs …", file=sys.stderr)
    seed_fake_runs(args.runs)

    def bench_preflight() -> None:
        server.preflight_review({"repoPath": str(repo), "mode": "working"})

    def bench_graph() -> None:
        files = context_engine.list_source_files(repo)
        context_engine.build_import_graph(repo, files)

    def bench_list_reviews() -> None:
        server.list_reviews()

    def bench_overview() -> None:
        server.overview()

    def bench_sqlite_threads() -> None:
        def worker(worker_id: int) -> None:
            for index in range(25):
                store.append_audit(
                    {"ts": "bench", "event": "bench_append", "worker": worker_id, "i": index}
                )

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    rows = [
        (BUDGETS[0][0], BUDGETS[0][1], timed(bench_preflight)),
        (BUDGETS[1][0], BUDGETS[1][1], timed(bench_graph)),
        (BUDGETS[2][0], BUDGETS[2][1], timed(bench_list_reviews)),
        (BUDGETS[3][0], BUDGETS[3][1], timed(bench_overview)),
        (BUDGETS[4][0], BUDGETS[4][1], timed(bench_sqlite_threads)),
    ]

    failed = False
    print(f"\n{'operation':<46} {'budget':>9} {'actual':>9}  result")
    print("-" * 78)
    for name, budget, actual in rows:
        label = name.format(files=args.files, runs=args.runs)
        ok = actual <= budget
        failed = failed or not ok
        print(f"{label:<46} {budget:>8.3f}s {actual:>8.3f}s  {'PASS' if ok else 'FAIL'}")

    store.close()
    shutil.rmtree(root, ignore_errors=True)
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
