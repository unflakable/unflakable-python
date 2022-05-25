#  Copyright (c) 2022 Developer Innovations, LLC
import logging
from typing import Optional, List
from subprocess import run
import sys

GIT_ERROR_STR = 'WARNING: Unflakable failed to auto-detect current git branch and commit'
GIT_ERROR_HINT = (
    'HINT: pass the --branch and --commit command-line arguments or disable git '
    'auto-detection by passing --no-git-auto-detect'
)


def _run_git_command(args: List[str]) -> Optional[str]:
    try:
        git_outcome = run(
            ['git'] + args,
            capture_output=True,
            text=True,
        )
    except Exception as e:
        sys.stderr.write(f'{GIT_ERROR_STR}: {repr(e)}\n{GIT_ERROR_HINT}\n')
        return None

    if git_outcome is None:
        return None
    elif git_outcome.returncode != 0:
        sys.stderr.write(
            f'{GIT_ERROR_STR}: git exited with non-zero exit code {git_outcome.returncode}: '
            f'{git_outcome.stderr}\n{GIT_ERROR_HINT}\n'
        )
        return None
    else:
        return git_outcome.stdout


def get_current_git_branch(commit_sha: str, logger: logging.Logger) -> Optional[str]:
    # In the common case (an attached HEAD), we can just use `git rev-parse`.
    head_ref_raw = _run_git_command(['rev-parse', '--abbrev-ref', 'HEAD'])
    head_ref = head_ref_raw if head_ref_raw is None else head_ref_raw.rstrip()

    # If `git rev-parse` returns `HEAD`, then we have a detached head, and we need to see if the
    # current commit SHA matches any known refs (i.e., local/remote branches or tags). This happens
    # when running GitHub Actions in response to a `pull_request` event. In that case, the commit
    # is a detached HEAD, but there's a `refs/remotes/pull/PR_NUMBER/merge` ref we can use as the
    # "branch" (abbreviated to pull/PR_NUMBER/merge).
    if head_ref != 'HEAD':
        return head_ref

    # The code below runs the equivalent of `git show-ref | grep $(git rev-parse HEAD)`.
    git_output = _run_git_command(['show-ref'])
    if git_output is None:
        return git_output

    matching_refs = [
        line.split(' ', 1)[1] for line in git_output.splitlines()
        if line.startswith(commit_sha + ' ')
    ]
    logger.debug(
        'git show-ref returned %d ref(s) for SHA %s: %s',
        len(matching_refs),
        commit_sha,
        ', '.join(matching_refs)
    )

    if len(matching_refs) == 0:
        return None

    # `git show-ref` returns refs sorted lexicographically:
    #   refs/heads/*
    #   refs/remotes/*
    #   refs/stash
    #   refs/tags/*
    # We just take the first matching ref and use its abbreviation (i.e., removing the refs/remotes
    # prefix) as the branch name. Users can override this behavior by setting the UNFLAKABLE_BRANCH
    # environment variable.
    abbreviated_ref = _run_git_command(['rev-parse', '--abbrev-ref', matching_refs[0]])
    if abbreviated_ref is None:
        return None
    else:
        return abbreviated_ref.rstrip()


def get_current_git_commit() -> Optional[str]:
    git_commit = _run_git_command(['rev-parse', 'HEAD'])
    if git_commit is None:
        return None
    else:
        return git_commit.rstrip()
