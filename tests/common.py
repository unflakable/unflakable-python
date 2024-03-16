"""Tests for pytest_unflakable plugin."""
#  Copyright (c) 2022-2024 Developer Innovations, LLC

import gzip
import hashlib
import itertools
import json
import os
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import (TYPE_CHECKING, Callable, Dict, Iterable, List, Optional,
                    Sequence, Tuple, cast)
from unittest import mock
from unittest.mock import Mock, call, patch

import pytest
import requests
import requests_mock
from _pytest.config import ExitCode

from pytest_unflakable import _api

if TYPE_CHECKING:
    CompletedProcess = subprocess.CompletedProcess[str]
    # These weren't exported until PyTest 6.2.0.
    MonkeyPatch = pytest.MonkeyPatch
else:
    CompletedProcess = subprocess.CompletedProcess
    MonkeyPatch = object

MOCK_RUN_ID = 'MOCK_RUN_ID'
MOCK_SUITE_ID = 'MOCK_SUITE_ID'
MOCK_TEAM_ID = 'MOCK_TEAM_ID'

# e.g., 2022-01-23T04:05:06.000000+00:00
TIMESTAMP_REGEX = (
    r'^[0-9]{4}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]\.[0-9]{6}\+00:00$'
)

# Tuple of (filename, name)
_TestKey = Tuple[str, Tuple[str, ...]]


class _TestAttemptOutcome(Enum):
    PASSED = 1
    FAILED = 2
    ERROR = 3
    ERROR_QUARANTINED = 4
    RETRY_PASSED = 5
    RETRY_FAILED = 6
    RETRY_QUARANTINED = 7
    RETRY_ERROR = 8
    RETRY_ERROR_QUARANTINED = 9
    RETRY_XFAILED = 10
    QUARANTINED = 11
    SKIPPED = 12
    XFAILED = 13
    XPASSED = 14


TEST_ATTEMPT_OUTCOME_CHARS = {
    _TestAttemptOutcome.PASSED: '\x1b[32m.\x1b[0m',
    _TestAttemptOutcome.FAILED: '\x1b[31mF\x1b[0m',
    _TestAttemptOutcome.ERROR: '\x1b[31mE\x1b[0m',
    _TestAttemptOutcome.ERROR_QUARANTINED: '\x1b[35mQ\x1b[0m',
    _TestAttemptOutcome.RETRY_PASSED: '\x1b[32mR\x1b[0m',
    _TestAttemptOutcome.RETRY_FAILED: '\x1b[31mR\x1b[0m',
    _TestAttemptOutcome.RETRY_QUARANTINED: '\x1b[35mQ\x1b[0m',
    _TestAttemptOutcome.RETRY_ERROR: '\x1b[31mR\x1b[0m',
    _TestAttemptOutcome.RETRY_ERROR_QUARANTINED: '\x1b[35mQ\x1b[0m',
    _TestAttemptOutcome.RETRY_XFAILED: '\x1b[33mR\x1b[0m',
    _TestAttemptOutcome.QUARANTINED: '\x1b[35mQ\x1b[0m',
    _TestAttemptOutcome.SKIPPED: '\x1b[33ms\x1b[0m',
    _TestAttemptOutcome.XFAILED: '\x1b[33mx\x1b[0m',
    _TestAttemptOutcome.XPASSED: '\x1b[33mX\x1b[0m',
}
VERBOSE_TEST_ATTEMPT_OUTCOME_CHARS = {
    _TestAttemptOutcome.PASSED: '\x1b[32mPASSED\x1b[0m',
    _TestAttemptOutcome.FAILED: '\x1b[31mFAILED\x1b[0m',
    _TestAttemptOutcome.ERROR: '\x1b[31mERROR\x1b[0m',
    _TestAttemptOutcome.ERROR_QUARANTINED: '\x1b[35mERROR (quarantined)\x1b[0m',
    _TestAttemptOutcome.RETRY_PASSED: '\x1b[32mPASSED (retry)\x1b[0m',
    _TestAttemptOutcome.RETRY_FAILED: '\x1b[31mFAILED (retry)\x1b[0m',
    _TestAttemptOutcome.RETRY_QUARANTINED: '\x1b[35mFAILED (retry, quarantined)\x1b[0m',
    _TestAttemptOutcome.RETRY_ERROR: '\x1b[31mERROR (retry)\x1b[0m',
    _TestAttemptOutcome.RETRY_ERROR_QUARANTINED: '\x1b[35mERROR (retry, quarantined)\x1b[0m',
    _TestAttemptOutcome.RETRY_XFAILED: '\x1b[33mXFAIL (retry)\x1b[0m',
    _TestAttemptOutcome.QUARANTINED: '\x1b[35mFAILED (quarantined)\x1b[0m',
    _TestAttemptOutcome.SKIPPED: '\x1b[33mSKIPPED\x1b[0m',
    _TestAttemptOutcome.XFAILED: '\x1b[33mXFAIL\x1b[0m',
    _TestAttemptOutcome.XPASSED: '\x1b[33mXPASS\x1b[0m',
}


class _TestResultCounts:
    def __init__(
            self,
            num_passed: int = 0,
            num_failed: int = 0,
            num_error: int = 0,
            num_flaky: int = 0,
            num_quarantined: int = 0,
            num_skipped: int = 0,
            num_deselected: int = 0,
            num_xfailed: int = 0,
            num_xpassed: int = 0,
            num_warnings: int = 0,
            num_collected: int = 0,
    ):
        self.num_passed = num_passed
        self.num_failed = num_failed
        self.num_error = num_error
        self.num_flaky = num_flaky
        self.num_quarantined = num_quarantined
        self.num_skipped = num_skipped
        self.num_deselected = num_deselected
        self.num_xfailed = num_xfailed
        self.num_xpassed = num_xpassed
        self.num_warnings = num_warnings
        self.num_collected = num_collected

    @property
    def color_code(self) -> str:
        if self.num_failed == 0 and self.num_error == 0 and self.num_quarantined == 0 and (
                self.num_flaky == 0
        ):
            if (self.num_passed > 0 or self.num_collected > 0) and (
                    self.num_xfailed == 0 and
                    self.num_xpassed == 0 and
                    self.num_warnings == 0
            ):
                # Green
                return '32'
            # Unfortunately, pytest treats all unknown outcome keys as yellow if there are no failed
            # (non-flaky) tests.
            else:
                # Yellow
                return '33'
        else:
            # Red
            return '31'

    @property
    def non_skipped_tests(self) -> int:
        return (
            self.num_passed +
            self.num_failed +
            self.num_error +
            self.num_flaky +
            self.num_quarantined +
            self.num_xfailed +
            self.num_xpassed
            # NB: We omit warnings here since those count the warnings, not the tests.
        )

    @property
    def total_tests(self) -> int:
        return self.non_skipped_tests + self.num_skipped + self.num_collected


class GitMock:
    def __init__(self) -> None:
        self.branch: Optional[str] = None
        self.commit: Optional[str] = None
        self.is_detached_head = False

    def update(
            self,
            branch: Optional[str],
            commit: Optional[str],
            is_detached_head: bool = False,
    ) -> None:
        self.branch = branch
        self.commit = commit
        self.is_detached_head = is_detached_head

    def mock_run(
            self,
            args: List[str],
            capture_output: bool = False,
            text: Optional[bool] = None,
    ) -> CompletedProcess:
        if args == ['git', 'rev-parse', 'HEAD']:
            if self.commit is not None:
                return subprocess.CompletedProcess(
                    args,
                    returncode=0,
                    stdout=self.commit + '\n',
                )
            else:
                raise RuntimeError('mock git error')
        elif args == ['git', 'rev-parse', '--abbrev-ref', 'HEAD']:
            if self.commit is not None and self.branch is not None:
                if self.is_detached_head:
                    return subprocess.CompletedProcess(
                        args,
                        returncode=0,
                        stdout='HEAD\n',
                    )
                else:
                    return subprocess.CompletedProcess(
                        args,
                        returncode=0,
                        stdout=self.branch + '\n',
                    )
            else:
                raise RuntimeError('mock git error')
        elif args == ['git', 'show-ref']:
            if self.commit is not None and self.branch is not None:
                if self.is_detached_head:
                    return subprocess.CompletedProcess(
                        args,
                        returncode=0,
                        stdout=f'{self.commit} refs/heads/{self.branch}\n',
                    )
                else:
                    raise RuntimeError("`git show-ref` shouldn't be called")
            else:
                raise RuntimeError('mock git error')
        elif self.commit is not None and self.branch is not None and (
                args == ['git', 'rev-parse', '--abbrev-ref', f'refs/heads/{self.branch}']):
            if self.is_detached_head:
                return subprocess.CompletedProcess(
                    args,
                    returncode=0,
                    stdout=self.branch + '\n',
                )
            else:
                raise RuntimeError(
                    "`git rev-parse --abbrev-ref refs/heads/BRANCH` shouldn't be called"
                )
        else:
            raise RuntimeError(f'unexpected git call with args: {repr(args)}')


__uploads: Dict[str, Optional[_api.CreateTestSuiteRunInlineRequest]] = {}


def __upload_id_for_current_test() -> str:
    return hashlib.sha1(os.environ['PYTEST_CURRENT_TEST'].encode('utf8')).hexdigest()


def __upload_url(upload_id: str) -> str:
    return (
        f'https://s3.mock.amazonaws.com/unflakable-backend-mock-test-uploads/teams/{MOCK_TEAM_ID}'
        f'/suites/{MOCK_SUITE_ID}/runs/upload/{upload_id}?X-Amz-Signature=MOCK_SIGNATURE'
    )


def __mock_create_test_suite_run_upload_url_response(
        upload_id: str,
        request: requests_mock.request._RequestObjectProxy,
        context: requests_mock.response._Context,
) -> _api.CreateTestSuiteRunUploadUrlResponse:
    upload_url = __upload_url(upload_id)
    assert upload_url not in __uploads
    __uploads[upload_url] = None

    context.headers['Location'] = upload_url
    return {
        'upload_id': upload_id,
    }


def __match_upload(request: requests_mock.request._RequestObjectProxy) -> bool:
    return re.match(
        r'^%s[0-9a-f]{40}%s$' % (
            re.escape(
                'https://s3.mock.amazonaws.com/unflakable-backend-mock-test-uploads/teams/'
                f'{MOCK_TEAM_ID}/suites/{MOCK_SUITE_ID}/runs/upload/'
            ),
            re.escape('?X-Amz-Signature=MOCK_SIGNATURE')
        ),
        request.url
    ) is not None


def __mock_upload_response(
        request: requests_mock.request._RequestObjectProxy,
        context: requests_mock.response._Context,
) -> bytes:
    assert request.url in __uploads
    assert __uploads[request.url] is None, 'duplicate upload'
    __uploads[request.url] = json.loads(gzip.decompress(request.body))
    return b''


def __mock_create_test_suite_run_response(
        request: requests_mock.request._RequestObjectProxy,
        context: requests_mock.response._Context,
) -> _api.TestSuiteRunPendingSummary:
    request_body: _api.CreateTestSuiteRunUploadRequest = request.json()
    upload_url = __upload_url(request_body['upload_id'])
    upload = __uploads[upload_url]
    assert upload is not None, 'missing upload'

    return {
        'run_id': MOCK_RUN_ID,
        'suite_id': MOCK_SUITE_ID,
        'branch': upload.get('branch'),
        'commit': upload.get('commit'),
    }


def assert_regex(regex: str, string: str) -> None:
    assert re.match(regex, string) is not None, f'`{string}` does not match regex {regex}'


@patch.multiple('time', sleep=mock.DEFAULT)
@requests_mock.Mocker(case_sensitive=True, kw='requests_mocker')
def run_test_case(
        pytester: pytest.Pytester,
        manifest: Optional[_api.TestSuiteManifest],
        requests_mocker: requests_mock.Mocker,
        sleep: Mock,
        expected_test_file_outcomes: List[
            Tuple[str, List[Tuple[Tuple[str, ...], List[_TestAttemptOutcome]]]]],
        expected_test_result_counts: _TestResultCounts,
        expected_uploaded_test_runs: Optional[Dict[_TestKey, List[_api.TestAttemptResult]]],
        expected_exit_code: ExitCode,
        expected_branch: Optional[str] = 'MOCK_BRANCH',
        expected_commit: Optional[str] = 'MOCK_COMMIT',
        expected_api_key: str = 'MOCK_API_KEY',
        verbose: bool = False,
        extra_args: Iterable[str] = (),
        plugin_enabled: bool = True,
        use_api_key_path: bool = True,
        monkeypatch: Optional[MonkeyPatch] = None,
        env_vars: Optional[Dict[str, str]] = None,
        expect_progress: bool = True,
        expect_xdist: bool = False,
        failed_manifest_requests: int = 0,
        failed_upload_requests: int = 0,
) -> None:
    api_key_path = pytester.makefile('', expected_api_key) if use_api_key_path else None
    requests_mocker.get(
        url=f'https://app.unflakable.com/api/v1/test-suites/{MOCK_SUITE_ID}/manifest',
        request_headers={'Authorization': f'Bearer {expected_api_key}'},
        complete_qs=True,
        response_list=[
            {'exc': requests.exceptions.ConnectTimeout}
            for _ in range(failed_manifest_requests)
        ] + ([{
            'status_code': 200,
            'json': manifest,
        }] if manifest is not None else [])
    )

    upload_id = __upload_id_for_current_test()
    requests_mocker.post(
        url=f'https://app.unflakable.com/api/v1/test-suites/{MOCK_SUITE_ID}/runs/upload',
        request_headers={
            'Authorization': f'Bearer {expected_api_key}'
        },
        complete_qs=True,
        response_list=[
            {'exc': requests.exceptions.ConnectTimeout}
            for _ in range(failed_upload_requests)
        ] + [{
            'status_code': 201,
            'json': lambda request, context: __mock_create_test_suite_run_upload_url_response(
                upload_id,
                request,
                context,
            ),
        }]
    )

    requests_mocker.put(
        # The __match_upload() function matches the URL.
        requests_mock.ANY,
        request_headers={
            'Content-Encoding': 'gzip',
            'Content-Type': 'application/json',
        },
        complete_qs=True,
        status_code=200,
        additional_matcher=__match_upload,
        content=__mock_upload_response,
    )

    requests_mocker.post(
        url=f'https://app.unflakable.com/api/v1/test-suites/{MOCK_SUITE_ID}/runs',
        request_headers={
            'Authorization': f'Bearer {expected_api_key}',
            'Content-Type': 'application/json',
        },
        complete_qs=True,
        status_code=201,
        json=__mock_create_test_suite_run_response,
    )

    pytest_args: List[str] = (
        (['--enable-unflakable'] if plugin_enabled else []) +
        (['--api-key-path', str(api_key_path)] if api_key_path is not None else []) +
        [
            '--test-suite-id', MOCK_SUITE_ID,
            '--unflakable-log-level', 'DEBUG',
            '--color', 'yes',
        ] + (
            ['-v'] if verbose else []
        ) + list(extra_args)
    )

    __pytest_current_test = os.environ['PYTEST_CURRENT_TEST']
    if monkeypatch is not None:
        with monkeypatch.context() as mp:
            for key, val in (env_vars if env_vars is not None else {}).items():
                mp.setenv(key, val)

            result = pytester.runpytest(*pytest_args)
    else:
        result = pytester.runpytest(*pytest_args)

    # pytester clears PYTEST_CURRENT_TEST for some reason.
    os.environ['PYTEST_CURRENT_TEST'] = __pytest_current_test

    if verbose:
        test_outcomes_output = [
            # Per-file test outcomes (one line for each test, color-coded).
            ''.join([
                '^',
                r'\[gw[0-9]\]\x1b\[36m \[ *[0-9]+%\] \x1b\[0m',
                re.escape(VERBOSE_TEST_ATTEMPT_OUTCOME_CHARS[test_outcome]),
                ' %s ' % (re.escape('::'.join((Path(test_file).as_posix(),) + test_name))),
                '$',
            ] if expect_xdist else [
                '^',
                '%s ' % (re.escape('::'.join((Path(test_file).as_posix(),) + test_name))),
                re.escape(VERBOSE_TEST_ATTEMPT_OUTCOME_CHARS[test_outcome]),
                # Statuses may be truncated when test names are long (e.g., when there are parent
                # classes) to keep lines under 80 chars. Consequently, we assume anything can appear
                # between the parentheses after skipped tests.
                r' \([^)]*\)' if test_outcome == _TestAttemptOutcome.SKIPPED else '',
                r'\x1b[..m +\[ *[0-9]+%\]\x1b\[0m',
                '$',
            ])
            for test_file, test_file_outcomes in expected_test_file_outcomes
            for test_name, test_name_outcomes in test_file_outcomes
            for test_outcome in test_name_outcomes
        ]
    elif expect_xdist:
        test_outcomes_flattened = [
            test_outcome.value
            for test_file, test_file_outcomes in expected_test_file_outcomes
            for test_name, test_outcomes in test_file_outcomes
            for test_outcome in test_outcomes
        ]
        test_outcomes_output = [
            '^' + ''.join([
                # Use a positive lookahead to make sure the expected number of each outcome appears
                # in the output, irrespective of ordering (since xdist introduces non-deterministic
                # ordering).
                ('(?=.*' +
                 '.*'.join([
                     re.escape(
                         TEST_ATTEMPT_OUTCOME_CHARS[_TestAttemptOutcome(test_outcome)])
                 ] * count) + '.*)')
                for test_outcome, count in (
                    {test_outcome: len([i for i in instances])
                     for test_outcome, instances in
                     itertools.groupby(sorted(test_outcomes_flattened))
                     }
                ).items()
            ]) + (r'\x1b\[..m.\x1b\[0m' * len(test_outcomes_flattened)) + (
                r'\x1b\[..m +\[ *[0-9]+%\]\x1b\[0m' if expect_progress and len(
                    expected_test_file_outcomes) > 0 else '') + '$'
        ]
    else:
        test_outcomes_output = [
            r'^%s %s%s$' % (
                re.escape(test_file),
                ''.join(
                    [
                        re.escape(TEST_ATTEMPT_OUTCOME_CHARS[test_outcome])
                        for test_name, test_name_outcomes in test_file_outcomes
                        for test_outcome in test_name_outcomes
                    ],
                ),
                # For --stepwise interrupts, the progress stats don't get printed.
                r'\x1b\[..m +\[ *[0-9]+%\]\x1b\[0m' if expect_progress else ''
            )
            for test_file, test_file_outcomes in expected_test_file_outcomes
        ]

    # xdist has non-deterministic ordering.
    cast(Callable[[Sequence[str]], None],
         result.stdout.re_match_lines_random if expect_xdist else result.stdout.re_match_lines)(
        test_outcomes_output
    )

    quarantined_test_regex = (
        '\x1b\\[33m%s%s quarantined\x1b\\[0m' % (
            '\x1b\\[1m' if expected_test_result_counts.color_code == '33' else '',
            expected_test_result_counts.num_quarantined
        )
    ) if expected_test_result_counts.num_quarantined > 0 else None
    flaky_test_regex = (
        '\x1b\\[33m%s%s flaky\x1b\\[0m' % (
            '\x1b\\[1m' if expected_test_result_counts.color_code == '33' else '',
            expected_test_result_counts.num_flaky
        )
    ) if expected_test_result_counts.num_flaky > 0 else None

    # NB: The order of 'unknown' (to pytest) categories matches the order that
    # they're returned by pytest_report_teststatus(), which in turn matches the
    # order in which they appear in the tests. This ordering may be non-deterministic,
    # especially in the case of xdist, so we accept either ordering.
    custom_result_regex = (
        f'(?:(?:{quarantined_test_regex}, {flaky_test_regex})|'
        f'(?:{flaky_test_regex}, {quarantined_test_regex}))'
    ) if quarantined_test_regex is not None and flaky_test_regex is not None else (
        quarantined_test_regex if quarantined_test_regex is not None else flaky_test_regex
    )

    result.stdout.re_match_lines([
        # Summary stats (color-coded).
        '^\x1b\\[%sm=+ %s\x1b\\[%sm in [0-9.]+s\x1b\\[0m\x1b\\[%sm =+\x1b\\[0m$' % (
            expected_test_result_counts.color_code,
            ', '.join(
                # NB: These follow the order of KNOWN_TYPES in _pytest/terminal.py, followed by
                # the unknown (to pytest) categories.
                ([
                    '\x1b\\[31m%s%d failed\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '31' else '',
                        expected_test_result_counts.num_failed,
                    )
                ] if expected_test_result_counts.num_failed > 0 else []) +
                ([
                    '\x1b\\[32m%s%d passed\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '32' else '',
                        expected_test_result_counts.num_passed,
                    )
                ] if expected_test_result_counts.num_passed > 0 else []) +
                ([
                    '\x1b\\[33m%s%s skipped\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '33' else '',
                        expected_test_result_counts.num_skipped
                    )
                ] if expected_test_result_counts.num_skipped > 0 else []) +
                ([
                    '\x1b\\[33m%s%s deselected\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '33' else '',
                        expected_test_result_counts.num_deselected
                    )
                ] if expected_test_result_counts.num_deselected > 0 else []) +
                ([
                    '\x1b\\[33m%s%s xfailed\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '33' else '',
                        expected_test_result_counts.num_xfailed
                    )
                ] if expected_test_result_counts.num_xfailed > 0 else []) +
                ([
                    '\x1b\\[33m%s%s xpassed\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '33' else '',
                        expected_test_result_counts.num_xpassed
                    )
                ] if expected_test_result_counts.num_xpassed > 0 else []) +
                ([
                    '\x1b\\[33m%s%s warnings\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '33' else '',
                        expected_test_result_counts.num_warnings
                    )
                ] if expected_test_result_counts.num_warnings > 0 else []) +
                ([
                    '\x1b\\[31m%s%d error\x1b\\[0m' % (
                        '\x1b\\[1m' if expected_test_result_counts.color_code == '31' else '',
                        expected_test_result_counts.num_error,
                    )
                ] if expected_test_result_counts.num_error > 0 else []) +
                ([
                    '\x1b\\[32m%d test collected\x1b\\[0m' % (
                        expected_test_result_counts.num_collected,
                    )
                ] if expected_test_result_counts.num_collected > 0 else []) +
                ([
                    custom_result_regex] if custom_result_regex is not None else []) +
                ([
                    '\x1b\\[33mno tests ran\x1b\\[0m'
                ] if expected_test_result_counts.total_tests == 0 else []),
            ),
            expected_test_result_counts.color_code,
            expected_test_result_counts.color_code,
        )
    ] + ([
        # Unflakable report URL.
        r'^Unflakable report: https://app\.unflakable\.com/test-suites/%s/runs/%s$' % (
            MOCK_SUITE_ID, MOCK_RUN_ID),
    ] if plugin_enabled and (
        expected_uploaded_test_runs is not None and
        expected_test_result_counts.non_skipped_tests > 0) else [])
    )

    if plugin_enabled:
        expected_get_test_suite_manifest_attempts = (
            failed_manifest_requests + (1 if failed_manifest_requests <
                                        _api.NUM_REQUEST_TRIES and manifest is not None else 0)
        )
        for manifest_attempt in range(expected_get_test_suite_manifest_attempts):
            request = requests_mocker.request_history[manifest_attempt]

            assert request.url == (
                f'https://app.unflakable.com/api/v1/test-suites/{MOCK_SUITE_ID}/manifest'
            )
            assert request.method == 'GET'
            assert request.headers.get('Authorization', '') == f'Bearer {expected_api_key}'
            assert request.body is None

            if manifest_attempt > 0:
                assert (
                    sleep.call_args_list[manifest_attempt - 1] == call(2 ** (manifest_attempt - 1))
                )

        expected_upload_attempts = (
            failed_upload_requests + (1 if (
                failed_upload_requests < _api.NUM_REQUEST_TRIES
                and expected_uploaded_test_runs is not None
                and expected_test_result_counts.non_skipped_tests != 0
            ) else 0)
        )

        for upload_attempt in range(expected_upload_attempts):
            create_upload_url_request = requests_mocker.request_history[
                expected_get_test_suite_manifest_attempts + upload_attempt
            ]
            assert create_upload_url_request.url == (
                f'https://app.unflakable.com/api/v1/test-suites/{MOCK_SUITE_ID}/runs/upload')
            assert create_upload_url_request.method == 'POST'
            assert (
                create_upload_url_request.headers.get('Authorization')
                == f'Bearer {expected_api_key}'
            )

            # Failed attempts only include the initial request.
            if upload_attempt < failed_upload_requests:
                continue

            upload_request = requests_mocker.request_history[
                expected_get_test_suite_manifest_attempts + upload_attempt + 1
            ]
            assert upload_request.url == __upload_url(upload_id)
            assert upload_request.method == 'PUT'
            assert upload_request.headers.get('Content-Encoding') == 'gzip'
            assert upload_request.headers.get('Content-Type') == 'application/json'
            upload_body: _api.CreateTestSuiteRunInlineRequest = (
                json.loads(gzip.decompress(upload_request.body))
            )

            assert_regex(TIMESTAMP_REGEX, upload_body['start_time'])
            assert_regex(TIMESTAMP_REGEX, upload_body['end_time'])

            actual_test_runs = {
                (test_run_record['filename'], tuple(test_run_record['name'])): [
                    attempt['result'] for attempt in test_run_record['attempts']
                ]
                for test_run_record in upload_body['test_runs']
            }
            assert actual_test_runs == expected_uploaded_test_runs

            # Make sure there aren't any duplicate test keys.
            assert len(upload_body['test_runs']) == len(actual_test_runs)

            if expected_commit is not None:
                assert upload_body['commit'] == expected_commit
            else:
                assert 'commit' not in upload_body

            if expected_branch is not None:
                assert upload_body['branch'] == expected_branch
            else:
                assert 'branch' not in upload_body

            create_test_suite_run_request = requests_mocker.request_history[
                expected_get_test_suite_manifest_attempts + upload_attempt + 2
            ]
            assert create_test_suite_run_request.url == (
                f'https://app.unflakable.com/api/v1/test-suites/{MOCK_SUITE_ID}/runs')
            assert create_test_suite_run_request.method == 'POST'
            assert (
                create_test_suite_run_request.headers.get('Authorization')
                == f'Bearer {expected_api_key}'
            )
            assert create_test_suite_run_request.headers.get('Content-Type') == 'application/json'
            create_test_suite_run_body: _api.CreateTestSuiteRunUploadRequest = (
                create_test_suite_run_request.json()
            )
            assert create_test_suite_run_body['upload_id'] == upload_id

            if upload_attempt > 0:
                assert (
                    sleep.call_args_list[
                        max(expected_get_test_suite_manifest_attempts - 1, 0) +
                        upload_attempt - 1
                    ] == call(2 ** (upload_attempt - 1))
                )

        assert requests_mocker.call_count == (
            expected_get_test_suite_manifest_attempts
            + failed_upload_requests + 3 * (expected_upload_attempts - failed_upload_requests)
        ), 'Expected %d total API requests, but received %d' % (
            expected_get_test_suite_manifest_attempts + expected_upload_attempts,
            requests_mocker.call_count,
        )

        # Checked expected User-Agent. We do this here instead of using an `additional_matcher` to
        # make errors easier to diagnose.
        for request in requests_mocker.request_history:
            assert_regex(
                r'^unflakable-pytest-plugin/.* \(PyTest .*; Python .*; Platform .*\)$',
                request.headers.get('User-Agent', '')
            )
    else:
        assert requests_mocker.call_count == 0
        assert sleep.call_count == 0

    assert result.ret == expected_exit_code, (
        f'expected exit code {expected_exit_code}, but got {result.ret}')

    if expected_test_result_counts.num_quarantined > 0 and (
            expected_test_result_counts.num_flaky == 0 and
            expected_test_result_counts.num_failed == 0 and
            expected_test_result_counts.num_error == 0):
        result.stderr.re_match_lines([r'^Exit: All failed tests are quarantined$'])
    else:
        result.stderr.no_re_match_line(r'^Exit: All failed tests are quarantined$')
