"""Unflakable plugin for PyTest."""

#  Copyright (c) 2022 Developer Innovations, LLC

import argparse

import os
from typing import TYPE_CHECKING

import pytest
import logging

from ._git import get_current_git_commit, get_current_git_branch
from ._plugin import UnflakablePlugin, QuarantineMode

if TYPE_CHECKING:
    Config = pytest.Config
    Parser = pytest.Parser
else:
    Config = object
    Parser = object


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup('Unflakable')
    group.addoption(
        '--api-key-path',
        type=str,
        dest='unflakable_api_key_path',
        help=(
            'Path to file containing Unflakable API key. If not specified, UNFLAKABLE_API_KEY '
            'environment variable must be set.'
        )
    )
    group.addoption(
        '--base-url',
        type=str,
        dest='unflakable_base_url',
        help=argparse.SUPPRESS,
    )
    group.addoption(
        '--branch',
        type=str,
        dest='unflakable_branch',
        help='name of version control branch (overrides Git auto-detection).',
    )
    group.addoption(
        '--commit',
        type=str,
        dest='unflakable_commit',
        help='name of version control revision (overrides Git auto-detection).',
    )
    group.addoption(
        '--enable-unflakable',
        action='store_true',
        dest='unflakable_enable',
        help='enable Unflakable plugin.'
    )
    group.addoption(
        '--failure-retries',
        type=int,
        dest='unflakable_failure_retries',
        default='2',
        help='maximum number of times to retry each failed test. (default: 2)'
    )
    # For local testing only!
    group.addoption(
        '--insecure-disable-tls-validation',
        action='store_true',
        dest='unflakable_insecure_disable_tls_validation',
        help=argparse.SUPPRESS,
    )
    group.addoption(
        '--no-git-auto-detect',
        action='store_const',
        const=True,
        dest='unflakable_no_git_auto_detect',
        help='do not attempt to auto-detect the current branch and commit hash from Git.',
    )
    group.addoption(
        '--no-upload-results',
        action='store_const',
        const=True,
        dest='unflakable_no_upload_results',
        help='do not report test results to Unflakable.',
    )
    group.addoption(
        '--quarantine-mode',
        type=str,
        dest='unflakable_quarantine_mode',
        default='ignore_failures',
        help=(
            'controls the behavior of quarantined tests. (default: ignore_failures)'
        ),
        metavar='<ignore_failures|no_quarantine|skip_tests>'
    )
    group.addoption(
        '--test-suite-id',
        type=str,
        dest='unflakable_suite_id',
        help='Unflakable test suite ID (required if --enable-unflakable is provided).'
    )
    group.addoption(
        '--unflakable-log-level',
        type=str,
        dest='unflakable_log_level',
        help='log level for Unflakable plugin',
    )


def pytest_configure(config: Config) -> None:
    logger = logging.getLogger('pytest_unflakable')
    if config.option.unflakable_log_level is not None:
        logging.basicConfig()
        logger.setLevel(level=config.option.unflakable_log_level)

    if not config.getoption('unflakable_enable', False):
        return

    if config.option.unflakable_quarantine_mode.upper() not in QuarantineMode.__members__:
        raise pytest.UsageError(
            f'Unrecognized quarantine mode `{config.option.unflakable_quarantine_mode}`')
    else:
        quarantine_mode = QuarantineMode.__members__[
            config.option.unflakable_quarantine_mode.upper()]

    # workerinput is set for xdist workers, but we only want to enable the plugin on the controller.
    is_xdist_worker = hasattr(config, 'workerinput')

    if config.getoption('unflakable_suite_id') is None:
        raise pytest.UsageError('missing required argument --test-suite-id')

    # pytest-xdist workers don't make API calls and amy not have the API key available.
    if is_xdist_worker:
        api_key = ''
    elif config.option.unflakable_api_key_path is not None:
        with open(config.option.unflakable_api_key_path, 'r') as api_key_file:
            api_key = api_key_file.read()
    elif 'UNFLAKABLE_API_KEY' in os.environ:
        api_key = os.environ['UNFLAKABLE_API_KEY']
    else:
        raise pytest.UsageError('missing required environment variable `UNFLAKABLE_API_KEY`')

    branch = config.option.unflakable_branch
    commit = config.option.unflakable_commit
    git_auto_detect = not config.getoption('unflakable_no_git_auto_detect', False)
    if git_auto_detect and not is_xdist_worker:
        if commit is None:
            commit = get_current_git_commit()
            logger.debug('auto-detected commit `%s`', commit)

        if branch is None and commit is not None:
            branch = get_current_git_branch(commit, logger)
            logger.debug('auto-detected branch `%s`', branch)

    if is_xdist_worker and 'unflakable_manifest' in config.workerinput:  # type: ignore
        worker_manifest = config.workerinput['unflakable_manifest']  # type: ignore
    else:
        worker_manifest = None

    config.pluginmanager.register(UnflakablePlugin(
        api_key=api_key,
        base_url=config.option.unflakable_base_url,
        branch=branch,
        commit=commit,
        failure_retries=config.option.unflakable_failure_retries,
        insecure_disable_tls_validation=config.getoption(
            'unflakable_insecure_disable_tls_validation', False),
        quarantine_mode=quarantine_mode,
        test_suite_id=config.option.unflakable_suite_id,
        upload_results=not is_xdist_worker and (
            not config.getoption('unflakable_no_upload_results', False)),
        logger=logger,
        worker_manifest=worker_manifest,
        is_xdist_worker=is_xdist_worker,
    ))
