import fnmatch
import logging
from os import PathLike
from typing import Iterable
from pathlib import Path
from functools import partial

import microcore as mc
from microcore import ui
from git import Repo, Commit
from git.exc import GitCommandError
from unidiff import PatchSet, PatchedFile
from unidiff.constants import DEV_NULL

from .context import Context
from .project_config import ProjectConfig
from .report_struct import Report
from .constants import JSON_REPORT_FILE_NAME
from .utils import make_streaming_function
from .pipeline import Pipeline
from .env import Env


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


def commit_in_branch(repo: Repo, commit: Commit, target_branch: str) -> bool:
    try:
        # exit code 0 if commit is ancestor of branch
        repo.git.merge_base('--is-ancestor', commit.hexsha, target_branch)
        return True
    except GitCommandError:
        pass
    return False


def get_diff(
    repo: Repo = None,
    what: str = None,
    against: str = None,
    use_merge_base: bool = True,
) -> PatchSet | list[PatchedFile]:
    repo = repo or Repo(".")
    if not against:
        # 'origin/main', 'origin/master', etc
        against = repo.remotes.origin.refs.HEAD.reference.name
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
        logging.info(
            f"Merge base({ui.green(current_ref)},{ui.yellow(against)})"
            f" --> {ui.cyan(merge_base.hexsha)}"
        )
        # if branch is already an ancestor of "against", merge_base == branch ⇒ it’s been merged
        if merge_base.hexsha == repo.commit(current_ref or repo.active_branch.name).hexsha:
            # @todo: check case: reviewing working copy index in main branch #103
            logging.info(
                f"Branch is already merged. ({ui.green(current_ref)} vs {ui.yellow(against)})"
            )
            merge_sha = repo.git.log(
                '--merges',
                '--ancestry-path',
                f'{current_ref}..{against}',
                '-n',
                '1',
                '--pretty=format:%H'
            ).strip()
            if merge_sha:
                logging.info(f"Merge commit is {ui.cyan(merge_sha)}")
                merge_commit = repo.commit(merge_sha)

                other_merge_parent = None
                for parent in merge_commit.parents:
                    logging.info(f"Checking merge parent: {parent.hexsha[:8]}")
                    if parent.hexsha == merge_base.hexsha:
                        logging.info(f"merge parent is {ui.cyan(parent.hexsha[:8])}, skipping")
                        continue
                    if not commit_in_branch(repo, parent, against):
                        logging.warning(f"merge parent is not in {against}, skipping")
                        continue
                    logging.info(f"Found other merge parent: {ui.cyan(parent.hexsha[:8])}")
                    other_merge_parent = parent
                    break
                if other_merge_parent:
                    first_common_ancestor = repo.merge_base(other_merge_parent, merge_base)[0]
                    # for gito remote (feature_branch vs origin/main)
                    # the same merge base appears in first_common_ancestor again
                    if first_common_ancestor.hexsha == merge_base.hexsha:
                        if merge_base.parents:
                            first_common_ancestor = repo.merge_base(
                                other_merge_parent, merge_base.parents[0]
                            )[0]
                        else:
                            logging.error(
                                "merge_base has no parents, "
                                "using merge_base as first_common_ancestor"
                            )
                    logging.info(
                        f"{what} will be compared to "
                        f"first common ancestor of {what} and {against}: "
                        f"{ui.cyan(first_common_ancestor.hexsha[:8])}"
                    )
                    against = first_common_ancestor.hexsha
                else:
                    logging.error(f"Can't find other merge parent for {merge_sha}")
            else:
                logging.error(
                    f"No merge‐commit found for {current_ref!r}→{against!r}; "
                    "falling back to merge‐base diff"
                )
        else:
            # normal case: branch not yet merged
            against = merge_base.hexsha
            logging.info(
                f"Using merge base: {ui.cyan(merge_base.hexsha[:8])} ({merge_base.summary})"
            )
    logging.info(
        f"Making diff: {ui.green(what or 'INDEX')} vs {ui.yellow(against)}"
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


def make_cr_summary(ctx: Context, **kwargs) -> str:
    return (
        mc.prompt(
            ctx.config.summary_prompt,
            diff=mc.tokenizing.fit_to_token_size(ctx.diff, ctx.config.max_code_tokens)[0],
            issues=ctx.report.issues,
            pipeline_out=ctx.pipeline_out,
            **ctx.config.prompt_vars,
            **kwargs,
        ).to_llm()
        if ctx.config.summary_prompt
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


def get_affected_code_block(repo: Repo, file: str, start_line: int, end_line: int) -> str | None:
    if not start_line or not end_line:
        return None
    try:
        if isinstance(start_line, str):
            start_line = int(start_line)
        if isinstance(end_line, str):
            end_line = int(end_line)
        lines = file_lines(repo, file, max_tokens=None, use_local_files=True)
        if lines:
            lines = [""] + lines.splitlines()
            return "\n".join(
                lines[start_line: end_line + 1]
            )
    except Exception as e:
        logging.error(
            f"Error getting affected code block for {file} from {start_line} to {end_line}: {e}"
        )
    return None


def provide_affected_code_blocks(issues: dict, repo: Repo):
    for file, file_issues in issues.items():
        for issue in file_issues:
            for i in issue.get("affected_lines", []):
                file_name = i.get("file", issue.get("file", file))
                if block := get_affected_code_block(
                    repo,
                    file_name,
                    i.get("start_line"),
                    i.get("end_line")
                ):
                    i["affected_code"] = block


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
    provide_affected_code_blocks(issues, repo)
    exec(cfg.post_process, {"mc": mc, **locals()})
    out_folder = Path(out_folder or repo.working_tree_dir)
    out_folder.mkdir(parents=True, exist_ok=True)
    report = Report(issues=issues, number_of_processed_files=len(diff))
    ctx = Context(
        report=report,
        config=cfg,
        diff=diff,
        repo=repo,
    )
    if cfg.pipeline_steps:
        pipe = Pipeline(
            ctx=ctx,
            steps=cfg.pipeline_steps
        )
        pipe.run()
    else:
        logging.info("No pipeline steps defined, skipping pipeline execution")

    report.summary = make_cr_summary(ctx)
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
    use_pipeline: bool = True,
    prompt_file: str = None,
) -> str | None:
    try:
        repo, config, diff, lines = _prepare(
            repo=repo, what=what, against=against, filters=filters, use_merge_base=use_merge_base
        )
    except NoChangesInContextError:
        logging.error("No changes to review")
        return

    ctx = Context(
        repo=repo,
        diff=diff,
        config=config,
        report=Report()
    )
    if use_pipeline:
        pipe = Pipeline(
            ctx=ctx,
            steps=config.pipeline_steps
        )
        pipe.run()
    if prompt_file:
        prompt_func = partial(mc.tpl, prompt_file)
    else:
        prompt_func = partial(mc.prompt, config.answer_prompt)
    response = mc.llm(
        prompt_func(
            question=question,
            diff=diff,
            all_file_lines=lines,
            pipeline_out=ctx.pipeline_out,
            **config.prompt_vars,
        ),
        callback=make_streaming_function() if Env.verbosity == 0 else None,
    )
    return response
