"""Plugin implementation."""
#  Copyright (c) 2022 Developer Innovations, LLC

from enum import Enum
from pathlib import Path
from typing import (
    Any, Dict, List, Union, Tuple, Mapping, Optional, cast, Generator, Set, TYPE_CHECKING
)

import logging
import pprint

import pytest
import _pytest
import sys
from time import time
from datetime import datetime, timezone

from ._api import (
    get_test_suite_manifest,
    create_test_suite_run,
    build_test_suite_run_url,
    CreateTestSuiteRunRequest,
    TestRunAttemptRecord,
    TestRunRecord, TestAttemptResult, TestSuiteManifest,
)

if TYPE_CHECKING:
    import py
    from typing import Literal

    # Most of these types aren't defined or exported in older versions of pytest, but we only need
    # the type checking to work on newer versions.
    CallInfo = pytest.CallInfo[None]
    CallPhase = Literal['setup', 'call', 'teardown']
    CollectReport = pytest.CollectReport
    Config = pytest.Config
    PathCompat = Union[py.path.local, Path]
else:
    CallInfo = object
    CallPhase = str
    CollectReport = object
    Config = object
    PathCompat = object

TestPathAndName = Tuple[str, Tuple[str, ...]]
QuarantinedTests = Set[TestPathAndName]


class QuarantineMode(Enum):
    IGNORE_FAILURES = 1
    NO_QUARANTINE = 2
    SKIP_TESTS = 3


def _ts_to_rfc3339(ts_sec: float) -> str:
    return datetime.fromtimestamp(ts_sec, timezone.utc).isoformat('T', 'microseconds')


# Support both pre-7.0 and post-7.0 versions of PyTest.
def node_path(session: Union[pytest.Item, pytest.Session]) -> PathCompat:
    if hasattr(session, 'path'):
        return session.path
    else:
        return session.fspath


def relative_to(path: PathCompat, base: PathCompat) -> str:
    if hasattr(path, 'relative_to'):
        return str(path.relative_to(base))  # type: ignore
    else:
        return str(path.relto(base))  # type: ignore


def item_name(item: _pytest.nodes.Node) -> Tuple[str, ...]:
    if isinstance(item, pytest.Module):
        return tuple()
    elif item.parent is None:
        return (item.name,)
    elif isinstance(item, pytest.Class) or isinstance(item, pytest.Function):
        return item_name(item.parent) + (item.name,)
    else:
        # Prior to PyTest 7.0, there was a node in between Class and Function called Instance. Its
        # name is `()`, which doesn't add any value, so we just filter it out.
        return item_name(item.parent)


class UnflakableReport(_pytest.reports.BaseReport):
    unflakable_attempt: int
    unflakable_is_quarantined: bool
    unflakable_prior_non_teardown_failures: int
    unflakable_start_time: float
    unflakable_end_time: float
    unflakable_test_name: Tuple[str, ...]
    unflakable_filename: str

    # The PyTest TerminalReporter emits error messages for `failed` and `error` categories, but
    # not for unknown categories like `flaky` and `quarantined`. To ensure that errors get logged
    # properly, we generate two reports for flaky/quarantined test:
    #   1. One for logging with category `failed` or `error` and count_towards_summary() returning
    #      False. The TerminalReporter will log the corresponding errors, but this report will
    #      not contribute toward the summary stats at the end. For this one,
    #      `unflakable_fake_report_for_logging` is True.
    #   2. One with category `flaky`/`quarantined` that contributes to the summary stats but
    #      doesn't trigger any additional logging of errors. For this one,
    #      `unflakable_fake_report_for_logging` is False.
    unflakable_fake_report_for_logging: bool

    def __init__(
            self,
            unflakable_attempt: int,
            unflakable_is_quarantined: bool,
            unflakable_prior_non_teardown_failures: int,
            unflakable_start_time: float,
            unflakable_end_time: float,
            unflakable_test_name: Tuple[str, ...],
            unflakable_filename: str,
            unflakable_fake_report_for_logging: bool = False,
            **kw: Any
    ) -> None:
        super().__init__(
            unflakable_attempt=unflakable_attempt,
            unflakable_is_quarantined=unflakable_is_quarantined,
            unflakable_prior_non_teardown_failures=unflakable_prior_non_teardown_failures,
            unflakable_start_time=unflakable_start_time,
            unflakable_end_time=unflakable_end_time,
            unflakable_test_name=unflakable_test_name,
            unflakable_filename=unflakable_filename,
            unflakable_fake_report_for_logging=unflakable_fake_report_for_logging,
            **kw,
        )

    def __repr__(self) -> str:
        return '<UnflakableReport {!r} when={!r} outcome={!r}>'.format(
            self.nodeid, self.when, self.outcome
        )

    def to_fake_for_logging(self) -> 'UnflakableReport':
        assert not self.unflakable_fake_report_for_logging
        copy = UnflakableReport(**self.__dict__)
        copy.unflakable_fake_report_for_logging = True
        return copy

    @property
    def count_towards_summary(self) -> bool:
        return not self.unflakable_fake_report_for_logging
        # if self.failed and self.unflakable_is_quarantined and self.unflakable_attempt > 0:
        #     return False
        # return True


ItemReports = Dict[CallPhase, UnflakableReport]


class UnflakablePlugin:
    api_key: str
    base_url: Optional[str]
    branch: Optional[str]
    commit: Optional[str]
    failure_retries: int
    insecure_disable_tls_validation: bool
    quarantine_mode: QuarantineMode
    test_suite_id: str
    upload_results: bool
    logger: logging.Logger

    is_xdist_worker: bool
    item_reports: Dict[TestPathAndName, List[ItemReports]]
    manifest: Optional[TestSuiteManifest]
    quarantined_tests: Set[TestPathAndName]
    non_teardown_failures: Set[str]
    session: Optional[pytest.Session]
    num_tests_quarantined: int
    end_time: float
    start_time: float

    def __init__(
            self,
            api_key: str,
            base_url: Optional[str],
            branch: Optional[str],
            commit: Optional[str],
            failure_retries: int,
            insecure_disable_tls_validation: bool,
            quarantine_mode: QuarantineMode,
            test_suite_id: str,
            upload_results: bool,
            logger: logging.Logger,
            worker_manifest: Optional[TestSuiteManifest],
            is_xdist_worker: bool,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.branch = branch
        self.commit = commit
        self.failure_retries = failure_retries
        self.insecure_disable_tls_validation = insecure_disable_tls_validation
        self.quarantine_mode = quarantine_mode
        self.test_suite_id = test_suite_id
        self.upload_results = upload_results
        self.logger = logger

        self.is_xdist_worker = is_xdist_worker
        self.item_reports = {}

        self.manifest = worker_manifest
        if self.manifest is None:
            try:
                self.manifest = get_test_suite_manifest(
                    test_suite_id=self.test_suite_id,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    insecure_disable_tls_validation=self.insecure_disable_tls_validation,
                    logger=self.logger,
                )
            # IOError is the base class for `requests.RequestException`.
            except IOError as e:
                sys.stderr.write(
                    ('ERROR: Failed to get Unflakable manifest: %s\nTest failures will NOT be'
                     ' quarantined.\n') % (repr(e))),
        else:
            logger.debug(
                f'xdist worker received manifest for test suite {self.test_suite_id}: '
                f'{pprint.pformat(self.manifest)}'
            )

        self.quarantined_tests = set([
            (quarantined_test['filename'], tuple(quarantined_test['name'])) for
            quarantined_test
            in self.manifest['quarantined_tests']
        ]) if self.manifest is not None else set()

        self.non_teardown_failures = set()
        self.session = None
        self.num_tests_quarantined = 0
        self.end_time = 0.
        self.start_time = 0.

    # Lets us filter out quarantined tests when `quarantine_mode` is `skip_tests`.
    def pytest_collection_modifyitems(
            self,
            session: pytest.Session,
            config: Config,
            items: List[pytest.Item],
    ) -> None:
        self.logger.debug('called hook pytest_collection_modifyitems')
        for idx, item in enumerate(items):
            test_path = relative_to(node_path(item), node_path(session))
            test_name = item_name(item)
            if (test_path, test_name) in self.quarantined_tests:
                self.logger.info(
                    'test `%s` in file %s is quarantined', '.'.join(test_name), test_path
                )
                if self.quarantine_mode == QuarantineMode.SKIP_TESTS:
                    item.add_marker(pytest.mark.skip)
            else:
                self.logger.debug('test `%s` in file %s is not quarantined', test_name, test_path)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem: Optional[pytest.Item]) -> bool:
        self.logger.debug(f'called hook pytest_runtest_protocol: {item.nodeid}')
        ihook = item.ihook
        ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)

        assert self.session
        test_filename = relative_to(node_path(item), node_path(self.session))
        test_name = item_name(item)
        for attempt in range(item.config.option.unflakable_failure_retries + 1):
            if attempt > 0:
                self.logger.info(
                    'retrying test `%s` in file %s (attempt %d of %d)',
                    '.'.join(test_name),
                    relative_to(node_path(item), node_path(item.session)),
                    attempt + 1,
                    item.config.option.unflakable_failure_retries + 1,
                )

            # It's not ideal to use this protected function directly, but it's the only way to
            # avoid calling the pytest_runtest_logstart() and pytest_runtest_logfinish() hooks
            # multiple times for retried tests, which somewhat garbles the output. This is
            # similar to the approach that pytest_rerunfailures takes:
            # https://github.com/pytest-dev/pytest-rerunfailures/blob/e80c12eb9456b7646c3c6610b2c08420e9247cd4/pytest_rerunfailures.py#L525
            _pytest.runner.runtestprotocol(item, nextitem=nextitem)

            item_reports = self.item_reports.get((test_filename, test_name), [])
            if all([item_report.passed or item_report.skipped for item_report in
                    item_reports[-1].values()]):
                break

        ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
        return True

    def pytest_runtest_makereport(
            self,
            item: pytest.Item,
            call: CallInfo,
    ) -> UnflakableReport:
        # Older versions of PyTest don't export this from the main pytest package.
        report = _pytest.reports.TestReport.from_item_and_call(item, call)

        # NB: For xdist, this hook runs in the worker.
        self.logger.debug(
            f'called hook pytest_runtest_makereport: {item.nodeid} ({call.when}) - {report.outcome}'
        )

        if item.parent:
            pass

        test_filename = relative_to(node_path(item), node_path(item.session))
        test_name = item_name(item)
        is_quarantined = (test_filename, test_name) in self.quarantined_tests and (
                self.quarantine_mode == QuarantineMode.IGNORE_FAILURES)

        assert self.session

        item_reports = self.item_reports.get((test_filename, test_name), [])
        if len(item_reports) == 0:
            attempt = 0
        elif call.when in item_reports[-1]:
            attempt = len(item_reports)
        else:
            attempt = len(item_reports) - 1

        # NB: When running with xdist, this function only gets called on the workers, so we don't
        # update any plugin state until pytest_report_teststatus() gets called on the controller.

        unflakable_report = UnflakableReport(
            unflakable_attempt=attempt,
            unflakable_is_quarantined=is_quarantined,
            unflakable_prior_non_teardown_failures=item.nodeid in self.non_teardown_failures,
            unflakable_start_time=call.start,
            unflakable_end_time=call.stop,
            unflakable_test_name=test_name,
            unflakable_filename=test_filename,
            **report.__dict__,
        )
        return unflakable_report

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(
            self,
            report: UnflakableReport,
    ) -> None:
        # NB: This gets called on the xdist worker, which then proxies it to the controller and
        # calls it again there.
        self.logger.debug(
            f'called hook pytest_runtest_logreport {report.nodeid} ({report.when}) - '
            f'{report.outcome}'
        )

        # Recursive base case.
        if report.unflakable_fake_report_for_logging:
            return None

        # NB: We do this here and not in pytest_runtest_makereport() because the PyTest `skipping`
        # module has a pytest_runtest_makereport() wrapper hook for handling xfail strict and other
        # cases that modify the report. We need to act on the final version of the report after
        # those modifications.
        if report.failed and report.when != 'teardown':
            self.non_teardown_failures.add(report.nodeid)

        item_reports: List[ItemReports] = (
            self.item_reports.setdefault(
                (report.unflakable_filename, report.unflakable_test_name),
                [],
            )
        )
        if report.when == 'setup':
            item_reports.append({'setup': report})
        elif report.when in ['call', 'teardown'] and len(item_reports) > 0:
            item_reports[-1][cast(CallPhase, report.when)] = report

        if report.failed and not self.is_xdist_worker:
            assert self.session

            if report.unflakable_is_quarantined and (
                    self.quarantine_mode == QuarantineMode.IGNORE_FAILURES
            ):
                self.num_tests_quarantined += 1
                self.session.ihook.pytest_runtest_logreport(report=report.to_fake_for_logging())
            # If the last attempt fails, it'll get logged as a failure/error anyway, so we don't
            # need to create an extra report.
            elif report.unflakable_attempt < self.failure_retries:
                self.session.ihook.pytest_runtest_logreport(report=report.to_fake_for_logging())

    def pytest_report_teststatus(
            self,
            report: Union[CollectReport, UnflakableReport],
            config: Config,
    ) -> Optional[Tuple[str, str, Union[str, Tuple[str, Mapping[str, bool]]]]]:
        self.logger.debug(
            f'called hook pytest_report_teststatus: {report.nodeid} ({report.when}) - '
            f'{report.outcome}'
        )

        item_reports = self.item_reports.get(
            (report.unflakable_filename, report.unflakable_test_name), [])
        attempt = max(len(item_reports) - 1, 0)

        if report.failed:
            if report.unflakable_is_quarantined and (
                    self.quarantine_mode == QuarantineMode.IGNORE_FAILURES
            ):
                if report.unflakable_fake_report_for_logging:
                    return 'error' if report.when in ['setup', 'teardown'] else 'failed', '', ''
                return (
                    # Don't double-count quarantined tests; we already returned `quarantined` for
                    # the category during the first attempt, so return an empty string for retries
                    # to prevent the final stats from double-counting multiple runs of the same
                    # test.
                    'quarantined' if attempt == 0 else '',
                    'Q',
                    (
                        'ERROR (quarantined)' if attempt == 0 else 'ERROR (retry, quarantined)',
                        {'purple': True},
                    ) if report.when in ['setup', 'teardown'] else (
                        'FAILED (quarantined)' if attempt == 0 else 'FAILED (retry, quarantined)',
                        {'purple': True},
                    ),
                )
            elif report.when in ['setup', 'teardown']:
                if report.unflakable_fake_report_for_logging:
                    return 'error', '', ''
                return (
                    # If it's not the last retry attempt, we don't know yet whether the final
                    # outcome is failed or flaky, so we don't return a category.
                    'error' if attempt == self.failure_retries else '',
                    'E' if attempt == 0 else 'R',
                    'ERROR' if attempt == 0 else 'ERROR (retry)',
                )
            elif report.unflakable_fake_report_for_logging:
                return 'failed', '', ''
            else:
                return (
                    # If it's not the last retry attempt, we don't know yet whether the final
                    # outcome is failed or flaky, so we don't return a category.
                    'failed' if attempt == self.failure_retries else '',
                    'F' if attempt == 0 else 'R',
                    'FAILED' if attempt == 0 else 'FAILED (retry)',
                )
        elif (report.passed or (report.skipped and 'xfail' in report.keywords)) and (
                report.when == 'call' and attempt > 0):
            return (
                # Retry passed, so the test is flaky.
                (
                    # We returned the 'quarantined' category on the first failure, so return an
                    # empty string here to avoid double-counting.
                    '' if (report.unflakable_is_quarantined
                           and self.quarantine_mode == QuarantineMode.IGNORE_FAILURES) or (
                              # If only the `teardown` phase has failed in the past, then don't
                              # treat the test as flaky just because the `call` phase passed.
                              not report.unflakable_prior_non_teardown_failures)
                    else 'flaky'
                ),
                'R',
                ('PASSED (retry)', {'green': True}) if report.passed else (
                    'XFAIL (retry)', {'yellow': True}),
            )
        # There's an edge case where just the teardown is flaky, which we don't know until the
        # teardown phase passes on retry without a prior non-teardown failure.
        elif report.passed and report.when == 'teardown' and attempt > 0 and (
                not report.unflakable_is_quarantined or
                self.quarantine_mode != QuarantineMode.IGNORE_FAILURES
        ) and not report.unflakable_prior_non_teardown_failures:
            return (
                'flaky',
                '',
                '',
            )

        return None

    @pytest.hookimpl(tryfirst=True)
    def pytest_report_to_serializable(
            self,
            report: Union[CollectReport, UnflakableReport]
    ) -> Optional[Dict[str, Any]]:
        if isinstance(report, UnflakableReport):
            data = report._to_json()
            data['$report_type'] = report.__class__.__name__
            return data
        return None

    @pytest.hookimpl(tryfirst=True)
    def pytest_report_from_serializable(
            self,
            data: Dict[str, Any],
    ) -> Optional[Union[CollectReport, UnflakableReport]]:
        if data.get('$report_type') == 'UnflakableReport':
            return UnflakableReport._from_json(data)
        return None

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.logger.debug('called hook pytest_sessionstart')
        self.session = session
        self.start_time = time()

    # This is a `xdist.workermanage.WorkerController`, but pytest-xdist doesn't provide types.
    def pytest_configure_node(self, node: Any) -> None:
        """
        Hook called by pytest-xdist to configure each worker node.

        We leverage this hook to send the manifest to the worker.
        """
        nodeid = node.workerinput['workerid']
        self.logger.debug(f'called hook pytest_configure_node: {nodeid}')
        if self.manifest is not None:
            node.workerinput['unflakable_manifest'] = self.manifest

    def _build_test_suite_run_request(
            self,
            session: pytest.Session,
    ) -> CreateTestSuiteRunRequest:
        test_runs: List[TestRunRecord] = []
        for (test_filename, test_name), item_reports in self.item_reports.items():
            is_quarantined = (test_filename, test_name) in self.quarantined_tests
            item_attempts: List[TestRunAttemptRecord] = []

            for item_attempt_reports in item_reports:
                # Don't report skipped tests.
                if (
                        'setup' in item_attempt_reports
                        and item_attempt_reports['setup'].skipped
                        and ('call' not in item_attempt_reports
                             or item_attempt_reports['call'].skipped)
                        and 'teardown' in item_attempt_reports
                        and item_attempt_reports['teardown'].passed
                ):
                    continue

                setup_report = item_attempt_reports.get('setup')
                call_report = item_attempt_reports.get('call')
                teardown_report = item_attempt_reports.get('teardown')

                if teardown_report is not None:
                    end_time = teardown_report.unflakable_end_time
                elif call_report is not None:
                    end_time = call_report.unflakable_end_time
                elif setup_report is not None:
                    end_time = setup_report.unflakable_end_time
                else:
                    end_time = None

                if ('setup' in item_attempt_reports and item_attempt_reports['setup'].failed) or (
                        'call' in item_attempt_reports and item_attempt_reports['call'].failed or
                        'teardown' in item_attempt_reports and item_attempt_reports[
                            'teardown'].failed
                ):
                    attempt_result = (
                        cast(TestAttemptResult, 'quarantined')
                        if is_quarantined and (
                                self.quarantine_mode == QuarantineMode.IGNORE_FAILURES) else cast(
                            TestAttemptResult, 'fail')
                    )
                else:
                    attempt_result = cast(TestAttemptResult, 'pass')

                attempt: TestRunAttemptRecord = {
                    'start_time': (
                        _ts_to_rfc3339(setup_report.unflakable_start_time)
                        if setup_report is not None else None
                    ),
                    'end_time': _ts_to_rfc3339(end_time) if end_time is not None else None,
                    'duration_ms': int(
                        (
                                (
                                    item_attempt_reports['setup'].duration
                                    if 'setup' in item_attempt_reports else 0.
                                ) + (
                                    item_attempt_reports['call'].duration
                                    if 'call' in item_attempt_reports else 0.
                                ) + (
                                    item_attempt_reports['teardown'].duration
                                    if 'teardown' in item_attempt_reports else 0.
                                )
                        ) * 1000.
                    ),
                    'result': attempt_result,
                }
                item_attempts.append(attempt)

            if len(item_attempts) > 0:
                run_record: TestRunRecord = {
                    'filename': test_filename,
                    'name': list(test_name),
                    'attempts': item_attempts,
                }
                test_runs.append(run_record)

        request = {
            'start_time': _ts_to_rfc3339(self.start_time),
            'end_time': _ts_to_rfc3339(self.end_time),
            'test_runs': test_runs,
        }
        request.update(**({'branch': self.branch} if self.branch is not None else {}))
        request.update(**({'commit': self.commit} if self.commit is not None else {}))

        return cast(CreateTestSuiteRunRequest, request)

    # Allows us to override the exit code if all the failures are quarantined. We need this to be a
    # wrapper so that the default hook still gets invoked and prints the summary line with the test
    # category counts.
    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(
            self,
            session: pytest.Session,
            exitstatus: int,
    ) -> Generator[None, None, None]:
        self.end_time = time()
        self.logger.debug('called hook pytest_sessionfinish')
        yield
        self.logger.debug('resumed yielded hook pytest_sessionfinish')

        request = self._build_test_suite_run_request(session)

        if self.upload_results and len(request['test_runs']) > 0:
            try:
                run_summary = create_test_suite_run(
                    request=request,
                    test_suite_id=self.test_suite_id,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    insecure_disable_tls_validation=self.insecure_disable_tls_validation,
                    logger=self.logger,
                )
            except Exception as e:
                pytest.exit('ERROR: Failed to report results to Unflakable: %s\n' % (repr(e)), 1)
            else:
                print(
                    'Unflakable report: %s' % (
                        build_test_suite_run_url(
                            self.test_suite_id,
                            run_summary['run_id'],
                            self.base_url,
                        )),
                )

        # We multiply by 2 here because each quarantined test is double-counted by the Session: once
        # for the quarantined report, and once for the fake report that's used for logging errors.
        if session.testsfailed > 0 and session.testsfailed == self.num_tests_quarantined * 2:
            pytest.exit('All failed tests are quarantined', 0)
