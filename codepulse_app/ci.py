"""CI batch mode: run a CodePulse review synchronously and gate on it.

Usage (a GitHub Actions / GitLab CI step, or any shell):

    python -m codepulse_app.ci --repo . --what "$HEAD_SHA" --against origin/main \
        --fail-on block [--publish-pr 42]

Runs the exact server review pipeline (static + cross-file + LLM + verifier)
inline, prints the summary markdown to stdout, and exits:

    0  review completed and the gate is below --fail-on
    1  gate met --fail-on (block, or review when --fail-on review)
    2  the review itself failed to run

Publishing (``--publish-pr``) uses the same server-side tokens as the
dashboard (``GITHUB_TOKEN`` / ``GITLAB_TOKEN``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import publisher, server

GATE_ORDER = {"pass": 0, "review": 1, "block": 2}


def gate_breached(gate: str, fail_on: str) -> bool:
    if fail_on == "none":
        return False
    return GATE_ORDER.get(gate, 0) >= GATE_ORDER.get(fail_on, 2)


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {
        "repoPath": args.repo,
        "mode": args.mode,
    }
    if args.refs:
        payload["refs"] = args.refs
    if args.what:
        payload["what"] = args.what
    if args.against:
        payload["against"] = args.against
    if args.filters:
        payload["filters"] = args.filters
    if args.no_merge_base:
        payload["mergeBase"] = False
    if args.model:
        payload["model"] = args.model
    if args.ollama_base:
        payload["ollamaBase"] = args.ollama_base
    if args.timeout:
        payload["timeoutSeconds"] = args.timeout
    if args.verify_timeout:
        payload["verifyTimeoutSeconds"] = args.verify_timeout
    if args.no_verify:
        payload["verifyFindings"] = False
    if args.no_static:
        payload["staticAnalysis"] = False
    if args.no_cross_file:
        payload["crossFileAnalysis"] = False
    if args.no_reuse_verdicts:
        payload["reuseVerdicts"] = False
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m codepulse_app.ci",
        description="Run a CodePulse review synchronously (CI batch mode).",
    )
    parser.add_argument("--repo", required=True, help="Path to the git repository")
    parser.add_argument("--mode", default="refs", choices=["working", "refs", "all"])
    parser.add_argument("--refs", default="", help="Refs pair, e.g. main..feature")
    parser.add_argument("--what", default="", help="Commit/branch under review")
    parser.add_argument("--against", default="", help="Base ref, e.g. origin/main")
    parser.add_argument("--filters", default="", help="File glob filters")
    parser.add_argument("--no-merge-base", action="store_true")
    parser.add_argument("--model", default="", help="Ollama model (default: server default)")
    parser.add_argument("--ollama-base", default="", help="Ollama base URL")
    parser.add_argument("--timeout", type=int, default=0, help="Review timeout seconds")
    parser.add_argument("--verify-timeout", type=int, default=0)
    parser.add_argument("--no-verify", action="store_true", help="Skip the verifier pass")
    parser.add_argument("--no-static", action="store_true", help="Skip static analysis")
    parser.add_argument("--no-cross-file", action="store_true", help="Skip cross-file analysis")
    parser.add_argument("--no-reuse-verdicts", action="store_true")
    parser.add_argument(
        "--fail-on",
        default="block",
        choices=["block", "review", "none"],
        help="Exit 1 when the gate is at least this severe (default: block)",
    )
    parser.add_argument("--publish-pr", type=int, default=0, help="Post the review to PR/MR #N")
    parser.add_argument("--platform", default="", choices=["", "github", "gitlab"])
    parser.add_argument("--slug", default="", help="owner/repo override for publishing")
    parser.add_argument("--json", default="", help="Write a machine-readable result JSON here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(args)
    try:
        run_id, repo_path, payload, command = server.create_review_run(payload)
    except (ValueError, FileNotFoundError) as exc:
        print(f"codepulse: {exc}", file=sys.stderr)
        return 2

    print(f"codepulse: run {run_id} on {repo_path}", file=sys.stderr)
    server.run_review(run_id, repo_path, payload, command)

    meta = server.read_json(server.meta_path(run_id), {}) or {}
    stats = meta.get("stats") or {}
    report = server.read_json(server.report_path(run_id), None)
    gate = str(stats.get("gate") or "pass")

    result = {
        "run_id": run_id,
        "status": meta.get("status"),
        "gate": gate,
        "risk_score": stats.get("risk_score"),
        "total_issues": stats.get("total_issues"),
        "stats": stats,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")

    if meta.get("status") != "completed" or report is None:
        log_file = server.log_path(run_id)
        if log_file.exists():
            tail = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-20:]
            print("\n".join(tail), file=sys.stderr)
        print(f"codepulse: review {run_id} failed ({meta.get('error') or 'see log'})", file=sys.stderr)
        return 2

    print(publisher.build_summary_markdown(meta, report, stats))

    if args.publish_pr:
        publish_payload: dict[str, object] = {"pr": args.publish_pr, "dryRun": False}
        if args.platform:
            publish_payload["platform"] = args.platform
        if args.slug:
            publish_payload["repo"] = args.slug
        try:
            published = server.publish_run(run_id, publish_payload)
            print(f"codepulse: published to {published.get('target')}", file=sys.stderr)
        except (ValueError, FileNotFoundError) as exc:
            print(f"codepulse: publish failed: {exc}", file=sys.stderr)
            return 2

    if gate_breached(gate, args.fail_on):
        print(f"codepulse: gate '{gate}' meets --fail-on {args.fail_on}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
