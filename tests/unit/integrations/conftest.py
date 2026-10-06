from unittest.mock import Mock

import pytest

from assistant.commands.Commands import Commands


@pytest.fixture
def commands_instance():
    """A Commands hosting the discovered command sets, on a command processor that looks
    fresh: a bare Mock's truthy attributes would make the background publish treat the
    reply as a pending error and fold it in."""
    mock_cp = Mock()
    mock_cp.command_reply = None
    mock_cp.command_reply_contains_error = False
    return Commands(mock_cp)
