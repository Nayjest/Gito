import fnmatch
import logging
from os import PathLike
from typing import Iterable
from pathlib import Path

import microcore as mc
from git import Repo
from unidiff import PatchSet, PatchedFile
from unidiff.constants import DEV_NULL

from .project_config import ProjectConfig
from .report_struct import Report
from .constants import JSON_REPORT_FILE_NAME


def is_binary_file(repo: Repo, file_path: str) -> bool:
    """
    Check if a file is binary by attempting to read it as text.
    Returns True if the file is binary, False otherwise.
    """
    try:
        # Attempt to read the file content from the repository tree
        content = repo.tree()[file_path].data_stream.read()
        # Try decoding as UTF-8; if it fails, it's likely binary
        content.decode("utf-8")
        return False
    except (UnicodeDecodeError, KeyError):
        return True
    except Exception as e:
        logging.warning(f"Error checking if file {file_path} is binary: {e}")
        return True  # Conservatively treat errors as binary to avoid issues


def get_diff(
    repo: Repo = None,
    what: str = None,
    against: str = None,
    use_merge_base: bool = True,
) -> PatchSet | list[PatchedFile]:
    repo = repo or Repo(".")
    if not against:
        pprint("Against available")
        against = repo.remotes.origin.refs.HEAD.reference.name  # origin/main
    else:
        pprint("Against not available")
    if not what:
        what = None  # working copy
    if use_merge_base:
        if what is None:
            try:
                current_ref = repo.active_branch.name
            except TypeError:
                # In detached HEAD state, use HEAD directly
                current_ref = "HEAD"
                logging.info(
                    "Detected detached HEAD state, using HEAD as current reference"
                )
        else:
            current_ref = what
        merge_base = repo.merge_base(current_ref or repo.active_branch.name, against)[0]
        against = merge_base.hexsha
        logging.info(
            f"Using merge base: {mc.ui.cyan(merge_base.hexsha[:8])} ({merge_base.summary})"
        )
    logging.info(
        f"Making diff: {mc.ui.green(what or 'INDEX')} vs {mc.ui.yellow(against)}"
    )
    diff_content = repo.git.diff(against, what)
    diff = PatchSet.from_string(diff_content)
    diff = PatchSet.from_string(diff_content)

    # Filter out binary files
    non_binary_diff = PatchSet([])
    for patched_file in diff:
        # Check if the file is binary using the source or target file path
        file_path = (
            patched_file.target_file
            if patched_file.target_file != DEV_NULL
            else patched_file.source_file
        )
        if file_path == DEV_NULL or is_binary_file(repo, file_path.lstrip("b/")):
            logging.info(f"Skipping binary file: {patched_file.path}")
            continue
        non_binary_diff.append(patched_file)
    return non_binary_diff


def filter_diff(
    patch_set: PatchSet | Iterable[PatchedFile], filters: str | list[str]
) -> PatchSet | Iterable[PatchedFile]:
    """
    Filter the diff files by the given fnmatch filters.
    """
    assert isinstance(filters, (list, str))
    if not isinstance(filters, list):
        filters = [f.strip() for f in filters.split(",") if f.strip()]
    if not filters:
        return patch_set
    files = [
        file
        for file in patch_set
        if any(fnmatch.fnmatch(file.path, pattern) for pattern in filters)
    ]
    return files


def file_lines(repo: Repo, file: str, max_tokens: int = None) -> str:
    text = repo.tree()[file].data_stream.read().decode()
    lines = [f"{i + 1}: {line}\n" for i, line in enumerate(text.splitlines())]
    if max_tokens:
        lines, removed_qty = mc.tokenizing.fit_to_token_size(lines, max_tokens)
        if removed_qty:
            lines.append(
                f"(!) DISPLAYING ONLY FIRST {len(lines)} LINES DUE TO LARGE FILE SIZE\n"
            )
    return "".join(lines)


def make_cr_summary(cfg: ProjectConfig, report: Report, diff):
    return (
        mc.prompt(
            cfg.summary_prompt,
            diff=mc.tokenizing.fit_to_token_size(diff, cfg.max_code_tokens)[0],
            issues=report.issues,
            **cfg.prompt_vars,
        ).to_llm()
        if cfg.summary_prompt
        else ""
    )


async def review(
    repo: Repo = None,
    what: str = None,
    against: str = None,
    filters: str | list[str] = "",
    use_merge_base: bool = True,
    out_folder: str | PathLike | None = None,
):
    cfg = ProjectConfig.load()
    repo = repo or Repo(".")
    out_folder = Path(out_folder or repo.working_tree_dir)
    diff = get_diff(
        repo=repo, what=what, against=against, use_merge_base=use_merge_base
    )
    diff = filter_diff(diff, filters)
    if not diff:
        logging.error("Nothing to review")
        return
    lines = {
        file_diff.path: (
            file_lines(
                repo,
                file_diff.path,
                cfg.max_code_tokens
                - mc.tokenizing.num_tokens_from_string(str(file_diff)),
            )
            if file_diff.target_file != DEV_NULL and not file_diff.is_added_file
            else ""
        )
        for file_diff in diff
    }
    responses = await mc.llm_parallel(
        [
            mc.prompt(
                cfg.prompt,
                input=file_diff,
                file_lines=lines[file_diff.path],
                **cfg.prompt_vars,
            )
            for file_diff in diff
        ],
        retries=cfg.retries,
        parse_json=True,
    )
    issues = {file.path: issues for file, issues in zip(diff, responses) if issues}
    for file, file_issues in issues.items():
        for issue in file_issues:
            for i in issue.get("affected_lines", []):
                if lines[file]:
                    f_lines = [""] + lines[file].splitlines()
                    i["affected_code"] = "\n".join(
                        f_lines[i["start_line"]: i["end_line"] + 1]
                    )
    exec(cfg.post_process, {"mc": mc, **locals()})
    out_folder.mkdir(parents=True, exist_ok=True)
    report = Report(issues=issues, number_of_processed_files=len(diff))
    report.summary = make_cr_summary(cfg, report, diff)
    report.save(file_name=out_folder / JSON_REPORT_FILE_NAME)
    report_text = report.render(cfg, Report.Format.MARKDOWN)
    print(mc.ui.yellow(report_text))
    text_report_path = out_folder / "code-review-report.md"
    text_report_path.write_text(report_text, encoding="utf-8")
