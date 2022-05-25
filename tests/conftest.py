from typing import Generator
from unittest.mock import patch

import pytest

from tests.common import GitMock

pytest_plugins = 'pytester'


@pytest.fixture()
def subprocess_mock() -> Generator[GitMock, None, None]:
    mock_instance = GitMock()
    # We need to mock the imported instance of subprocess.run. See
    # https://docs.python.org/3/library/unittest.mock.html#where-to-patch.
    with patch(
            'pytest_unflakable._git.run',
            side_effect=mock_instance.mock_run
    ):
        yield mock_instance
