import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional

from .SingletonMixin import SingletonMixin


class IntegrationState(Enum):
    """Health state of an external integration.

    Transitions: OK -> WARNING (1 failure) -> ERROR (3 consecutive failures).
    Any successful check resets back to OK. NOT_CONFIGURED is a terminal state
    for integrations whose config keys are missing.
    """
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"           # not in config's enabled_integrations: never constructed
    NEEDS_2FA = "needs_2fa"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class Integration(SingletonMixin, ABC):
    """Base class for the external service integrations (Tesla, Ring, YR, CalDAV, etc.).

    Subclasses implement name, config_keys, and check_health(). The IntegrationMonitor
    periodically calls check_health() and manages state transitions on this base class.
    Singleton access (get_instance/reset_instance) comes from SingletonMixin.
    """

    # Off unless config.yaml lists the integration in `enabled_integrations`. The exceptions
    # are Web Access, which needs no credentials and the assistant leans on constantly, and
    # ElevenLabs TTS, the engine voice speaks through unless another one is enabled.
    enabled_by_default: bool = False
    # Its data is private (messages, mail, calendar, photos, cameras): enabling it ends the
    # transcript part of the beta telemetry for good — see assistant/Telemetry.py.
    sensitive: bool = False

    @classmethod
    def config_name(cls) -> str:
        """The name an integration is listed under in `enabled_integrations`, readable before
        an instance exists: the constant `name` property, else the class name without
        'Integration' (ArloCameraIntegration -> ArloCamera; give such a class a constant name)."""
        prop = getattr(cls, "name", None)
        if isinstance(prop, property):
            try:
                value = prop.fget(None)
                if isinstance(value, str) and value:
                    return value
            except Exception:
                pass
        return cls.__name__.removesuffix("Integration")

    @classmethod
    def description(cls) -> str:
        """One line on what the integration is, readable before an instance exists: the first
        sentence of the class docstring ("" for a class without one)."""
        text = " ".join((cls.__doc__ or "").split())
        sentence = re.match(r".+?(?<!e\.g)(?<!i\.e)[.!?](?=\s|$)", text)
        return sentence.group(0) if sentence else text

    @classmethod
    def enabled(cls, config) -> bool:
        return config.integration_enabled(cls.config_name(), cls.enabled_by_default)

    def __init__(self):
        self._state: IntegrationState = IntegrationState.UNKNOWN
        self._last_check_time: Optional[float] = None
        self._last_error: Optional[str] = None
        self._consecutive_failures: int = 0

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this integration, e.g. "Tesla" or "Ring"."""
        ...

    @property
    @abstractmethod
    def config_keys(self) -> List[str]:
        """Config paths required for this integration (checked during registration).
        If any key is missing, the integration is marked NOT_CONFIGURED."""
        ...

    @abstractmethod
    def check_health(self) -> bool:
        """Return True if the integration is reachable and functional.
        May raise an exception on failure — the monitor will catch it."""
        ...

    def missing_config_keys(self) -> List[str]:
        """The config_keys that config.yaml does not set (absent or empty) — what the
        monitor's NOT_CONFIGURED verdict rests on, for a caller that wants the list."""
        from common.ConfigManager import config
        missing = []
        for key in self.config_keys:
            try:
                value = config.get(key)
            except KeyError:
                value = None
            if value in (None, ""):
                missing.append(key)
        return missing

    @property
    def state(self) -> IntegrationState:
        """Current health state, managed by IntegrationMonitor."""
        return self._state

    @state.setter
    def state(self, value: IntegrationState):
        self._state = value

    @property
    def last_check_time(self) -> Optional[float]:
        """Epoch timestamp of the most recent health check, or None if never checked."""
        return self._last_check_time

    @property
    def last_error(self) -> Optional[str]:
        """Error message from the most recent failure, or None if last check passed."""
        return self._last_error

    def needs_2fa(self) -> bool:
        """Return True if this integration requires 2FA before it can operate.
        Must be lightweight — only inspect local state, never trigger 2FA emails."""
        return False

    def on_not_configured(self):
        """Called when the integration is marked NOT_CONFIGURED during registration.
        Override to clean up persisted state (tokens, sessions, etc.)."""
        pass

    def on_needs_2fa(self):
        """Called when the integration requires 2FA. Override to attempt auto-resolution
        (e.g. polling email for verification codes)."""
        pass

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive failed health checks. Resets to 0 on success."""
        return self._consecutive_failures
