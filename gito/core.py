import fnmatch
import logging
from os import PathLike
from typing import Iterable
from pathlib import Path

import microcore as mc
from git import Repo
from gito.pipeline import Pipeline
from unidiff import PatchSet, PatchedFile
from unidiff.constants import DEV_NULL

from .project_config import ProjectConfig
from .report_struct import Report
from .constants import JSON_REPORT_FILE_NAME
from .utils import stream_to_cli


def review_subject_is_index(what):
    return not what or what == 'INDEX'


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
    except KeyError:
        try:
            fs_path = Path(repo.working_tree_dir) / file_path
            fs_path.read_text(encoding='utf-8')
            return False
        except FileNotFoundError:
            logging.error(f"File {file_path} not found in the repository.")
            return True
        except UnicodeDecodeError:
            return True
        except Exception as e:
            logging.error(f"Error reading file {file_path}: {e}")
            return True
    except UnicodeDecodeError:
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
        print("No 'against'")
        # 'origin/main', 'origin/master', etc
        against = repo.remotes.origin.refs.HEAD.reference.name
    else:
        print("'against' provided")
    if review_subject_is_index(what):
        what = None  # working copy
    if use_merge_base:
        if review_subject_is_index(what):
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

    # Filter out binary files
    non_binary_diff = PatchSet([])
    for patched_file in diff:
        # Check if the file is binary using the source or target file path
        file_path = (
            patched_file.target_file
            if patched_file.target_file != DEV_NULL
            else patched_file.source_file
        )
        if file_path == DEV_NULL:
            continue
        if is_binary_file(repo, file_path.lstrip("b/")):
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


def file_lines(repo: Repo, file: str, max_tokens: int = None, use_local_files: bool = False) -> str:
    if use_local_files:
        file_path = Path(repo.working_tree_dir) / file
        try:
            text = file_path.read_text(encoding='utf-8')
        except (FileNotFoundError, UnicodeDecodeError) as e:
            logging.warning(f"Could not read file {file} from working directory: {e}")
            text = repo.tree()[file].data_stream.read().decode('utf-8')
    else:
        # Read from HEAD (committed version)
        text = repo.tree()[file].data_stream.read().decode('utf-8')

    lines = [f"{i + 1}: {line}\n" for i, line in enumerate(text.splitlines())]
    if max_tokens:
        lines, removed_qty = mc.tokenizing.fit_to_token_size(lines, max_tokens)
        if removed_qty:
            lines.append(
                f"(!) DISPLAYING ONLY FIRST {len(lines)} LINES DUE TO LARGE FILE SIZE\n"
            )
    return "".join(lines)


def make_cr_summary(config: ProjectConfig, report: Report, diff, **kwargs) -> str:
    return (
        mc.prompt(
            config.summary_prompt,
            diff=mc.tokenizing.fit_to_token_size(diff, config.max_code_tokens)[0],
            issues=report.issues,
            **config.prompt_vars,
            **kwargs,
        ).to_llm()
        if config.summary_prompt
        else ""
    )


class NoChangesInContextError(Exception):
    """
    Exception raised when there are no changes in the context to review /answer questions.
    """


def _prepare(
    repo: Repo = None,
    what: str = None,
    against: str = None,
    filters: str | list[str] = "",
    use_merge_base: bool = True,
):
    repo = repo or Repo(".")
    cfg = ProjectConfig.load_for_repo(repo)
    diff = get_diff(
        repo=repo, what=what, against=against, use_merge_base=use_merge_base
    )
    diff = filter_diff(diff, filters)
    if not diff:
        raise NoChangesInContextError()
    lines = {
        file_diff.path: (
            file_lines(
                repo,
                file_diff.path,
                cfg.max_code_tokens
                - mc.tokenizing.num_tokens_from_string(str(file_diff)),
                use_local_files=review_subject_is_index(what)
            )
            if file_diff.target_file != DEV_NULL and not file_diff.is_added_file
            else ""
        )
        for file_diff in diff
    }
    return repo, cfg, diff, lines


async def review(
    repo: Repo = None,
    what: str = None,
    against: str = None,
    filters: str | list[str] = "",
    use_merge_base: bool = True,
    out_folder: str | PathLike | None = None,
):
    try:
        repo, cfg, diff, lines = _prepare(
            repo=repo, what=what, against=against, filters=filters, use_merge_base=use_merge_base
        )
    except NoChangesInContextError:
        logging.error("No changes to review")
        return
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
    out_folder = Path(out_folder or repo.working_tree_dir)
    out_folder.mkdir(parents=True, exist_ok=True)
    report = Report(issues=issues, number_of_processed_files=len(diff))
    ctx = dict(
        report=report,
        config=cfg,
        diff=diff,
        repo=repo,
        pipeline_out={},
    )
    if cfg.pipeline_steps:
        pipe = Pipeline(
            ctx=ctx,
            steps=cfg.pipeline_steps
        )
        pipe.run()
    else:
        logging.info("No pipeline steps defined, skipping pipeline execution")

    report.summary = make_cr_summary(**ctx)
    report.save(file_name=out_folder / JSON_REPORT_FILE_NAME)
    report_text = report.render(cfg, Report.Format.MARKDOWN)
    text_report_path = out_folder / "code-review-report.md"
    text_report_path.write_text(report_text, encoding="utf-8")
    report.to_cli()


def answer(
    question: str,
    repo: Repo = None,
    what: str = None,
    against: str = None,
    filters: str | list[str] = "",
    use_merge_base: bool = True,
) -> str | None:
    try:
        repo, cfg, diff, lines = _prepare(
            repo=repo, what=what, against=against, filters=filters, use_merge_base=use_merge_base
        )
    except NoChangesInContextError:
        logging.error("No changes to review")
        return
    response = mc.llm(mc.prompt(
        cfg.answer_prompt,
        question=question,
        diff=diff,
        all_file_lines=lines,
        **cfg.prompt_vars,
        callback=stream_to_cli
    ))
    return response
