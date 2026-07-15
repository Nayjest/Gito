"""LLM generation subprocess for Code Doctor: unit tests and PR drafts.

Invoked as ``python -m code_doctor_app.generator --kind tests|pr …`` with the
same ``LLM_*`` environment contract Gito uses, so any OpenAI-compatible or
provider-specific backend works. Writes structured artifacts into the run
directory so the dashboard can render, export, and audit them:

- ``generated-tests.json`` plus runnable files under ``generated-tests/``
- ``pr-draft.json`` plus a ready-to-paste ``code-review-report.md``

Exit codes: 0 success, 3 nothing to generate, 4 generation/parse failure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import microcore as mc

from . import static_analysis

MAX_DIFF_CHARS = 40_000
MAX_FILE_CHARS = 12_000
MAX_CONTEXT_CHARS = 70_000
MAX_GENERATED_FILES = 12
MAX_ATTEMPTS = 3  # small local models regularly emit slightly broken JSON

TESTS_INSTRUCTIONS = """\
You are Code Doctor, a senior engineer writing unit tests for a junior developer's change.
Answer with the JSON immediately; do not include reasoning or commentary.

Write focused, runnable unit tests for the CHANGED code below.
Rules:
- Use pytest for Python and vitest for TypeScript/JavaScript.
- Cover the changed behavior only: happy path, failure path, and one edge case per changed function.
- Mock external dependencies (network, database, filesystem, subprocesses, LLM providers).
- Import the code under test through its real module path derived from the file path.
- Keep each file self-contained; no fixtures that live outside the generated file.
- Name test functions after the behavior they verify.

Respond with ONLY valid JSON (no prose, no markdown fences) matching:
{"files": [{"path": "tests/…", "framework": "pytest|vitest", "content": "<full file>",
"covers": ["<source file>:<function>"], "rationale": "<one sentence>"}],
"notes": "<caveats or empty string>"}
"""

PR_INSTRUCTIONS = """\
You are Code Doctor, a senior engineer drafting a pull request for the change below.
Answer with the JSON immediately; do not include reasoning or commentary.

Respond with ONLY valid JSON (no prose, no markdown fences) matching:
{"title": "<imperative, max 72 chars>",
"body_markdown": "<sections: ## Summary, ## Changes, ## Risk, ## Test Plan>",
"labels": ["<label>"], "checklist": ["<reviewer checklist item>"]}

Guidance:
- Summary explains the why in two or three sentences.
- Changes is a bulleted list grouped by area.
- Risk names the riskiest part of the diff and how a reviewer verifies it.
- Test Plan lists concrete commands or scenarios.
- Describe only what is actually in the diff; never invent changes.
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_llm_json(text: str) -> dict:
    """Extract the outermost JSON object from an LLM response.

    Tolerates markdown fences, reasoning-model <think> blocks, and stray
    prose around the JSON body.
    """
    cleaned = re.sub(r"<think>.*?(</think>|$)", "", str(text), flags=re.DOTALL)
    cleaned = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", cleaned.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in model response.")
    return json.loads(cleaned[start : end + 1])


def safe_artifact_path(base_dir: Path, relative: str) -> Path:
    """Resolve a model-proposed file path strictly inside ``base_dir``."""
    candidate = Path(str(relative).strip())
    if candidate.is_absolute() or not candidate.parts or any(
        part in ("..", "") for part in candidate.parts
    ):
        raise ValueError(f"Unsafe generated file path: {relative!r}")
    resolved = (base_dir / candidate).resolve()
    base = base_dir.resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError(f"Unsafe generated file path: {relative!r}")
    return resolved


def gather_context(repo: Path, args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    diff = static_analysis.collect_diff(
        repo,
        mode=args.mode,
        refs=args.refs,
        what=args.what,
        against=args.against,
        use_merge_base=args.merge_base,
    )[:MAX_DIFF_CHARS]
    files = static_analysis.collect_changed_files(
        repo,
        mode=args.mode,
        refs=args.refs,
        what=args.what,
        against=args.against,
        use_merge_base=args.merge_base,
        filters=args.filters,
    )
    contents: dict[str, str] = {}
    budget = MAX_CONTEXT_CHARS - len(diff)
    for file in files:
        target = repo / file
        try:
            if not target.is_file():
                continue
            text = target.read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_CHARS]
        except OSError:
            continue
        if len(text) > budget:
            break
        contents[file] = text
        budget -= len(text)
    return diff, contents


def build_tests_prompt(diff: str, contents: dict[str, str]) -> str:
    files_block = "\n\n".join(
        f"----- {path} -----\n{text}" for path, text in contents.items()
    )
    return (
        f"{TESTS_INSTRUCTIONS}\n"
        f"----CHANGED FILES DIFF----\n{diff}\n\n"
        f"----FULL CONTENT OF CHANGED FILES----\n{files_block or '(no readable source files)'}\n"
    )


def build_pr_prompt(diff: str) -> str:
    return f"{PR_INSTRUCTIONS}\n----DIFF----\n{diff}\n"


def tests_markdown(result: dict) -> str:
    lines = ["# Code Doctor — Generated Unit Tests", ""]
    for item in result.get("files", []):
        lines += [
            f"## `{item.get('path', '')}` ({item.get('framework', '')})",
            "",
            item.get("rationale", ""),
            "",
            "```" + ("python" if item.get("framework") == "pytest" else "ts"),
            item.get("content", ""),
            "```",
            "",
        ]
    if result.get("notes"):
        lines += ["## Notes", "", str(result["notes"]), ""]
    return "\n".join(lines)


def pr_markdown(result: dict) -> str:
    checklist = "\n".join(f"- [ ] {item}" for item in result.get("checklist", []))
    labels = ", ".join(f"`{label}`" for label in result.get("labels", []))
    parts = [f"# {result.get('title', 'Pull request')}", "", result.get("body_markdown", "")]
    if labels:
        parts += ["", f"**Suggested labels:** {labels}"]
    if checklist:
        parts += ["", "## Reviewer Checklist", "", checklist]
    return "\n".join(parts) + "\n"


def normalize_tests(result: dict) -> dict:
    files = []
    for item in (result.get("files") or [])[:MAX_GENERATED_FILES]:
        if not isinstance(item, dict) or not str(item.get("content") or "").strip():
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        files.append(
            {
                "path": path,
                "framework": str(item.get("framework") or "pytest"),
                "content": str(item.get("content")),
                "covers": [str(entry) for entry in item.get("covers") or []],
                "rationale": str(item.get("rationale") or ""),
            }
        )
    if not files:
        raise ValueError("Model returned no usable test files.")
    return {"files": files, "notes": str(result.get("notes") or "")}


def normalize_pr(result: dict) -> dict:
    title = str(result.get("title") or "").strip()
    body = str(result.get("body_markdown") or "").strip()
    if not title or not body:
        raise ValueError("Model returned an incomplete PR draft.")
    return {
        "title": title[:120],
        "body_markdown": body,
        "labels": [str(label) for label in result.get("labels") or []][:8],
        "checklist": [str(item) for item in result.get("checklist") or []][:12],
    }


def write_tests_artifacts(out_dir: Path, payload: dict) -> None:
    artifact = {"kind": "tests", "created_at": utc_now(), **payload}
    (out_dir / "generated-tests.json").write_text(
        json.dumps(artifact, indent=2), encoding="utf-8"
    )
    files_dir = out_dir / "generated-tests"
    for item in payload["files"]:
        try:
            target = safe_artifact_path(files_dir, item["path"])
        except ValueError as exc:
            print(f"Skipping unsafe path from model: {exc}", file=sys.stderr)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
    (out_dir / "code-review-report.md").write_text(tests_markdown(payload), encoding="utf-8")


def write_pr_artifacts(out_dir: Path, payload: dict) -> None:
    artifact = {"kind": "pr", "created_at": utc_now(), **payload}
    (out_dir / "pr-draft.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    (out_dir / "code-review-report.md").write_text(pr_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Code Doctor LLM generator")
    parser.add_argument("--kind", choices=("tests", "pr"), required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", default="working")
    parser.add_argument("--refs", default="")
    parser.add_argument("--what", default="")
    parser.add_argument("--against", default="")
    parser.add_argument("--filters", default="")
    parser.add_argument("--no-merge-base", dest="merge_base", action="store_false")
    args = parser.parse_args()

    repo = Path(args.repo)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    diff, contents = gather_context(repo, args)
    if not diff.strip():
        print("No changes found for the selected scope; nothing to generate.")
        return 3

    mc.configure(USE_LOGGING=True, EMBEDDING_DB_TYPE=mc.EmbeddingDbType.NONE)
    prompt = build_tests_prompt(diff, contents) if args.kind == "tests" else build_pr_prompt(diff)
    print(f"Code Doctor generator: kind={args.kind}, files={len(contents)}, diff_chars={len(diff)}")

    payload = None
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempt_prompt = prompt
        if last_error is not None:
            attempt_prompt += (
                f"\n\nYour previous response was rejected: {last_error}. "
                "Respond again with ONLY valid JSON. Escape every double quote and "
                "newline inside string values."
            )
        try:
            response = mc.llm(attempt_prompt)
            parsed = parse_llm_json(response)
            payload = normalize_tests(parsed) if args.kind == "tests" else normalize_pr(parsed)
            break
        except Exception as exc:  # noqa: BLE001 - subprocess boundary, retried then reported
            last_error = exc
            print(
                f"Attempt {attempt}/{MAX_ATTEMPTS} failed: [{type(exc).__name__}] {exc}",
                file=sys.stderr,
            )

    if payload is None:
        print(f"Generation failed after {MAX_ATTEMPTS} attempts: {last_error}", file=sys.stderr)
        return 4

    try:
        if args.kind == "tests":
            write_tests_artifacts(out_dir, payload)
        else:
            write_pr_artifacts(out_dir, payload)
    except Exception as exc:  # noqa: BLE001 - subprocess boundary, reported via log + exit code
        print(f"Failed to write artifacts: [{type(exc).__name__}] {exc}", file=sys.stderr)
        return 4

    print("Generation completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
