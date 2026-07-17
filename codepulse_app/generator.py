"""LLM generation subprocess for CodePulse: unit tests and PR drafts.

Invoked as ``python -m codepulse_app.generator --kind tests|pr …`` with the
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
You are CodePulse, a senior engineer writing unit tests for a junior developer's change.
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

VERIFY_INSTRUCTIONS = """\
You are CodePulse's verification pass: a skeptical senior engineer re-checking a reviewer's draft findings.
Answer with the JSON immediately; do not include reasoning or commentary.

For every finding, judge it strictly against the DIFF and file content provided:
- "confirmed": the evidence clearly supports the finding as described.
- "rejected": the finding is factually wrong, describes code that is not in the change,
  is already handled elsewhere in the shown code, or is speculative style preference.
- "uncertain": plausible, but the provided context cannot prove it either way.

Be strict: a finding that names the wrong line, wrong behavior, or invents an API is "rejected".
Do not invent new findings.

Respond with ONLY valid JSON (no prose, no markdown fences) matching:
{"verdicts": [{"id": <finding id>, "verdict": "confirmed|rejected|uncertain", "reason": "<one sentence>"}]}
"""

MAX_VERIFY_FINDINGS = 40

PR_INSTRUCTIONS = """\
You are CodePulse, a senior engineer drafting a pull request for the change below.
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


def issues_for_verification(report: dict, skip_ids: tuple | list = ()) -> list[dict]:
    """Flatten LLM findings for the verifier; deterministic static findings are
    rule-based evidence and are never second-guessed by a model. ``skip_ids``
    excludes findings whose verdicts were carried over from a previous run."""
    skipped = {str(item) for item in skip_ids}
    findings = []
    for file, issues in (report.get("issues") or {}).items():
        for issue in issues:
            if issue.get("source") == "static":
                continue
            if str(issue.get("id")) in skipped:
                continue
            findings.append(
                {
                    "id": issue.get("id"),
                    "file": issue.get("file") or file,
                    "title": issue.get("title"),
                    "details": issue.get("details"),
                    "severity": issue.get("severity"),
                    "affected_lines": [
                        {k: block.get(k) for k in ("start_line", "end_line", "affected_code")}
                        for block in issue.get("affected_lines") or []
                    ],
                }
            )
    return findings[:MAX_VERIFY_FINDINGS]


def cross_file_context_block(repo: Path, context_pack_path: str) -> str:
    """Dependent-file excerpts from the run's context pack, so the verifier can
    judge cross-file claims (changed signatures, removed symbols) beyond the diff."""
    if not context_pack_path:
        return ""
    try:
        pack = json.loads(Path(context_pack_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    sections: list[str] = []
    budget = MAX_CONTEXT_CHARS // 3
    seen: set[str] = set()
    for file, info in (pack.get("files") or {}).items():
        dependents = info.get("dependents") or []
        symbols = info.get("symbols") or {}
        if symbols.get("removed") or symbols.get("signature_changed"):
            sections.append(
                f"{file}: removed={symbols.get('removed')} "
                f"signature_changed={[c.get('name') for c in symbols.get('signature_changed') or []]} "
                f"dependents={dependents}"
            )
        for dependent in dependents[:3]:
            if dependent in seen or budget <= 0:
                continue
            seen.add(dependent)
            try:
                text = (repo / dependent).read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                continue
            snippet = text[: min(len(text), budget)]
            budget -= len(snippet)
            sections.append(f"----- dependent: {dependent} -----\n{snippet}")
    if not sections:
        return ""
    return "----CROSS-FILE CONTEXT (files that import the changed files)----\n" + "\n\n".join(sections) + "\n\n"


def build_verify_prompt(
    diff: str, contents: dict[str, str], findings: list[dict], cross_context: str = ""
) -> str:
    files_block = "\n\n".join(
        f"----- {path} -----\n{text}" for path, text in contents.items()
    )
    return (
        f"{VERIFY_INSTRUCTIONS}\n"
        f"----FINDINGS TO VERIFY----\n{json.dumps(findings, indent=1)}\n\n"
        f"----DIFF----\n{diff}\n\n"
        f"{cross_context}"
        f"----FULL CONTENT OF CHANGED FILES----\n{files_block or '(none)'}\n"
    )


def normalize_verify(result: dict, findings: list[dict]) -> dict:
    known_ids = {finding["id"] for finding in findings}
    verdicts = []
    for item in result.get("verdicts") or []:
        if not isinstance(item, dict) or item.get("id") not in known_ids:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in {"confirmed", "rejected", "uncertain"}:
            continue
        verdicts.append(
            {
                "id": item["id"],
                "verdict": verdict,
                "reason": str(item.get("reason") or "").strip()[:400],
            }
        )
    if not verdicts:
        raise ValueError("Model returned no usable verdicts.")
    return {"verdicts": verdicts}


def tests_markdown(result: dict) -> str:
    lines = ["# CodePulse — Generated Unit Tests", ""]
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
    parser = argparse.ArgumentParser(description="CodePulse LLM generator")
    parser.add_argument("--kind", choices=("tests", "pr", "verify"), required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", default="", help="Review report JSON (verify kind)")
    parser.add_argument("--context", default="", help="Cross-file context pack JSON (verify kind)")
    parser.add_argument(
        "--skip-ids",
        default="",
        help="Comma-separated finding ids with verdicts carried from a prior run (verify kind)",
    )
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

    findings: list[dict] = []
    if args.kind == "verify":
        report = json.loads(Path(args.report).read_text(encoding="utf-8")) if args.report else {}
        skip_ids = [item.strip() for item in args.skip_ids.split(",") if item.strip()]
        findings = issues_for_verification(report, skip_ids)
        if not findings:
            print("No LLM findings to verify.")
            return 3

    mc.configure(USE_LOGGING=True, EMBEDDING_DB_TYPE=mc.EmbeddingDbType.NONE)
    if args.kind == "tests":
        prompt = build_tests_prompt(diff, contents)
    elif args.kind == "verify":
        cross_context = cross_file_context_block(repo, args.context)
        prompt = build_verify_prompt(diff, contents, findings, cross_context)
    else:
        prompt = build_pr_prompt(diff)
    print(f"CodePulse generator: kind={args.kind}, files={len(contents)}, diff_chars={len(diff)}")

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
            if args.kind == "tests":
                payload = normalize_tests(parsed)
            elif args.kind == "verify":
                payload = normalize_verify(parsed, findings)
            else:
                payload = normalize_pr(parsed)
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
        elif args.kind == "verify":
            artifact = {"kind": "verify", "created_at": utc_now(), **payload}
            (out_dir / "verification.json").write_text(
                json.dumps(artifact, indent=2), encoding="utf-8"
            )
        else:
            write_pr_artifacts(out_dir, payload)
    except Exception as exc:  # noqa: BLE001 - subprocess boundary, reported via log + exit code
        print(f"Failed to write artifacts: [{type(exc).__name__}] {exc}", file=sys.stderr)
        return 4

    print("Generation completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
