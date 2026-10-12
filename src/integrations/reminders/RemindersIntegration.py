import threading
from typing import List, Optional

from common.ConfigManager import config
from assistant.integration.Integration import Integration
from common.logging import error, info, trace

try:
    import EventKit
except ImportError:   # not a Mac, or pyobjc missing: check_health says so
    EventKit = None

ACCESS_HINT = ("Reminders access was not granted. Allow it for the app the server runs in (Terminal) under "
               "System Settings → Privacy & Security → Reminders, then restart the integration.")


class RemindersIntegration(Integration):
    """Apple Reminders lists, such as the shopping list, added to when asked.

    Adds items to Apple Reminders lists, such as the shopping list, when asked.

    Goes through EventKit (pyobjc), not AppleScript: an AppleScript call launches and drives
    the Reminders app, half a second warm and seconds cold, while EventKit writes to the
    reminders store in-process in milliseconds. macOS asks once for Reminders access, for the
    process that started the server (Terminal); until the prompt is answered the first call
    waits. One EKEventStore serves the monitor's health checks and the commands, under a lock,
    since EventKit objects are not thread-safe. Two lists of the same name: the shared one wins,
    the shopping list being the one shared with the family."""
    sensitive = True

    ACCESS_TIMEOUT = 30   # seconds to wait for the user to answer macOS's access prompt

    def __init__(self):
        super().__init__()
        self._store = None
        self._lock = threading.RLock()

    @property
    def name(self) -> str:
        return "Reminders"

    @property
    def config_keys(self) -> List[str]:
        return ["reminders"]

    def check_health(self) -> bool:
        with self._lock:
            store = self._event_store()
            list_name = config.get("reminders.shopping_list_name")
            self._calendar(store, list_name)
        return True

    def add_reminder_item(self, item: str, list_name: str = None) -> str:
        try:
            with self._lock:
                if not list_name:
                    list_name = config.get("reminders.shopping_list_name")
                store = self._event_store()
                calendar = self._calendar(store, list_name)
                reminder = EventKit.EKReminder.reminderWithEventStore_(store)
                reminder.setTitle_(item)
                reminder.setCalendar_(calendar)
                ok, err = store.saveReminder_commit_error_(reminder, True, None)
            if not ok:
                message = err.localizedDescription() if err else "EventKit refused the save"
                error(f"Reminders: saving {item!r} to {list_name!r} failed: {message}")
                return f"Failed to add reminder: {message}"
            info(f"Reminders: added {item!r} to list {list_name!r}")
            return ""
        except Exception as e:
            error(f"Failed to add reminder: {e}")
            return f"Failed to add reminder: {e}"

    def _event_store(self):
        """The one EKEventStore, built on first use once macOS has granted Reminders access.
        Raises when EventKit is missing or access was denied, with what to do about it."""
        if self._store is not None:
            return self._store
        if EventKit is None:
            raise RuntimeError("EventKit is not available: Apple Reminders needs macOS and pyobjc")
        store = EventKit.EKEventStore.alloc().init()
        answered = threading.Event()
        verdict = {}

        def completion(granted, err):
            verdict["granted"] = granted
            verdict["error"] = err
            answered.set()

        trace("Reminders: requesting access to the reminders store")
        if hasattr(store, "requestFullAccessToRemindersWithCompletion_"):   # macOS 14+
            store.requestFullAccessToRemindersWithCompletion_(completion)
        else:
            store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeReminder, completion)
        if not answered.wait(self.ACCESS_TIMEOUT):
            raise RuntimeError("Reminders access prompt not answered within "
                               f"{self.ACCESS_TIMEOUT}s — answer it on the server's screen")
        if not verdict.get("granted"):
            detail = verdict.get("error")
            raise RuntimeError(f"{ACCESS_HINT} ({detail.localizedDescription()})" if detail else ACCESS_HINT)
        info("Reminders: access granted, store ready")
        self._store = store
        return store

    @staticmethod
    def _calendar(store, list_name: str):
        """The reminders list called list_name; a shared one before an unshared namesake.
        Raises, naming the lists there are, when none has the name."""
        candidates = [c for c in store.calendarsForEntityType_(EventKit.EKEntityTypeReminder)
                      if c.title() == list_name]
        if not candidates:
            names = sorted({c.title() for c in store.calendarsForEntityType_(EventKit.EKEntityTypeReminder)})
            raise LookupError(f"No Reminders list called {list_name!r}; the lists are: {', '.join(names)}")
        if len(candidates) > 1:
            trace(f"Reminders: {len(candidates)} lists called {list_name!r}, preferring a shared one")
            candidates.sort(key=lambda c: not _is_shared(c))
        return candidates[0]

    def _on_reset(self) -> None:
        self._store = None


def _is_shared(calendar) -> bool:
    sharing_status = getattr(calendar, "sharingStatus", None)   # EKCalendarSharingStatusNotShared is 0
    try:
        return bool(sharing_status and sharing_status())
    except Exception:
        return False


def add_reminder_item(item: str, list_name: str = None) -> str:
    return RemindersIntegration.get_instance().add_reminder_item(item, list_name)
