"""Local (non-git) project analysis via git snapshots."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_doctor_app import server, snapshot, store


def _folder(tmp_path: Path, name: str = "proj") -> Path:
    proj = tmp_path / name
    proj.mkdir()
    (proj / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (proj / "util.js").write_text("export const g = (a) => a\n", encoding="utf-8")
    # Junk that must never be snapshotted.
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / "dep.js").write_text("x\n", encoding="utf-8")
    (proj / "__pycache__").mkdir()
    (proj / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"\x00")
    return proj


def test_is_git_work_tree_true_and_false(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert snapshot.is_git_work_tree(plain) is False
    subprocess.run(["git", "-C", str(plain), "init", "-q"], check=True)
    assert snapshot.is_git_work_tree(plain) is True


def test_build_snapshot_creates_git_baseline_and_skips_junk(tmp_path):
    proj = _folder(tmp_path)
    tree = snapshot.build_snapshot(proj, tmp_path / "snaps")
    assert snapshot.is_git_work_tree(tree) is True
    # Source folder is untouched — no .git leaked in.
    assert not (proj / ".git").exists()
    names = sorted(p.name for p in tree.iterdir() if p.is_file())
    assert names == ["app.py", "util.js"]
    assert not (tree / "node_modules").exists()
    assert not (tree / "__pycache__").exists()
    tracked = subprocess.run(
        ["git", "-C", str(tree), "ls-files"],
        text=True, capture_output=True, check=True,
    ).stdout.split()
    assert set(tracked) == {"app.py", "util.js"}


def test_snapshot_id_stable_and_refreshes_in_place(tmp_path):
    proj = _folder(tmp_path)
    snaps = tmp_path / "snaps"
    first = snapshot.build_snapshot(proj, snaps)
    (proj / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    second = snapshot.build_snapshot(proj, snaps)
    assert first == second  # same stable location, refreshed
    assert "return a + b" in (second / "app.py").read_text(encoding="utf-8")


def test_build_snapshot_rejects_empty_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no files"):
        snapshot.build_snapshot(empty, tmp_path / "snaps")


def test_build_snapshot_enforces_size_guardrail(tmp_path, monkeypatch):
    proj = _folder(tmp_path)
    monkeypatch.setattr(snapshot, "MAX_SNAPSHOT_FILES", 1)
    with pytest.raises(snapshot.SnapshotTooLargeError):
        snapshot.build_snapshot(proj, tmp_path / "snaps")


def test_list_files_skips_ignored_dirs(tmp_path):
    proj = _folder(tmp_path)
    assert sorted(snapshot.list_files(proj)) == ["app.py", "util.js"]


# --- server integration ---


def _isolate(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "code-doctor"
    monkeypatch.setattr(server, "DATA_DIR", data)
    monkeypatch.setattr(server, "RUNS_DIR", data / "runs")
    monkeypatch.setattr(server, "SNAPSHOTS_DIR", data / "snapshots")
    monkeypatch.setattr(server, "AUDIT_LOG", data / "audit.jsonl")
    monkeypatch.setattr(server, "REPOS_FILE", data / "repos.json")
    monkeypatch.setattr(server, "POLICIES_FILE", data / "policies.json")
    monkeypatch.setattr(server, "SUPPRESSIONS_FILE", data / "suppressions.json")
    monkeypatch.setattr(store, "DB_PATH", data / "code-doctor.db")


def test_resolve_review_target_snapshots_non_git_and_passes_git_through(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    proj = _folder(tmp_path)
    review_path, source_path, is_snapshot = server.resolve_review_target(str(proj))
    assert is_snapshot is True
    assert source_path == proj.resolve()
    assert snapshot.is_git_work_tree(review_path)

    gitrepo = tmp_path / "gitrepo"
    gitrepo.mkdir()
    (gitrepo / "f.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(gitrepo), "init", "-q"], check=True)
    r_path, r_source, r_snap = server.resolve_review_target(str(gitrepo))
    assert r_snap is False
    assert r_path == r_source == gitrepo.resolve()


def test_resolve_review_target_rejects_missing_path(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        server.resolve_review_target(str(tmp_path / "nope"))
    with pytest.raises(ValueError, match="Provide a repository path"):
        server.resolve_review_target("")


def test_register_repo_accepts_non_git_folder(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    proj = _folder(tmp_path)
    repo = server.register_repo({"path": str(proj), "name": "MyProj"})
    assert repo["is_snapshot"] is True
    assert repo["path"] == str(proj.resolve())
    assert repo["metadata"]["snapshot"] is True
    # Identity is stable across re-registration of the same source.
    again = server.register_repo({"path": str(proj)})
    assert again["id"] == repo["id"]


def test_create_review_run_forces_whole_tree_scope_for_snapshot(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    proj = _folder(tmp_path)
    run_id, repo_path, effective, command = server.create_review_run({"repoPath": str(proj)})
    meta = server.read_json(server.meta_path(run_id), {})
    assert meta["is_snapshot"] is True
    assert meta["source_path"] == str(proj.resolve())
    assert meta["mode"] == "refs"
    assert meta["refs"] == snapshot.SNAPSHOT_REFS
    assert meta["merge_base"] is False
    # The run operates on the snapshot, which is a real git repo.
    assert snapshot.is_git_work_tree(Path(meta["repo_path"]))
    # Every file is in scope (empty-tree diff).
    assert set(meta["changed_files"]) == {"app.py", "util.js"}
    assert snapshot.SNAPSHOT_REFS in " ".join(command)


def test_snapshot_preflight_is_lightweight_and_warns(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    proj = _folder(tmp_path)
    pf = server.preflight_review({"repoPath": str(proj)})
    assert pf["metadata"]["snapshot"] is True
    assert set(pf["changedFiles"]) == {"app.py", "util.js"}
    assert any("local snapshot" in w for w in pf["warnings"])
    # Preflight must not have built a snapshot copy yet.
    assert not (server.SNAPSHOTS_DIR).exists()
