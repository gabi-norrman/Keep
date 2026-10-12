from unittest.mock import Mock, patch

import pytest

from integrations.reminders import RemindersIntegration as module
from integrations.reminders.RemindersIntegration import RemindersIntegration, add_reminder_item


def a_list(title, shared=False):
    cal = Mock()
    cal.title.return_value = title
    cal.sharingStatus.return_value = 1 if shared else 0
    return cal


def a_store(lists, save_ok=True, save_error=None):
    store = Mock()
    store.calendarsForEntityType_.return_value = lists
    store.saveReminder_commit_error_.return_value = (save_ok, save_error)
    return store


@pytest.fixture
def integration():
    RemindersIntegration.reset_instance()
    yield RemindersIntegration.get_instance()
    RemindersIntegration.reset_instance()


@pytest.fixture
def eventkit():
    with patch.object(module, "EventKit") as ek:
        yield ek


class TestAddReminderItem:
    def test_the_item_is_saved_to_the_named_list(self, integration, eventkit):
        groceries = a_list("Groceries")
        store = a_store([a_list("Todo"), groceries])
        with patch.object(integration, "_event_store", return_value=store):
            assert add_reminder_item("Milk", list_name="Groceries") == ""
        reminder = eventkit.EKReminder.reminderWithEventStore_.return_value
        reminder.setTitle_.assert_called_once_with("Milk")
        reminder.setCalendar_.assert_called_once_with(groceries)
        store.saveReminder_commit_error_.assert_called_once_with(reminder, True, None)

    def test_the_list_comes_from_config_when_none_is_named(self, integration, eventkit):
        groceries = a_list("Groceries")
        store = a_store([groceries])
        with patch.object(integration, "_event_store", return_value=store), \
             patch.object(module, "config") as config:
            config.get.return_value = "Groceries"
            assert add_reminder_item("Eggs") == ""
        config.get.assert_called_with("reminders.shopping_list_name")
        eventkit.EKReminder.reminderWithEventStore_.return_value.setCalendar_.assert_called_once_with(groceries)

    def test_a_shared_list_wins_over_an_unshared_namesake(self, integration, eventkit):
        shared = a_list("Groceries", shared=True)
        store = a_store([a_list("Groceries"), shared])
        with patch.object(integration, "_event_store", return_value=store):
            add_reminder_item("Bread", list_name="Groceries")
        eventkit.EKReminder.reminderWithEventStore_.return_value.setCalendar_.assert_called_once_with(shared)

    def test_a_missing_list_fails_naming_the_lists_there_are(self, integration, eventkit):
        store = a_store([a_list("Todo"), a_list("Private")])
        with patch.object(integration, "_event_store", return_value=store):
            result = add_reminder_item("Bread", list_name="Groceries")
        assert result.startswith("Failed to add reminder")
        assert "Todo" in result and "Private" in result
        store.saveReminder_commit_error_.assert_not_called()

    def test_a_refused_save_reports_eventkits_reason(self, integration, eventkit):
        refusal = Mock()
        refusal.localizedDescription.return_value = "The store is read only"
        store = a_store([a_list("Groceries")], save_ok=False, save_error=refusal)
        with patch.object(integration, "_event_store", return_value=store):
            result = add_reminder_item("Bread", list_name="Groceries")
        assert result.startswith("Failed to add reminder")
        assert "read only" in result

    def test_a_denied_store_fails_the_add_instead_of_raising(self, integration, eventkit):
        with patch.object(integration, "_event_store", side_effect=RuntimeError("access denied")):
            result = add_reminder_item("Bread", list_name="Groceries")
        assert result.startswith("Failed to add reminder")
        assert "access denied" in result


class TestCheckHealth:
    def test_passes_when_the_configured_list_exists(self, integration, eventkit):
        store = a_store([a_list("Groceries")])
        with patch.object(integration, "_event_store", return_value=store), \
             patch.object(module, "config") as config:
            config.get.return_value = "Groceries"
            assert integration.check_health() is True

    def test_fails_when_the_configured_list_is_gone(self, integration, eventkit):
        store = a_store([a_list("Todo")])
        with patch.object(integration, "_event_store", return_value=store), \
             patch.object(module, "config") as config:
            config.get.return_value = "Groceries"
            with pytest.raises(LookupError, match="Todo"):
                integration.check_health()


class TestEventStore:
    def test_the_store_is_built_once_after_access_is_granted(self, integration, eventkit):
        store = eventkit.EKEventStore.alloc.return_value.init.return_value
        store.requestFullAccessToRemindersWithCompletion_.side_effect = lambda done: done(True, None)
        assert integration._event_store() is store
        assert integration._event_store() is store
        eventkit.EKEventStore.alloc.assert_called_once()
        store.requestFullAccessToRemindersWithCompletion_.assert_called_once()

    def test_denied_access_says_where_to_grant_it(self, integration, eventkit):
        store = eventkit.EKEventStore.alloc.return_value.init.return_value
        store.requestFullAccessToRemindersWithCompletion_.side_effect = lambda done: done(False, None)
        with pytest.raises(RuntimeError, match="Privacy & Security"):
            integration._event_store()
        assert integration._store is None

    def test_an_unanswered_prompt_times_out_instead_of_hanging(self, integration, eventkit):
        store = eventkit.EKEventStore.alloc.return_value.init.return_value
        store.requestFullAccessToRemindersWithCompletion_.side_effect = lambda done: None
        with patch.object(RemindersIntegration, "ACCESS_TIMEOUT", 0.01):
            with pytest.raises(RuntimeError, match="not answered"):
                integration._event_store()

    def test_the_pre_macos_14_access_call_is_used_when_the_new_one_is_missing(self, integration, eventkit):
        store = Mock(spec=["requestAccessToEntityType_completion_"])
        store.requestAccessToEntityType_completion_.side_effect = lambda kind, done: done(True, None)
        eventkit.EKEventStore.alloc.return_value.init.return_value = store
        assert integration._event_store() is store
        store.requestAccessToEntityType_completion_.assert_called_once()

    def test_without_eventkit_the_store_cannot_be_built(self, integration):
        with patch.object(module, "EventKit", None):
            with pytest.raises(RuntimeError, match="EventKit"):
                integration._event_store()

    def test_reset_drops_the_store(self, integration, eventkit):
        integration._store = Mock()
        RemindersIntegration.reset_instance()
        assert integration._store is None
