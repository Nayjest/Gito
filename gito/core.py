"""
Gito core business logic.
"""
import os
import fnmatch
import logging
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
from .report_struct import Report, RawIssue
from .constants import JSON_REPORT_FILE_NAME, REFS_VALUE_ALL
from .utils import make_streaming_function
from .pipeline import Pipeline
from .env import Env
from .gh_api import gh_api


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


def get_base_branch(repo: Repo, pr: int | str = None):
    if os.getenv('GITHUB_ACTIONS'):

        # triggered from PR
        if base_ref := os.getenv('GITHUB_BASE_REF'):
            logging.info(f"Using GITHUB_BASE_REF:{base_ref} as base branch")
            return f'origin/{base_ref}'
        logging.info("GITHUB_BASE_REF is not available...")
        if pr:
            api = gh_api(repo=repo)
            pr_obj = api.pulls.get(pr)
            logging.info(
                f"Using 'origin/{pr_obj.base.ref}' as base branch "
                f"(received via GH API for PR#{pr})"
            )
            return f'origin/{pr_obj.base.ref}'

    try:
        logging.info(
            "Trying to resolve base branch from repo.remotes.origin.refs.HEAD.reference.name..."
        )
        # 'origin/main', 'origin/master', etc
        # Stopped working in github actions since 07/2025
        return repo.remotes.origin.refs.HEAD.reference.name
    except AttributeError:
        try:
            logging.info(
                "Checking if repo has 'main' or 'master' branches to use as --against branch..."
            )
            remote_refs = repo.remotes.origin.refs
            for branch_name in ['main', 'master']:
                if hasattr(remote_refs, branch_name):
                    return f'origin/{branch_name}'
        except Exception:
            pass

        logging.error("Could not determine default branch from remote refs.")
        raise ValueError("No default branch found in the repository.")


def get_diff(
    repo: Repo = None,
    what: str = None,
    against: str = None,
    use_merge_base: bool = True,
    pr: str | int = None
) -> PatchSet | list[PatchedFile]:
    repo = repo or Repo(".")
    if what == REFS_VALUE_ALL:
        what = get_base_branch(repo, pr=pr)
        # Git's canonical empty tree hash
        against = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        use_merge_base = False
    if not against:
        # 'origin/main', 'origin/master', etc
        against = get_base_branch(repo, pr=pr)
    if review_subject_is_index(what):
        what = None  # working copy
    if use_merge_base:
        try:
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
                    logging.warning(
                        f"No merge‐commit found for {current_ref!r}→{against!r}; "
                        "falling back to merge‐base diff"
                    )
            else:
                # normal case: branch not yet merged
                against = merge_base.hexsha
                logging.info(
                    f"Using merge base: {ui.cyan(merge_base.hexsha[:8])} ({merge_base.summary})"
                )
        except Exception as e:
            logging.error(f"Error finding merge base: {e}")
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
        if is_binary_file(repo, file_path.removeprefix("b/")):
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


def read_file(repo: Repo, file: str, use_local_files: bool = False) -> str:
    if use_local_files:
        file_path = Path(repo.working_tree_dir) / file
        try:
            return file_path.read_text(encoding='utf-8')
        except (FileNotFoundError, UnicodeDecodeError) as e:
            logging.warning(f"Could not read file {file} from working directory: {e}")

    # Read from HEAD (committed version)
    return repo.tree()[file].data_stream.read().decode('utf-8')


def file_lines(repo: Repo, file: str, max_tokens: int = None, use_local_files: bool = False) -> str:
    text = read_file(repo=repo, file=file, use_local_files=use_local_files)
    lines = [f"{i + 1}: {line}\n" for i, line in enumerate(text.splitlines())]
    if max_tokens:
        lines, removed_qty = mc.tokenizing.fit_to_token_size(lines, max_tokens)
        if removed_qty:
            lines.append(
                f"(!) DISPLAYING ONLY FIRST {len(lines)} LINES DUE TO LARGE FILE SIZE\n"
            )
    return "".join(lines)


def read_files(repo: Repo, files: list[str], max_tokens: int = None) -> dict:
    out = dict()
    total_tokens = 0
    for file in files:
        content = read_file(repo=repo, file=file, use_local_files=True)
        total_tokens += mc.tokenizing.num_tokens_from_string(file)
        total_tokens += mc.tokenizing.num_tokens_from_string(content)
        if max_tokens and total_tokens > max_tokens:
            logging.warning(
                f"Skipping file {file} due to exceeding max_tokens limit ({max_tokens})"
            )
            continue
        out[file] = content
    return out


def make_cr_summary(ctx: Context, **kwargs) -> str:
    return (
        mc.prompt(
            ctx.config.summary_prompt,
            diff=mc.tokenizing.fit_to_token_size(ctx.diff, ctx.config.max_code_tokens)[0],
            issues=ctx.report.issues,
            pipeline_out=ctx.pipeline_out,
            env=Env,
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
    pr: str | int = None,
):
    repo = repo or Repo(".")
    cfg = ProjectConfig.load_for_repo(repo)
    diff = get_diff(
        repo=repo, what=what, against=against, use_merge_base=use_merge_base, pr=pr,
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
                use_local_files=review_subject_is_index(what) or what == REFS_VALUE_ALL
            )
            if file_diff.target_file != DEV_NULL or what == REFS_VALUE_ALL
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


def _llm_response_validator(parsed_response: list[dict]):
    """
    Validate that the LLM response is a list of dicts that can be converted to RawIssue.
    """
    if not isinstance(parsed_response, list):
        raise ValueError("Response is not a list")
    for item in parsed_response:
        if not isinstance(item, dict):
            raise ValueError("Response item is not a dict")
        RawIssue(**item)
    return True


async def review(
    repo: Repo = None,
    what: str = None,
    against: str = None,
    filters: str | list[str] = "",
    use_merge_base: bool = True,
    out_folder: str | os.PathLike | None = None,
    pr: str | int = None
):
    """
    Conducts a code review.
    Prints the review report to the console and saves it to a file.
    """
    reviewing_all = what == REFS_VALUE_ALL
    try:
        repo, cfg, diff, lines = _prepare(
            repo=repo,
            what=what,
            against=against,
            filters=filters,
            use_merge_base=use_merge_base,
            pr=pr,
        )
    except NoChangesInContextError:
        logging.error("No changes to review")
        return

    def input_is_diff(file_diff: PatchedFile) -> bool:
        """
        In case of reviewing all changes, or added files,
        we provide full file content as input.
        Otherwise, we provide the diff and additional file lines separately.
        """
        return not reviewing_all and not file_diff.is_added_file

    responses = await mc.llm_parallel(
        [
            mc.prompt(
                cfg.prompt,
                input=(
                    file_diff if input_is_diff(file_diff)
                    else str(file_diff.path) + ":\n" + lines[file_diff.path]
                ),
                file_lines=lines[file_diff.path] if input_is_diff(file_diff) else None,
                **cfg.prompt_vars,
            )
            for file_diff in diff
        ],
        retries=cfg.retries,
        parse_json={"validator": _llm_response_validator},
    )
    issues = {file.path: issues for file, issues in zip(diff, responses) if issues}
    provide_affected_code_blocks(issues, repo)
    exec(cfg.post_process, {"mc": mc, **locals()})
    out_folder = Path(out_folder or repo.working_tree_dir)
    out_folder.mkdir(parents=True, exist_ok=True)
    report = Report(number_of_processed_files=len(diff))
    report.register_issues(issues)
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
    pr: str | int = None,
    aux_files: list[str] = None,
) -> str | None:
    """
    Answers a question about the code changes.
    Returns the LLM response as a string.
    """
    try:
        repo, config, diff, lines = _prepare(
            repo=repo,
            what=what,
            against=against,
            filters=filters,
            use_merge_base=use_merge_base,
            pr=pr
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

    if aux_files or config.aux_files:
        aux_files_dict = read_files(
            repo,
            (aux_files or []) + config.aux_files,
            config.max_code_tokens // 2
        )
    else:
        aux_files_dict = {}

    if not prompt_file and config.answer_prompt.startswith("tpl:"):
        prompt_file = str(config.answer_prompt)[4:]

    if prompt_file:
        prompt_func = partial(mc.tpl, prompt_file)
    else:
        prompt_func = partial(mc.prompt, config.answer_prompt)
    prompt = prompt_func(
        question=question,
        diff=diff,
        all_file_lines=lines,
        pipeline_out=ctx.pipeline_out,
        aux_files=aux_files_dict,
        **config.prompt_vars,
    )
    response = mc.llm(
        prompt,
        callback=make_streaming_function() if Env.verbosity == 0 else None,
    )
    return response
