"""Tests for pytest_unflakable plugin."""
import os

#  Copyright (c) 2022-2023 Developer Innovations, LLC

import pytest
import requests_mock
from _pytest.config import ExitCode
import sys
import platform

from pytest_unflakable import _api

from .common import (
    GitMock, run_test_case, _TestAttemptOutcome, _TestResultCounts, MonkeyPatch
)

requests_mock.mock.case_sensitive = True

# Run on 2 CPUs.
XDIST_ARGS = ['-n', '2']


# These fixtures let us include info about the test environment so that it's easy to understand
# which environment a test failed in. They're named such that in lexicographical order they'll
# render as parameters in the order <python-version>-<pytest-version>-<platform>
@pytest.fixture(params=[f'py{sys.version_info.major}.{sys.version_info.minor}'], autouse=True)
def _1python_version() -> None:
    pass


@pytest.fixture(params=[f'pytest{_api.PYTEST_VERSION}'], autouse=True)
def _2pytest_version() -> None:
    pass


@pytest.fixture(params=[platform.system().lower()], autouse=True)
def _3platform() -> None:
    pass


TEST_PARAMS_XDIST_ARG_NAMES = ['xdist']
TEST_PARAMS_XDIST_ARG_VALUES = (
        [
            pytest.param(False, id='not_xdist'),
        ] + ([
                 pytest.param(True, id='xdist'),
             ] if os.environ.get('TEST_XDIST') == '1' else [])
)

TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES = ['verbose', 'xdist']
TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES = (
        [
            pytest.param(False, False, id='not_verbose-not_xdist'),
            pytest.param(True, False, id='verbose-not_xdist'),
        ] + ([
                 pytest.param(False, True, id='not_verbose-xdist'),
                 pytest.param(True, True, id='verbose-xdist'),
             ] if os.environ.get('TEST_XDIST') == '1' else [])
)

TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_NAMES = ['verbose', 'quarantined', 'xdist']
TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_VALUES = (
        [
            pytest.param(False, False, False, id='not_verbose-not_quarantined-not_xdist'),
            pytest.param(False, True, False, id='not_verbose-quarantined-not_xdist'),
            pytest.param(True, False, False, id='verbose-not_quarantined-not_xdist'),
            pytest.param(True, True, False, id='verbose-quarantined-not_xdist'),
        ] + ([
                 pytest.param(False, False, True, id='not_verbose-not_quarantined-xdist'),
                 pytest.param(False, True, True, id='not_verbose-quarantined-xdist'),
                 pytest.param(True, False, True, id='verbose-not_quarantined-xdist'),
                 pytest.param(True, True, True, id='verbose-quarantined-xdist'),
             ] if os.environ.get('TEST_XDIST') == '1' else [])
)


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_flaky(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        first_invocation = True


        def test_flaky():
            global first_invocation
            if first_invocation:
                first_invocation = False
                assert False
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_flaky',), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ])
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_flaky=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_flaky',)): ['fail', 'pass'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_quarantine_flaky(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        first_invocation = True


        def test_flaky():
            global first_invocation
            if first_invocation:
                first_invocation = False
                assert False
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_flaky']
            }
        ]},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_flaky',), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ])
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_quarantined=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_flaky',)): ['quarantined', 'pass'],
        },
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_flaky_until_last_attempt(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        invocation_count = 0


        def test_flaky():
            global invocation_count
            if invocation_count < 2:
                invocation_count += 1
                assert False
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_flaky',), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ])
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_flaky=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_flaky',)): ['fail', 'fail', 'pass'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_all_statuses(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_fail():
            assert False


        def test_quarantined():
            assert False


        first_invocation = True


        def test_flaky():
            global first_invocation
            if first_invocation:
                first_invocation = False
                assert False


        def test_pass():
            pass


        @pytest.mark.skip
        def test_skipped():
            assert False
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_fail',), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                    ]),
                    (('test_quarantined',), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                    ]),
                    (('test_flaky',), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_skipped',), [_TestAttemptOutcome.SKIPPED]),
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(
            num_failed=1,
            num_flaky=1,
            num_passed=1,
            num_quarantined=1,
            num_skipped=1,
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_fail',)): ['fail', 'fail', 'fail'],
            ('test_input.py', ('test_quarantined',)): ['quarantined', 'quarantined', 'quarantined'],
            ('test_input.py', ('test_flaky',)): ['fail', 'pass'],
            ('test_input.py', ('test_pass',)): ['pass'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_class_all_statuses(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest

        first_invocation = True


        class TestAllStatuses:
            def test_fail(self):
                assert False


            def test_quarantined(self):
                assert False


            def test_flaky(self):
                global first_invocation
                if first_invocation:
                    first_invocation = False
                    assert False


            def test_pass(self):
                pass


            @pytest.mark.skip
            def test_skipped(self):
                assert False
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['TestAllStatuses', 'test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('TestAllStatuses', 'test_fail'), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                    ]),
                    (('TestAllStatuses', 'test_quarantined'), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                    ]),
                    (('TestAllStatuses', 'test_flaky'), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                    (('TestAllStatuses', 'test_pass'), [_TestAttemptOutcome.PASSED]),
                    (('TestAllStatuses', 'test_skipped'), [_TestAttemptOutcome.SKIPPED]),
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(
            num_failed=1,
            num_flaky=1,
            num_passed=1,
            num_quarantined=1,
            num_skipped=1,
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('TestAllStatuses', 'test_fail')): ['fail', 'fail', 'fail'],
            ('test_input.py', ('TestAllStatuses', 'test_quarantined')): [
                'quarantined', 'quarantined', 'quarantined'],
            ('test_input.py', ('TestAllStatuses', 'test_flaky')): ['fail', 'pass'],
            ('test_input.py', ('TestAllStatuses', 'test_pass')): ['pass'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_nested_classes(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    """
    Ensures quarantining works properly with nested classes containing tests of the same name.
    """
    pytester.makepyfile(test_input="""
        import pytest


        first_invocation1 = True
        first_invocation2 = True
        first_invocation3 = True


        class TestClass:
            def test_flaky(self):
                global first_invocation1
                if first_invocation1:
                    first_invocation1 = False
                    assert False


            class TestInnerA:
                def test_flaky(self):
                    global first_invocation2
                    if first_invocation2:
                        first_invocation2 = False
                        assert False


            class TestInnerB:
                def test_flaky(self):
                    global first_invocation3
                    if first_invocation3:
                        first_invocation3 = False
                        assert False
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['TestClass', 'TestInnerA', 'test_flaky']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('TestClass', 'test_flaky'), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                    (('TestClass', 'TestInnerA', 'test_flaky'), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                    (('TestClass', 'TestInnerB', 'test_flaky'), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(
            num_flaky=2,
            num_quarantined=1,
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('TestClass', 'test_flaky')): ['fail', 'pass'],
            ('test_input.py', ('TestClass', 'TestInnerA', 'test_flaky')): ['quarantined', 'pass'],
            ('test_input.py', ('TestClass', 'TestInnerB', 'test_flaky')): ['fail', 'pass'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_unittest_all_statuses(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    # NB: We don't support unittest subtests, which relies on the pytest-subtests plugin, which in
    # turn conflicts with this plugin by using its own `SubTestReport` class:
    # https://github.com/pytest-dev/pytest-subtests/blob/d82e4e29df557cd940d97634a7285dd939f2bdea/pytest_subtests.py#L34.
    # We could probably overcome this limitation with some additional work to provide first-class
    # support for subtests.
    pytester.makepyfile(test_input="""
        import pytest
        import unittest

        first_invocation = True


        class AllStatuses(unittest.TestCase):
            def test_fail(self):
                assert False


            def test_quarantined(self):
                assert False


            def test_flaky(self):
                global first_invocation
                if first_invocation:
                    first_invocation = False
                    assert False


            def test_pass(self):
                pass


            @pytest.mark.skip
            def test_skipped(self):
                assert False
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['AllStatuses', 'test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('AllStatuses', 'test_fail'), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                    ]),
                    (('AllStatuses', 'test_flaky'), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                    (('AllStatuses', 'test_pass'), [_TestAttemptOutcome.PASSED]),
                    (('AllStatuses', 'test_quarantined'), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                    ]),
                    (('AllStatuses', 'test_skipped'), [_TestAttemptOutcome.SKIPPED]),
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(
            num_failed=1,
            num_flaky=1,
            num_passed=1,
            num_quarantined=1,
            num_skipped=1,
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('AllStatuses', 'test_fail')): ['fail', 'fail', 'fail'],
            ('test_input.py', ('AllStatuses', 'test_quarantined')): [
                'quarantined', 'quarantined', 'quarantined'],
            ('test_input.py', ('AllStatuses', 'test_flaky')): ['fail', 'pass'],
            ('test_input.py', ('AllStatuses', 'test_pass')): ['pass'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_multiple_files(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(
        test_input1="""
            def test_pass():
                pass
        """,
        test_input2="""
            def test_quarantined():
                raise RuntimeError()
        """,
    )

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input2.py',
                'name': ['test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input1.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                ],
            ),
            (
                'test_input2.py',
                [
                    (('test_quarantined',), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                    ])
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_passed=1, num_quarantined=1),
        expected_uploaded_test_runs={
            ('test_input1.py', ('test_pass',)): ['pass'],
            ('test_input2.py', ('test_quarantined',)): ['quarantined', 'quarantined',
                                                        'quarantined'],
        },
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_quarantine_mode_ignore_failures(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass


        def test_quarantined():
            raise RuntimeError()
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_quarantined',), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                    ])
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_passed=1, num_quarantined=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
            ('test_input.py', ('test_quarantined',)): ['quarantined', 'quarantined', 'quarantined'],
        },
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_quarantine_mode_no_quarantine(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass


        def test_quarantined():
            raise RuntimeError()
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_quarantined',), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                    ])
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_passed=1, num_failed=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
            ('test_input.py', ('test_quarantined',)): ['fail', 'fail', 'fail'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=['--quarantine-mode', 'no_quarantine'] + (XDIST_ARGS if xdist else []),
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_quarantine_mode_skip_tests(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass


        def test_quarantined():
            raise RuntimeError()
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_quarantined',), [_TestAttemptOutcome.SKIPPED]),
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_passed=1, num_skipped=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
        },
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=['--quarantine-mode', 'skip_tests'] + (XDIST_ARGS if xdist else []),
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_parameterized(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        @pytest.mark.parametrize('p', ['foo', 'bar'])
        def test_with_param(p):
            assert False
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_with_param[foo]']
            }
        ]},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_with_param[foo]',), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                    ]),
                    (('test_with_param[bar]',), [
                        _TestAttemptOutcome.FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                        _TestAttemptOutcome.RETRY_FAILED,
                    ]),
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_failed=1, num_quarantined=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_with_param[foo]',)): [
                'quarantined',
                'quarantined',
                'quarantined'
            ],
            ('test_input.py', ('test_with_param[bar]',)): ['fail', 'fail', 'fail'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_empty_collection(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input='')
    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[],
        expected_test_result_counts=_TestResultCounts(),
        expected_uploaded_test_runs={},
        expected_exit_code=ExitCode.NO_TESTS_COLLECTED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_all_skipped(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        @pytest.mark.skip
        def test_skipped():
            pass
    """)
    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[],
        expected_test_result_counts=_TestResultCounts(num_skipped=1),
        expected_uploaded_test_runs={},
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_skipped_and_pass(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_pass():
            pass


        @pytest.mark.skip
        def test_skipped():
            pass
    """)
    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')
    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_skipped',), [_TestAttemptOutcome.SKIPPED]),
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_passed=1, num_skipped=1),
        expected_uploaded_test_runs={('test_input.py', ('test_pass',)): ['pass']},
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_git_detached_head(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT', is_detached_head=True)

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_pass',), [_TestAttemptOutcome.PASSED])])],
        expected_test_result_counts=_TestResultCounts(num_passed=1),
        expected_uploaded_test_runs={('test_input.py', ('test_pass',)): ['pass']},
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_no_git_repo(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass
    """)

    subprocess_mock.update(branch=None, commit=None)

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_pass',), [_TestAttemptOutcome.PASSED])])],
        expected_test_result_counts=_TestResultCounts(num_passed=1),
        expected_uploaded_test_runs={('test_input.py', ('test_pass',)): ['pass']},
        expected_exit_code=ExitCode.OK,
        expected_branch=None,
        expected_commit=None,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_no_git_auto_detect(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_pass',), [_TestAttemptOutcome.PASSED])])],
        expected_test_result_counts=_TestResultCounts(num_passed=1),
        expected_uploaded_test_runs={('test_input.py', ('test_pass',)): ['pass']},
        expected_exit_code=ExitCode.OK,
        expected_branch=None,
        expected_commit=None,
        expect_xdist=xdist,
        extra_args=['--no-git-auto-detect'] + (XDIST_ARGS if xdist else []),
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_git_cli_args(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass
    """)

    subprocess_mock.update(branch=None, commit=None)

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_pass',), [_TestAttemptOutcome.PASSED])])],
        expected_test_result_counts=_TestResultCounts(num_passed=1),
        expected_uploaded_test_runs={('test_input.py', ('test_pass',)): ['pass']},
        expected_exit_code=ExitCode.OK,
        expected_branch='CLI_BRANCH',
        expected_commit='CLI_COMMIT',
        expect_xdist=xdist,
        extra_args=[
                       '--branch', 'CLI_BRANCH', '--commit', 'CLI_COMMIT'
                   ] + (XDIST_ARGS if xdist else []),
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_no_retries(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        first_invocation = True


        def test_flaky():
            global first_invocation
            if first_invocation:
                first_invocation = False
                assert False
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_flaky',), [_TestAttemptOutcome.FAILED])]),
        ],
        expected_test_result_counts=_TestResultCounts(num_failed=1),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_flaky',)): ['fail'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        verbose=verbose,
        expect_xdist=xdist,
        extra_args=['--failure-retries', '0'] + (XDIST_ARGS if xdist else [])
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_api_key_environ(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        monkeypatch: MonkeyPatch,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_api_key='API_KEY_FROM_ENVIRON',
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_pass',), [_TestAttemptOutcome.PASSED])])],
        expected_test_result_counts=_TestResultCounts(num_passed=1),
        expected_uploaded_test_runs={('test_input.py', ('test_pass',)): ['pass']},
        expected_exit_code=ExitCode.OK,
        monkeypatch=monkeypatch,
        env_vars={'UNFLAKABLE_API_KEY': 'API_KEY_FROM_ENVIRON'},
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_plugin_disabled(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        first_invocation = True


        def test_flaky():
            global first_invocation
            if first_invocation:
                first_invocation = False
                assert False


        def test_pass():
            pass
    """)

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [
                (('test_flaky',), [_TestAttemptOutcome.FAILED]),
                (('test_pass',), [_TestAttemptOutcome.PASSED]),
            ])
        ],
        expected_test_result_counts=_TestResultCounts(num_failed=1, num_passed=1),
        expected_uploaded_test_runs={},
        expected_exit_code=ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        plugin_enabled=False,
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_no_upload_results(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        def test_pass():
            pass


        def test_quarantined():
            raise RuntimeError()
    """)

    manifest: _api.TestSuiteManifest = {
        'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_quarantined']
            }
        ]
    }

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_quarantined',), [
                        _TestAttemptOutcome.QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                        _TestAttemptOutcome.RETRY_QUARANTINED,
                    ])
                ],
            ),
        ],
        expected_test_result_counts=_TestResultCounts(num_passed=1, num_quarantined=1),
        expected_uploaded_test_runs=None,
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=['--no-upload-results'] + (XDIST_ARGS if xdist else []),
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_select_subset(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_pass():
            pass


        def test_skipped():
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                ],
            ),
        ],
        expected_test_result_counts=(
            _TestResultCounts(num_passed=1)
            if xdist else _TestResultCounts(num_deselected=1, num_passed=1)),
        expected_uploaded_test_runs={('test_input.py', ('test_pass',)): ['pass']},
        expected_exit_code=ExitCode.OK,
        verbose=verbose,
        expect_xdist=xdist,
        extra_args=['-k', 'test_pass'] + (XDIST_ARGS if xdist else []),
    )


@pytest.mark.parametrize(
    ['verbose'],
    [pytest.param(False, id='not_verbose'), pytest.param(True, id='verbose')],
)
def test_collect_only(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_pass():
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[],
        expected_test_result_counts=_TestResultCounts(num_collected=1),
        expected_uploaded_test_runs=None,
        expected_exit_code=ExitCode.OK,
        verbose=verbose,
        extra_args=['--collect-only'],
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_NAMES,
                         TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_VALUES)
def test_setup_failure(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_pass():
            pass


        @pytest.fixture
        def setup_fail():
            raise RuntimeError('setup failure')


        def test_setup_fail(setup_fail):
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_setup_fail']
            }
        ] if quarantined else []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_setup_fail',), [
                        _TestAttemptOutcome.ERROR_QUARANTINED,
                        _TestAttemptOutcome.RETRY_ERROR_QUARANTINED,
                        _TestAttemptOutcome.RETRY_ERROR_QUARANTINED,
                    ] if quarantined else [
                        _TestAttemptOutcome.ERROR,
                        _TestAttemptOutcome.RETRY_ERROR,
                        _TestAttemptOutcome.RETRY_ERROR,
                    ]),
                ],
            ),
        ],
        expected_test_result_counts=(
            _TestResultCounts(num_passed=1, num_quarantined=1)
            if quarantined else _TestResultCounts(num_passed=1, num_error=1)
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
            ('test_input.py', ('test_setup_fail',)): [
                'quarantined', 'quarantined', 'quarantined',
            ] if quarantined else ['fail', 'fail', 'fail'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_NAMES,
                         TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_VALUES)
def test_setup_flaky(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_pass():
            pass


        first_invocation = True


        @pytest.fixture
        def setup_flaky():
            global first_invocation
            if first_invocation:
                first_invocation = False
                raise RuntimeError('setup failure')


        def test_setup_flaky(setup_flaky):
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_setup_flaky']
            }
        ] if quarantined else []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_setup_flaky',), [
                        _TestAttemptOutcome.ERROR_QUARANTINED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ] if quarantined else [
                        _TestAttemptOutcome.ERROR,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                ],
            ),
        ],
        expected_test_result_counts=(
            _TestResultCounts(num_passed=1, num_quarantined=1)
            if quarantined else _TestResultCounts(num_passed=1, num_flaky=1)
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
            ('test_input.py', ('test_setup_flaky',)): [
                'quarantined', 'pass',
            ] if quarantined else ['fail', 'pass'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_NAMES,
                         TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_VALUES)
def test_teardown_failure(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_pass():
            pass


        @pytest.fixture
        def teardown_fail():
            yield
            raise RuntimeError('teardown failure')


        def test_teardown_fail(teardown_fail):
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_teardown_fail']
            }
        ] if quarantined else []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_teardown_fail',), [
                        # PyTest prints a pass after the `call` phase passes, and then emits an
                        # error outcome if the `teardown` phase fails.
                        _TestAttemptOutcome.PASSED,
                        _TestAttemptOutcome.ERROR_QUARANTINED,
                        _TestAttemptOutcome.RETRY_PASSED,
                        _TestAttemptOutcome.RETRY_ERROR_QUARANTINED,
                        _TestAttemptOutcome.RETRY_PASSED,
                        _TestAttemptOutcome.RETRY_ERROR_QUARANTINED,
                    ] if quarantined else [
                        _TestAttemptOutcome.PASSED,
                        _TestAttemptOutcome.ERROR,
                        _TestAttemptOutcome.RETRY_PASSED,
                        _TestAttemptOutcome.RETRY_ERROR,
                        _TestAttemptOutcome.RETRY_PASSED,
                        _TestAttemptOutcome.RETRY_ERROR,
                    ]),
                ],
            ),
        ],
        expected_test_result_counts=(
            # PyTest double-counts tests in which teardown fails (even when the plugin is disabled).
            _TestResultCounts(num_passed=2, num_quarantined=1)
            if quarantined else _TestResultCounts(num_passed=2, num_error=1)
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
            ('test_input.py', ('test_teardown_fail',)): [
                'quarantined', 'quarantined', 'quarantined',
            ] if quarantined else ['fail', 'fail', 'fail'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_NAMES,
                         TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_VALUES)
def test_teardown_flaky(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        def test_pass():
            pass


        first_invocation = True


        @pytest.fixture
        def teardown_flaky():
            yield
            global first_invocation
            if first_invocation:
                first_invocation = False
                raise RuntimeError('teardown failure')


        def test_teardown_flaky(teardown_flaky):
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_teardown_flaky']
            }
        ] if quarantined else []},
        expected_test_file_outcomes=[
            (
                'test_input.py',
                [
                    (('test_pass',), [_TestAttemptOutcome.PASSED]),
                    (('test_teardown_flaky',), [
                        _TestAttemptOutcome.PASSED,
                        _TestAttemptOutcome.ERROR_QUARANTINED,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ] if quarantined else [
                        _TestAttemptOutcome.PASSED,
                        _TestAttemptOutcome.ERROR,
                        _TestAttemptOutcome.RETRY_PASSED,
                    ]),
                ],
            ),
        ],
        expected_test_result_counts=(
            # PyTest double-counts tests in which teardown fails (even when the plugin is disabled).
            _TestResultCounts(num_passed=2, num_quarantined=1)
            if quarantined else _TestResultCounts(num_passed=2, num_flaky=1)
        ),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
            ('test_input.py', ('test_teardown_flaky',)): [
                'quarantined', 'pass',
            ] if quarantined else ['fail', 'pass'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_xfail_pass(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        @pytest.mark.xfail
        def test_xfail():
            assert False
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_xfail',), [_TestAttemptOutcome.XFAILED])]),
        ],
        expected_test_result_counts=(_TestResultCounts(num_xfailed=1)),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_xfail',)): ['pass'],
        },
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_XDIST_ARG_NAMES, TEST_PARAMS_VERBOSE_XDIST_ARG_VALUES)
def test_xfail_fail(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        @pytest.mark.xfail
        def test_xfail():
            # Should fail, but doesn't.
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': []},
        expected_test_file_outcomes=[
            ('test_input.py', [
                (('test_xfail',), [_TestAttemptOutcome.XPASSED])
            ]),
        ],
        expected_test_result_counts=(_TestResultCounts(num_xpassed=1)),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_xfail',)): ['pass'],
        },
        # "Both XFAIL and XPASS don’t fail the test suite by default." See
        # https://docs.pytest.org/en/latest/how-to/skipping.html#strict-parameter.
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_NAMES,
                         TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_VALUES)
def test_xfail_fail_strict(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        @pytest.mark.xfail(strict=True)
        def test_xfail():
            # Should fail, but doesn't.
            pass
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_xfail']
            }
        ] if quarantined else []},
        expected_test_file_outcomes=[
            ('test_input.py', [
                (('test_xfail',), [
                    _TestAttemptOutcome.QUARANTINED,
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                ] if quarantined else [
                    _TestAttemptOutcome.FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                ])
            ]),
        ],
        expected_test_result_counts=(
            _TestResultCounts(num_quarantined=1)
            if quarantined else _TestResultCounts(num_failed=1)),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_xfail',)): [
                'quarantined', 'quarantined', 'quarantined']
            if quarantined else ['fail', 'fail', 'fail'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_NAMES,
                         TEST_PARAMS_VERBOSE_QUARANTINED_XDIST_ARG_VALUES)
def test_xfail_flaky_strict(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import pytest


        first_invocation = True


        @pytest.mark.xfail(strict=True)
        def test_xfail():
            global first_invocation
            if first_invocation:
                first_invocation = False
                # Should fail, but doesn't.
            else:
                assert False
    """)

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest={'quarantined_tests': [
            {
                'test_id': 'MOCK_TEST_ID',
                'filename': 'test_input.py',
                'name': ['test_xfail']
            }
        ] if quarantined else []},
        expected_test_file_outcomes=[
            ('test_input.py', [
                (('test_xfail',), [
                    _TestAttemptOutcome.QUARANTINED,
                    _TestAttemptOutcome.RETRY_XFAILED,
                ] if quarantined else [
                    _TestAttemptOutcome.FAILED,
                    _TestAttemptOutcome.RETRY_XFAILED,
                ])
            ]),
        ],
        expected_test_result_counts=(
            _TestResultCounts(num_quarantined=1)
            if quarantined else _TestResultCounts(num_flaky=1)),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_xfail',)): [
                'quarantined', 'pass']
            if quarantined else ['fail', 'pass'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.TESTS_FAILED,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
        verbose=verbose,
    )


@pytest.mark.parametrize(TEST_PARAMS_XDIST_ARG_NAMES, TEST_PARAMS_XDIST_ARG_VALUES)
def test_warnings(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        xdist: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import warnings


        def test_pass():
            warnings.warn('warning1')
            warnings.warn('warning2')
    """)

    manifest: _api.TestSuiteManifest = {'quarantined_tests': []}

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            ('test_input.py', [(('test_pass',), [_TestAttemptOutcome.PASSED])]),
        ],
        expected_test_result_counts=_TestResultCounts(num_passed=1, num_warnings=2),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass',)): ['pass'],
        },
        expected_exit_code=ExitCode.OK,
        expect_xdist=xdist,
        extra_args=XDIST_ARGS if xdist else [],
    )


@pytest.mark.parametrize(
    ['verbose', 'quarantined'],
    [
        pytest.param(False, False, id='not_verbose-not_quarantined'),
        pytest.param(False, True, id='not_verbose-quarantined'),
        pytest.param(True, False, id='verbose-not_quarantined'),
        pytest.param(True, True, id='verbose-quarantined'),
    ],
)
def test_stepwise(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
) -> None:
    pytester.makepyfile(test_input="""
        import warnings


        def test_pass1():
            pass


        def test_fail():
            assert False


        def test_pass2():
            pass
    """)

    manifest: _api.TestSuiteManifest = {'quarantined_tests': [
        {
            'test_id': 'MOCK_TEST_ID',
            'filename': 'test_input.py',
            'name': ['test_fail']
        }
    ] if quarantined else []}

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            ('test_input.py', [
                (('test_pass1',), [_TestAttemptOutcome.PASSED]),
                (('test_fail',), [
                    _TestAttemptOutcome.QUARANTINED,
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                ] if quarantined else [
                    _TestAttemptOutcome.FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                ]),
            ]),
        ],
        expected_test_result_counts=(
            _TestResultCounts(num_passed=1, num_quarantined=1)
            if quarantined else _TestResultCounts(num_passed=1, num_failed=1)),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_pass1',)): ['pass'],
            ('test_input.py', ('test_fail',)): [
                'quarantined', 'quarantined', 'quarantined'
            ] if quarantined else ['fail', 'fail', 'fail'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.INTERRUPTED,
        verbose=verbose,
        extra_args=['--stepwise'],
        expect_progress=False,
    )

    requests_mock.reset_mock()

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            ('test_input.py', [
                (('test_fail',), [
                    _TestAttemptOutcome.QUARANTINED,
                    # Same issue as below, even if the failure is quarantined.
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                ] if quarantined else [
                    _TestAttemptOutcome.FAILED,
                    # KNOWN LIMITATION: PyTest's StepwisePlugin skips the first failure when
                    # --stepwise-skip is used, but then the retry failure is treated as a second
                    # failure and causes PyTest to stop executing. We can probably live with this
                    # for now since our plugin is less likely to be used for interactive runs
                    # outside of CI, which is where --stepwise is most likely to be used.
                    _TestAttemptOutcome.RETRY_FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                ]),
            ]),
        ],
        expected_test_result_counts=(
            _TestResultCounts(num_quarantined=1, num_deselected=1)
            if quarantined else _TestResultCounts(num_failed=1, num_deselected=1)),
        expected_uploaded_test_runs={
            ('test_input.py', ('test_fail',)): [
                'quarantined', 'quarantined', 'quarantined'
            ] if quarantined else ['fail', 'fail', 'fail'],
        },
        expected_exit_code=ExitCode.OK if quarantined else ExitCode.INTERRUPTED,
        verbose=verbose,
        extra_args=['--stepwise', '--stepwise-skip'],
        expect_progress=False,
    )


@pytest.mark.skipif(os.environ.get('TEST_XDIST') != '1', reason='xdist is disabled')
@pytest.mark.parametrize(
    ['verbose', 'quarantined'],
    [
        pytest.param(False, False, id='not_verbose-not_quarantined'),
        pytest.param(False, True, id='not_verbose-quarantined'),
        pytest.param(True, False, id='verbose-not_quarantined'),
        pytest.param(True, True, id='verbose-quarantined'),
    ],
)
def test_xdist(
        pytester: pytest.Pytester,
        requests_mock: requests_mock.Mocker,
        subprocess_mock: GitMock,
        verbose: bool,
        quarantined: bool,
) -> None:
    pytester.makepyfile(test_input1="""
        import pytest


        def test_fail():
            assert False


        def test_quarantined():
            assert False
    """,
                        test_input2="""
        import pytest


        first_invocation = True


        def test_flaky():
            global first_invocation
            if first_invocation:
                first_invocation = False
                assert False


        def test_pass():
            pass


        @pytest.mark.skip
        def test_skipped():
            assert False
    """)

    manifest: _api.TestSuiteManifest = {'quarantined_tests': [
        {
            'test_id': 'MOCK_TEST_ID',
            'filename': 'test_input1.py',
            'name': ['test_quarantined']
        }
    ] if quarantined else []}

    subprocess_mock.update(branch='MOCK_BRANCH', commit='MOCK_COMMIT')

    run_test_case(
        pytester,
        requests_mock,
        manifest,
        expected_test_file_outcomes=[
            ('test_input1.py', [
                (('test_fail',), [
                    _TestAttemptOutcome.FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                ]),
                (('test_quarantined',), [
                    _TestAttemptOutcome.QUARANTINED,
                    # Same issue as below, even if the failure is quarantined.
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                    _TestAttemptOutcome.RETRY_QUARANTINED,
                ] if quarantined else [
                    _TestAttemptOutcome.FAILED,
                    # KNOWN LIMITATION: PyTest's StepwisePlugin skips the first failure when
                    # --stepwise-skip is used, but then the retry failure is treated as a second
                    # failure and causes PyTest to stop executing. We can probably live with this
                    # for now since our plugin is less likely to be used for interactive runs
                    # outside of CI, which is where --stepwise is most likely to be used.
                    _TestAttemptOutcome.RETRY_FAILED,
                    _TestAttemptOutcome.RETRY_FAILED,
                ]),
            ]),
            ('test_input2.py', [
                (('test_flaky',), [
                    _TestAttemptOutcome.FAILED,
                    _TestAttemptOutcome.RETRY_PASSED,
                ]),
                (('test_pass',), [_TestAttemptOutcome.PASSED]),
                (('test_skipped',), [_TestAttemptOutcome.SKIPPED]),
            ])
        ],
        expected_test_result_counts=(
            _TestResultCounts(
                num_failed=1,
                num_flaky=1,
                num_passed=1,
                num_quarantined=1,
                num_skipped=1,
            )
            if quarantined else _TestResultCounts(
                num_failed=2,
                num_flaky=1,
                num_passed=1,
                num_skipped=1,
            )),
        expected_uploaded_test_runs={
            ('test_input1.py', ('test_fail',)): ['fail', 'fail', 'fail'],
            ('test_input1.py', ('test_quarantined',)): [
                'quarantined', 'quarantined', 'quarantined'] if quarantined else [
                'fail', 'fail', 'fail'],
            ('test_input2.py', ('test_flaky',)): ['fail', 'pass'],
            ('test_input2.py', ('test_pass',)): ['pass'],
        },
        expected_exit_code=ExitCode.TESTS_FAILED,
        verbose=verbose,
        # Run on 2 CPUs.
        extra_args=['-n', '2'],  # , '--debug'],
        expect_xdist=True,
    )
