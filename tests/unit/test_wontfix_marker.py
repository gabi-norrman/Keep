"""The `wontfix` marker of pytest.ini: a bug the maintainer has decided to leave, run as a
non-strict xfail by the unit conftest so neither the bug nor its disappearance fails the suite."""
from unittest.mock import MagicMock

import pytest

from tests.unit import conftest


def _item_with(marker):
    item = MagicMock()
    item.get_closest_marker.side_effect = lambda name: marker if name == "wontfix" else None
    return item


def _xfail_added_to(item):
    marks = [call.args[0] for call in item.add_marker.call_args_list]
    assert len(marks) == 1 and marks[0].name == "xfail"
    return marks[0]


@pytest.mark.unit
def test_a_wontfix_test_runs_as_an_xfail_that_is_not_strict():
    item = _item_with(pytest.mark.wontfix(reason="harmless, the fix would complicate the normal path").mark)
    conftest.pytest_itemcollected(item)
    xfail = _xfail_added_to(item)
    assert xfail.kwargs["strict"] is False
    assert "harmless" in xfail.kwargs["reason"]   # the verdict travels with the test


@pytest.mark.unit
def test_a_bare_wontfix_still_maps_rather_than_crashing_the_collection():
    item = _item_with(pytest.mark.wontfix.mark)
    conftest.pytest_itemcollected(item)
    assert _xfail_added_to(item).kwargs["strict"] is False


@pytest.mark.unit
def test_an_ordinary_test_is_left_alone():
    item = _item_with(None)
    conftest.pytest_itemcollected(item)
    item.add_marker.assert_not_called()
