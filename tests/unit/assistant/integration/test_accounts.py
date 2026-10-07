"""Accounts.py: the id prefixes derived from account names, the entries of an accounts list
with their names made unique, the per-account health mixin and the status clause."""
from unittest.mock import Mock

import pytest

from assistant.integration.Accounts import (AccountHealth, id_prefix, id_prefixes, named_entries, render_accounts_status)


@pytest.mark.unit
class TestIdPrefixes:
    def test_the_first_two_letters_uppercased(self):
        assert id_prefixes(["Personal", "work", "iCloud", "Gmail"]) == ["PE", "WO", "IC", "GM"]

    def test_a_collision_takes_the_initials_of_the_first_two_words(self):
        assert id_prefixes(["Work", "Wolf Mail"]) == ["WO", "WM"]

    def test_a_one_word_collision_takes_the_first_letter_and_a_digit(self):
        assert id_prefixes(["Work", "Wolf", "Woods"]) == ["WO", "W2", "W3"]

    def test_initials_already_taken_fall_through_to_the_digit(self):
        assert id_prefixes(["Work", "WM Shop", "Wolf Mail"]) == ["WO", "WM", "W2"]

    def test_punctuation_and_a_short_name_still_give_two_characters(self):
        assert id_prefix("@home", set()) == "HO"
        assert id_prefix("X", set()) == "XX"
        assert id_prefix("", set()) == "AC"


@pytest.mark.unit
class TestNamedEntries:
    def test_keeps_the_index_of_each_usable_entry(self):
        entries = [{"name": "Personal"}, {"name": ""}, {"preset": "gmail"}, {"name": "Work"}]
        assert [(i, e["name"]) for i, e in named_entries(entries, "imap")] == [(0, "Personal"), (3, "Work")]

    def test_a_repeated_name_whatever_its_case_or_spacing_keeps_the_first(self):
        entries = [{"name": "Work", "address": "a"}, {"name": " work ", "address": "b"}]
        (index, entry), = named_entries(entries, "imap")
        assert index == 0 and entry["address"] == "a"

    def test_anything_but_a_list_of_sections_is_no_entries(self):
        assert named_entries(None, "imap") == [] and named_entries("Personal", "imap") == []
        assert named_entries([42, "x"], "imap") == []


class _Holder(AccountHealth):
    def __init__(self, accounts):
        self.accounts = accounts

    def _health_accounts(self):
        return self.accounts


def _account(name, outcome=True):
    account = Mock()
    account.name = name
    if isinstance(outcome, Exception):
        account.check.side_effect = outcome
    else:
        account.check.return_value = outcome
    return account


@pytest.mark.unit
class TestAccountHealth:
    def test_all_passing_is_true_and_every_account_reads_ok(self):
        holder = _Holder([_account("Personal"), _account("Work")])
        assert holder.check_accounts(holder.accounts) is True
        assert [(a["name"], a["state"], a["last_error"]) for a in holder.accounts_status()] == [
            ("Personal", "ok", None), ("Work", "ok", None)]

    def test_a_failing_account_raises_with_its_name_and_reason_after_every_account_was_checked(self):
        work = _account("Work", ConnectionError("auth failed"))
        other = _account("Other", False)
        holder = _Holder([_account("Personal"), work, other])
        with pytest.raises(RuntimeError) as raised:
            holder.check_accounts(holder.accounts)
        message = str(raised.value)
        assert "Work: ConnectionError: auth failed" in message and "Other" in message and "Personal" not in message
        other.check.assert_called_once()
        assert [a["state"] for a in holder.accounts_status()] == ["ok", "error", "error"]

    def test_an_account_never_checked_is_unknown(self):
        assert [a["state"] for a in _Holder([_account("Personal")]).accounts_status()] == ["unknown"]


@pytest.mark.unit
def test_the_status_clause_names_each_account_with_its_verdict():
    text = render_accounts_status([{"name": "Personal", "state": "ok", "last_error": None},
                                   {"name": "Work", "state": "error", "last_error": "auth failed"},
                                   {"name": "New", "state": "unknown", "last_error": None}])
    personal, work, new = text.split(", ")
    assert personal == "Personal ok" and work == "Work: auth failed" and new.startswith("New")
