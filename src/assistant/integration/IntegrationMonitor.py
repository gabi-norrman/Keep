import threading
import time
from typing import Callable, Dict, List, Optional

from common.ConfigManager import ConfigManager
from .Feed import Feed
from .FeedRunner import Feeds
from .Integration import Integration, IntegrationState
from common.logging import info, error, trace
from common.DevelopmentConfig import RUNNING_UNDER_TEST


def folder_of(cls: type) -> Optional[str]:
    """The integration folder (`src/integrations/<folder>/`) a class lives in, None for a
    class outside the package (WebAccess, the TTS engines)."""
    parts = cls.__module__.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "integrations" else None


class UnavailableIntegration(Integration):
    """Stands in for an integration whose constructor raised (Tesla without a `tesla`
    section): listed as NOT_CONFIGURED with the error, so `list_integrations` sees it and
    `restart_integration` can try the constructor again once the config is there."""

    def __init__(self, cls: type, exc: Exception):
        super().__init__()
        self.cls = cls
        self._name = cls.__name__.removesuffix("Integration")
        self._state = IntegrationState.NOT_CONFIGURED
        self._last_error = f"{type(exc).__name__}: {exc}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def config_keys(self) -> List[str]:
        return []

    def check_health(self) -> bool:
        return False


class DisabledIntegration(Integration):
    """Stands in for an integration config.yaml does not list in `enabled_integrations`: the
    class is never constructed (no threads, logins or library scans), `list_integrations`
    shows it as disabled with the config_set line that enables it, and `restart` rebuilds it
    once the list holds its name."""

    def __init__(self, cls: type):
        super().__init__()
        self.cls = cls
        self._name = cls.config_name()
        self._state = IntegrationState.DISABLED
        # Known from the class alone, so `list_integrations` can still say "feed" — the user
        # asking for "my feeds" must find a disabled one too.
        self.is_feed = issubclass(cls, Feed)

    @property
    def name(self) -> str:
        return self._name

    @property
    def config_keys(self) -> List[str]:
        return []

    def check_health(self) -> bool:
        return False


class IntegrationMonitor:
    """Runs periodic health checks on the registered integrations and tracks their state.

    A background thread calls each integration's check_health() every CHECK_INTERVAL and
    moves it between OK/WARNING/ERROR (after FAILURE_THRESHOLD consecutive failures);
    NOT_CONFIGURED, NEEDS_2FA and RATE_LIMITED integrations are skipped until resolved.

    It is also the one answer to "is this integration on": `folder_enabled` and
    `contract_enabled` tell the prompt which folders' sections to run and which toolkits to
    offer, and `add_listener` callbacks run whenever that answer may have changed — after a
    registration batch (the registrar calls `notify`) and after every `restart`.
    """

    _instance: Optional['IntegrationMonitor'] = None

    CHECK_INTERVAL = 15 * 60  # 15 minutes
    FAILURE_THRESHOLD = 3

    def __init__(self):
        self._integrations: Dict[str, Integration] = {}
        # name.lower() -> (class, start hook) for integrations registered by class, the only
        # ones `restart` can rebuild; everything the launcher registers goes through
        # register_class, a plain `register` of an instance is for tests and ad hoc probes.
        self._restartable: Dict[str, tuple[type, Optional[Callable[[Integration], None]]]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._config_manager = ConfigManager()
        self._listeners: List[Callable[[], None]] = []

    @classmethod
    def get_instance(cls) -> 'IntegrationMonitor':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        if cls._instance is not None:
            cls._instance.stop()
        cls._instance = None

    def register(self, integration: Integration):
        for key in integration.config_keys:
            try:
                present = self._config_manager.get(key) not in (None, "", [], {})
            except KeyError:
                present = False
            if not present:   # absent or empty, the same verdict Integration.missing_config_keys gives
                integration.state = IntegrationState.NOT_CONFIGURED
                integration.on_not_configured()
                trace(f"Integration '{integration.name}' not configured (missing config key '{key}')")
                with self._lock:
                    self._integrations[integration.name] = integration
                return

        if integration.needs_2fa():
            integration.state = IntegrationState.NEEDS_2FA
            info(f"Integration '{integration.name}' requires 2FA")
            integration.on_needs_2fa()

        with self._lock:
            self._integrations[integration.name] = integration
        info(f"Starting integration {integration.name}...")

    def register_class(self, cls: type, start: Optional[Callable[[Integration], None]] = None) -> Integration:
        """Build the singleton and register it; a constructor that raises registers an
        UnavailableIntegration placeholder instead. *start* runs on the new instance right
        after registration (the context feeders' thread start) and again on every restart."""
        if not cls.enabled(self._config_manager):
            integration = DisabledIntegration(cls)
            info(f"Integration '{integration.name}' disabled (not in enabled_integrations) — not built")
            with self._lock:
                self._integrations[integration.name] = integration
                self._restartable[integration.name.lower()] = (cls, start)
            return integration
        try:
            integration = cls.get_instance()
        except Exception as exc:
            info(f"Integration {cls.__name__} could not be built: {exc}")
            integration = UnavailableIntegration(cls, exc)
        self.register(integration)
        with self._lock:
            self._restartable[integration.name.lower()] = (cls, start)
        if start is not None and not isinstance(integration, UnavailableIntegration):
            start(integration)
        return integration

    def add_listener(self, callback: Callable[[], None]):
        """Run *callback* after every change to which integrations are on (see `notify`)."""
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def notify(self):
        """Tell the listeners the set of enabled integrations may have changed. The registrar
        calls it once a batch of register_class calls is in; `restart` calls it itself."""
        with self._lock:
            listeners = list(self._listeners)
        trace(f"IntegrationMonitor: notifying {len(listeners)} listener(s) of a change")
        for callback in listeners:
            try:
                callback()
            except Exception as exc:
                error(f"IntegrationMonitor listener {callback} raised {type(exc).__name__}: {exc}")

    @staticmethod
    def _class_of(integration: Integration) -> type:
        """The integration class, behind a placeholder too."""
        return integration.cls if isinstance(integration, (DisabledIntegration, UnavailableIntegration)) \
            else type(integration)

    def folder_integrations(self, folder: str) -> List[Integration]:
        """The registered integrations (placeholders included) of an integration folder."""
        return [i for i in self._snapshot() if folder_of(self._class_of(i)) == folder]

    def contract_integrations(self, contract: type) -> List[Integration]:
        """The registered integrations (placeholders included) whose class implements *contract*."""
        return [i for i in self._snapshot() if issubclass(self._class_of(i), contract)]

    @staticmethod
    def _enabled(integrations: List[Integration]) -> Optional[bool]:
        if not integrations:
            return None
        return any(not isinstance(i, DisabledIntegration) for i in integrations)

    def folder_enabled(self, folder: str) -> Optional[bool]:
        """Whether the folder's integrations are on: True when one of them is built (configured
        or not), False when every registered one is disabled, None while none is registered
        (the launcher has not reached it yet, or a test never will)."""
        return self._enabled(self.folder_integrations(folder))

    def contract_enabled(self, contract: type) -> Optional[bool]:
        """The same verdict for the integrations implementing a contract (HomeAutomation, a
        mail or calendar source)."""
        return self._enabled(self.contract_integrations(contract))

    def find(self, name: str) -> Optional[Integration]:
        wanted = name.strip().lower()
        for integration in self._snapshot():
            if integration.name.lower() == wanted:
                return integration
        return None

    def restart(self, name: str) -> Optional[dict]:
        """Rebuild the integration behind *name*: drop the singleton (`_on_reset` stops its
        threads), construct it again, re-run the config check, then a health check. Returns
        None for an unknown name, else {"restartable": bool, "status": dict} — not
        restartable for integrations registered by instance, whose owners keep a reference."""
        integration = self.find(name)
        if integration is None:
            info(f"Integration restart: no integration named '{name}'")
            return None
        with self._lock:
            record = self._restartable.pop(integration.name.lower(), None)
        if record is None:
            info(f"Integration restart: '{integration.name}' is not restartable from here")
            return {"restartable": False, "status": self._status(integration)}
        cls, start = record
        info(f"Restarting integration {integration.name}...")
        self._config_manager.reload()   # config_request may just have added the section this needs
        try:
            cls.reset_instance()
        except Exception as exc:
            error(f"Integration '{integration.name}': _on_reset raised {type(exc).__name__}: {exc}")
        with self._lock:
            self._integrations.pop(integration.name, None)
        rebuilt = self.register_class(cls, start)
        if rebuilt.state not in (IntegrationState.NOT_CONFIGURED, IntegrationState.DISABLED, IntegrationState.NEEDS_2FA):
            self._await_feed_open(rebuilt)
            self._check_integration(rebuilt)
        info(f"Integration '{rebuilt.name}' restarted: {rebuilt.state.value}"
             + (f" ({rebuilt.last_error})" if rebuilt.last_error else ""))
        self.notify()
        return {"restartable": True, "status": self._status(rebuilt)}

    def restart_in_place(self, name: str) -> Optional[dict]:
        """A sign-in landed on the running instance (WhatsApp pairs on the feed's own
        connection, and the history the phone then uploads comes on that connection): run
        the start hook on the instance as it is, no rebuild, then the health check. Same
        answer shape as `restart`; None for an unknown name."""
        integration = self.find(name)
        if integration is None:
            info(f"Integration restart in place: no integration named '{name}'")
            return None
        with self._lock:
            record = self._restartable.get(integration.name.lower())
        if isinstance(integration, (DisabledIntegration, UnavailableIntegration)):
            info(f"Integration restart in place: '{integration.name}' is {integration.state.value}, nothing to start")
            return {"restartable": record is not None, "status": self._status(integration)}
        if integration.needs_2fa():
            integration.state = IntegrationState.NEEDS_2FA
            info(f"Integration '{integration.name}' still requires 2FA — not started")
            self.notify()
            return {"restartable": record is not None, "status": self._status(integration)}
        info(f"Starting integration {integration.name} in place...")
        if record is not None and record[1] is not None:
            record[1](integration)
        self._await_feed_open(integration)
        self._check_integration(integration)
        info(f"Integration '{integration.name}' started in place: {integration.state.value}"
             + (f" ({integration.last_error})" if integration.last_error else ""))
        self.notify()
        return {"restartable": record is not None, "status": self._status(integration)}

    def _await_feed_open(self, integration: Integration):
        """A Feed's start hook only spawns its thread; the connection its health check reads
        is built by open_feed on that thread. Checked a millisecond later the restart
        reported "Health check returned False" beside a live feed for the next 15 minutes,
        and the LLM read that as a login that had not landed."""
        if not isinstance(integration, Feed):
            return
        runner = Feeds.get_instance().runner(integration.feed_name)
        if runner is None:
            trace(f"Integration '{integration.name}': no feed runner to wait for")
            return
        started = time.time()
        if runner.wait_opened(self.FEED_OPEN_TIMEOUT):
            trace(f"Integration '{integration.name}': feed opened after {time.time() - started:.1f}s, checking health")
        else:
            info(f"Integration '{integration.name}': feed still opening after {self.FEED_OPEN_TIMEOUT}s, "
                 "checking health anyway")

    def _snapshot(self) -> List[Integration]:
        with self._lock:
            return list(self._integrations.values())

    def of_type(self, cls: type) -> List[Integration]:
        """The registered integrations that are instances of *cls* (a contract such as
        HomeAutomation, or a placeholder class), in registration order."""
        return [i for i in self._snapshot() if isinstance(i, cls)]

    def start(self):
        if self._running:
            return
        if RUNNING_UNDER_TEST:
            info("IntegrationMonitor not starting — running under test, integrations stay idle")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="IntegrationMonitor", daemon=True)
        self._thread.start()
        info("IntegrationMonitor started")

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    STARTUP_GRACE_PERIOD = 10  # seconds, let integrations finish connecting
    FEED_OPEN_TIMEOUT = 15     # seconds a restart waits for a feed's open_feed before the health check

    def _run_loop(self):
        time.sleep(self.STARTUP_GRACE_PERIOD)

        for integration in self._snapshot():
            if integration.state in (IntegrationState.NOT_CONFIGURED, IntegrationState.DISABLED, IntegrationState.NEEDS_2FA,
                                     IntegrationState.RATE_LIMITED):
                continue
            self._check_integration(integration)

        self._log_cycle_summary()

        while self._running:
            time.sleep(self.CHECK_INTERVAL)
            if not self._running:
                break
            self._check_cycle()

    def _check_cycle(self):
        """One recurring pass: a health check for every integration that can have one — a
        disabled placeholder has nothing to check and must keep its DISABLED state, which
        is what list_integrations keys the enable line on."""
        for integration in self._snapshot():
            if integration.state in (IntegrationState.NOT_CONFIGURED, IntegrationState.DISABLED):
                continue
            if integration.state == IntegrationState.NEEDS_2FA:
                if not integration.needs_2fa():
                    info(f"Integration '{integration.name}' 2FA resolved, resuming health checks")
                    self._check_integration(integration)
                else:
                    integration.on_needs_2fa()
                continue
            if integration.state == IntegrationState.RATE_LIMITED:
                self._check_integration(integration)
                continue
            if integration.needs_2fa():
                integration.state = IntegrationState.NEEDS_2FA
                info(f"Integration '{integration.name}' now requires 2FA")
                continue
            self._check_integration(integration)

    def _log_cycle_summary(self):
        counts: Dict[IntegrationState, int] = {}
        for integration in self._snapshot():
            counts[integration.state] = counts.get(integration.state, 0) + 1
        labels = {
            IntegrationState.OK: "healthy",
            IntegrationState.WARNING: "warning",
            IntegrationState.ERROR: "error",
            IntegrationState.NOT_CONFIGURED: "not configured",
            IntegrationState.DISABLED: "disabled",
            IntegrationState.NEEDS_2FA: "needs 2fa",
            IntegrationState.RATE_LIMITED: "rate limited",
            IntegrationState.UNKNOWN: "unknown",
        }
        parts = [f"{c} {labels[s]}" for s, c in counts.items() if c > 0]
        info(f"Health monitoring cycle complete, {', '.join(parts)}")

    def _check_integration(self, integration: Integration):
        try:
            trace(f"Checking health of '{integration.name}'")
            healthy = integration.check_health()
            integration._last_check_time = time.time()

            if healthy:
                integration._consecutive_failures = 0
                integration._last_error = None
                integration.state = IntegrationState.OK
            else:
                info(f"{integration.name} is not healthy")
                self._record_failure(integration, "Health check returned False")
        except Exception as e:
            integration._last_check_time = time.time()
            self._record_failure(integration, str(e))

    def _record_failure(self, integration: Integration, error_msg: str):
        if integration.state == IntegrationState.RATE_LIMITED:
            return
        integration._consecutive_failures += 1
        integration._last_error = error_msg
        error(f"Integration '{integration.name}' health check failed: {error_msg}")

        if integration._consecutive_failures >= self.FAILURE_THRESHOLD:
            integration.state = IntegrationState.ERROR
        elif integration.state in (IntegrationState.OK, IntegrationState.UNKNOWN):
            integration.state = IntegrationState.WARNING

    def check_now(self, name: str) -> Optional[IntegrationState]:
        integration = self._integrations.get(name)
        if integration is None:
            return None
        if integration.state in (IntegrationState.NOT_CONFIGURED, IntegrationState.DISABLED):
            return integration.state
        if integration.state == IntegrationState.NEEDS_2FA and integration.needs_2fa():
            return IntegrationState.NEEDS_2FA
        self._check_integration(integration)
        return integration.state

    def get_all_statuses(self) -> List[dict]:
        return [self._status(integration) for integration in self._snapshot()]

    def _status(self, integration: Integration) -> dict:
        with self._lock:
            restartable = integration.name.lower() in self._restartable
        status = {
            "name": integration.name,
            "state": integration.state.value,
            "last_check_time": integration.last_check_time,
            "last_error": integration.last_error,
            "consecutive_failures": integration.consecutive_failures,
            "config_keys": list(integration.config_keys),
            "restartable": restartable,
            "sensitive": (integration.cls if isinstance(integration, DisabledIntegration) else type(integration)).sensitive,
            "login_hint": (integration.cls if isinstance(integration, DisabledIntegration) else type(integration)).login_hint,
            "accounts": integration.accounts_status(),
        }
        if isinstance(integration, Feed):
            # The feed's own axis (catching up / live / stopped), next to the health state.
            status["feed"] = Feeds.get_instance().status(integration.feed_name)
        elif isinstance(integration, DisabledIntegration) and integration.is_feed:
            status["feed"] = None
        return status

    def describe_all(self) -> List[dict]:
        """`get_all_statuses` plus, per integration, which of its config keys are set —
        key names only (ConfigStore.describe_keys), the shape `list_integrations` renders."""
        from common import ConfigStore
        result = []
        for status in self.get_all_statuses():
            status["keys"] = ConfigStore.describe_keys(status["config_keys"])
            result.append(status)
        trace(f"IntegrationMonitor: described {len(result)} integrations")
        return result
