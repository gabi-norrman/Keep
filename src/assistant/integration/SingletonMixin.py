from typing import ClassVar, Dict, Optional, TypeVar

T = TypeVar("T", bound="SingletonMixin")


class SingletonMixin:
    """Process-wide singleton accessor shared by integration classes.

    Provides get_instance()/reset_instance() so each integration doesn't repeat
    the same _instance boilerplate. Instances are keyed by concrete class, so
    subclasses never share a slot.

    get_instance() forwards any args to the constructor on first call, so a class
    whose __init__ takes a config_manager simply calls get_instance(config_manager=...).
    Override _on_reset() to release resources (stop threads, observers) before the
    singleton is dropped.
    """

    _instances: ClassVar[Dict[type, "SingletonMixin"]] = {}

    @classmethod
    def get_instance(cls: type[T], *args, **kwargs) -> T:
        if cls not in SingletonMixin._instances:
            SingletonMixin._instances[cls] = cls(*args, **kwargs)
        return SingletonMixin._instances[cls]

    @classmethod
    def peek_instance(cls: type[T]) -> Optional[T]:
        """The singleton if one has been built, else None — never builds one."""
        return SingletonMixin._instances.get(cls)

    @classmethod
    def reset_instance(cls) -> None:
        instance = SingletonMixin._instances.pop(cls, None)
        if instance is not None:
            instance._on_reset()

    def _on_reset(self) -> None:
        """Hook called by reset_instance() before the singleton is dropped.
        Override to stop threads, observers, or other background work."""
        pass
