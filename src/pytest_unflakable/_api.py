"""Unflakable API."""

#  Copyright (c) 2022 Developer Innovations, LLC

from typing import List, Optional, TYPE_CHECKING

import sys
import logging
import pkg_resources
import platform
import pprint
import requests

if TYPE_CHECKING:
    from typing_extensions import NotRequired, TypedDict
else:
    from typing import Dict

    NotRequired = Optional
    TypedDict = Dict

BASE_URL = 'https://app.unflakable.com'
TEST_NAME_ENTRY_MAX_LENGTH = 4096
PACKAGE_VERSION = pkg_resources.get_distribution('pytest_unflakable').version
PLATFORM_VERSION = platform.platform()
PYTEST_VERSION = pkg_resources.get_distribution('pytest').version
PYTHON_VERSION = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
USER_AGENT = (
    f'unflakable-pytest-plugin/{PACKAGE_VERSION} (PyTest {PYTEST_VERSION}; '
    f'Python {PYTHON_VERSION}; Platform {PLATFORM_VERSION})'
)


class TestRef(TypedDict):
    test_id: str
    filename: str
    name: List[str]


class TestSuiteManifest(TypedDict):
    quarantined_tests: List[TestRef]


if TYPE_CHECKING:
    from typing import Literal

    TestAttemptResult = Literal['pass', 'fail', 'quarantined']
else:
    TestAttemptResult = str


class TestRunAttemptRecord(TypedDict):
    start_time: NotRequired[Optional[str]]
    end_time: NotRequired[Optional[str]]
    duration_ms: NotRequired[Optional[int]]
    result: TestAttemptResult


class TestRunRecord(TypedDict):
    filename: str
    name: List[str]
    attempts: List[TestRunAttemptRecord]


class CreateTestSuiteRunRequest(TypedDict):
    branch: NotRequired[Optional[str]]
    commit: NotRequired[Optional[str]]
    start_time: str
    end_time: str
    test_runs: List[TestRunRecord]


class TestSuiteRunPendingSummary(TypedDict):
    run_id: str
    suite_id: str
    branch: NotRequired[Optional[str]]
    commit: NotRequired[Optional[str]]


def create_test_suite_run(
        request: CreateTestSuiteRunRequest,
        test_suite_id: str,
        api_key: str,
        base_url: Optional[str],
        insecure_disable_tls_validation: bool,
        logger: logging.Logger,
) -> TestSuiteRunPendingSummary:
    logger.debug(f'creating test suite run {pprint.pformat(request)}')

    run_response = requests.post(
        url=(
            f'{base_url if base_url is not None else BASE_URL}/api/v1/test-suites/{test_suite_id}'
            '/runs'
        ),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'User-Agent': USER_AGENT,
        },
        json=request,
        verify=not insecure_disable_tls_validation,
    )
    run_response.raise_for_status()

    summary: TestSuiteRunPendingSummary = run_response.json()
    logger.debug(f'received response: {pprint.pformat(summary)}')

    return summary


def get_test_suite_manifest(
        test_suite_id: str,
        api_key: str,
        base_url: Optional[str],
        insecure_disable_tls_validation: bool,
        logger: logging.Logger,
) -> TestSuiteManifest:
    logger.debug(f'fetching manifest for test suite {test_suite_id}')

    manifest_response = requests.get(
        url=(
            f'{base_url if base_url is not None else BASE_URL}/api/v1/test-suites/{test_suite_id}'
            '/manifest'
        ),
        headers={
            'Authorization': f'Bearer {api_key}',
            'User-Agent': USER_AGENT,
        },
        verify=not insecure_disable_tls_validation,
    )
    manifest_response.raise_for_status()

    manifest: TestSuiteManifest = manifest_response.json()
    logger.debug(f'received response: {pprint.pformat(manifest)}')

    return manifest


def build_test_suite_run_url(
        test_suite_id: str,
        run_id: str,
        base_url: Optional[str]
) -> str:
    return (
        f'{base_url if base_url is not None else BASE_URL}/test-suites/{test_suite_id}'
        f'/runs/{run_id}'
    )
