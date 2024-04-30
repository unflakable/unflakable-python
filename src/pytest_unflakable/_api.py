"""Unflakable API."""

#  Copyright (c) 2022-2024 Developer Innovations, LLC

from __future__ import annotations

import gzip
import json
import logging
import platform
import pprint
import sys
import time
from importlib.metadata import version
from typing import TYPE_CHECKING, List, Mapping, Optional

import requests
from requests import HTTPError, Response, Session

if TYPE_CHECKING:
    from typing_extensions import NotRequired, TypedDict
else:
    from typing import Dict

    NotRequired = Optional
    TypedDict = Dict

BASE_URL = 'https://app.unflakable.com'
TEST_NAME_ENTRY_MAX_LENGTH = 4096
PACKAGE_VERSION = version('pytest_unflakable')
PLATFORM_VERSION = platform.platform()
PYTEST_VERSION = version('pytest')
PYTHON_VERSION = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
USER_AGENT = (
    f'unflakable-pytest-plugin/{PACKAGE_VERSION} (PyTest {PYTEST_VERSION}; '
    f'Python {PYTHON_VERSION}; Platform {PLATFORM_VERSION})'
)
NUM_REQUEST_TRIES = 3


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


class CreateTestSuiteRunInlineRequest(TypedDict):
    branch: NotRequired[Optional[str]]
    commit: NotRequired[Optional[str]]
    start_time: str
    end_time: str
    test_runs: List[TestRunRecord]


class CreateTestSuiteRunUploadRequest(TypedDict):
    upload_id: str


class CreateTestSuiteRunUploadUrlResponse(TypedDict):
    upload_id: str


class TestSuiteRunPendingSummary(TypedDict):
    run_id: str
    suite_id: str
    branch: NotRequired[Optional[str]]
    commit: NotRequired[Optional[str]]


def __new_requests_session() -> Session:
    session = Session()
    session.headers['User-Agent'] = USER_AGENT

    return session


def __send_api_request(
    session: Session,
    api_key: Optional[str],
    method: Literal['GET', 'POST', 'PUT'],
    url: str,
    logger: logging.Logger,
    headers: Optional[Mapping[str, str | bytes | None]] = None,
    body: Optional[str | bytes] = None,
    verify: Optional[bool | str] = None,
) -> Response:
    for idx in range(NUM_REQUEST_TRIES):
        try:
            response = session.request(
                method,
                url,
                headers={
                    **({'Authorization': f'Bearer {api_key}'} if api_key is not None else {}),
                    **(headers if headers is not None else {})
                },
                data=body,
                verify=verify,
            )
            if response.status_code not in [429, 500, 502, 503, 504]:
                return response
            elif idx + 1 != NUM_REQUEST_TRIES:
                logger.warning(
                    'Retrying request to `%s` due to unexpected response with status code %d' % (
                        url,
                        response.status_code,
                    )
                )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if idx + 1 != NUM_REQUEST_TRIES:
                logger.warning('Retrying %s request to `%s` due to error: %s' %
                               (method, url, repr(e)))
            else:
                raise

        sleep_sec = (2 ** idx)
        logger.debug('Sleeping for %f second(s) before retry' % sleep_sec)
        time.sleep(sleep_sec)

    return response


def create_test_suite_run(
        request: CreateTestSuiteRunInlineRequest,
        test_suite_id: str,
        api_key: str,
        base_url: Optional[str],
        insecure_disable_tls_validation: bool,
        logger: logging.Logger,
) -> TestSuiteRunPendingSummary:
    logger.debug(f'creating test suite run {pprint.pformat(request)}')

    session = __new_requests_session()

    create_upload_url_response = __send_api_request(
        session=session,
        api_key=api_key,
        method='POST',
        url=(
            f'{base_url if base_url is not None else BASE_URL}/api/v1/test-suites/{test_suite_id}'
            '/runs/upload'
        ),
        logger=logger,
        verify=not insecure_disable_tls_validation,
    )

    create_upload_url_response.raise_for_status()
    if create_upload_url_response.status_code != 201:
        raise HTTPError(
            f'Expected 201 response but received {create_upload_url_response.status_code}')

    upload_presigned_url = create_upload_url_response.headers.get('Location', None)
    if upload_presigned_url is None:
        raise HTTPError('Location response header not found')

    create_upload_url_response_body: CreateTestSuiteRunUploadUrlResponse = (
        create_upload_url_response.json()
    )
    upload_id = create_upload_url_response_body['upload_id']

    gzipped_request = gzip.compress(json.dumps(request).encode('utf8'))
    upload_response = __send_api_request(
        session=session,
        api_key=None,
        method='PUT',
        url=upload_presigned_url,
        logger=logger,
        headers={
            'Content-Encoding': 'gzip',
            'Content-Type': 'application/json',
        },
        body=gzipped_request,
        verify=not insecure_disable_tls_validation,
    )
    upload_response.raise_for_status()

    request_body: CreateTestSuiteRunUploadRequest = {'upload_id': upload_id}
    run_response = __send_api_request(
        session=session,
        api_key=api_key,
        method='POST',
        url=(
            f'{base_url if base_url is not None else BASE_URL}/api/v1/test-suites/{test_suite_id}'
            '/runs'
        ),
        logger=logger,
        headers={'Content-Type': 'application/json'},
        body=json.dumps(request_body).encode('utf8'),
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

    session = __new_requests_session()

    manifest_response = __send_api_request(
        session=session,
        api_key=api_key,
        method='GET',
        url=(
            f'{base_url if base_url is not None else BASE_URL}/api/v1/test-suites/{test_suite_id}'
            '/manifest'
        ),
        logger=logger,
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
