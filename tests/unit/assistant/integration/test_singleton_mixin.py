import pytest

from assistant.integration.SingletonMixin import SingletonMixin


class _Widget(SingletonMixin):
    def __init__(self, label="default"):
        self.label = label
        self.reset_called = False

    def _on_reset(self):
        self.reset_called = True


class _Gadget(SingletonMixin):
    """Separate subclass — must never share a singleton slot with _Widget."""
    pass


class _PlainSingleton(SingletonMixin):
    """Relies on the default _on_reset (no override)."""
    pass


@pytest.mark.unit
class TestSingletonMixin:
    """Tests for the shared singleton accessor every Integration inherits.

    A bug here (slot collisions between subclasses, args silently dropped,
    _on_reset skipped) would be subtle and affect every integration, so the
    per-class keying and lifecycle contract is pinned down here.
    """

    @pytest.fixture(autouse=True)
    def clean_registry(self):
        for cls in (_Widget, _Gadget, _PlainSingleton):
            SingletonMixin._instances.pop(cls, None)
        yield
        for cls in (_Widget, _Gadget, _PlainSingleton):
            SingletonMixin._instances.pop(cls, None)

    def test_get_instance_returns_same_object(self):
        assert _Widget.get_instance() is _Widget.get_instance()

    def test_subclasses_keyed_separately(self):
        widget = _Widget.get_instance()
        gadget = _Gadget.get_instance()
        assert widget is not gadget
        assert isinstance(widget, _Widget)
        assert isinstance(gadget, _Gadget)

    def test_constructor_args_forwarded_on_first_call(self):
        assert _Widget.get_instance(label="configured").label == "configured"

    def test_args_ignored_after_first_construction(self):
        first = _Widget.get_instance(label="original")
        second = _Widget.get_instance(label="ignored")
        assert second is first
        assert second.label == "original"

    def test_peek_instance_never_builds_and_follows_the_lifecycle(self):
        assert _Widget.peek_instance() is None
        assert _Widget not in SingletonMixin._instances, "peeking must not construct"
        built = _Widget.get_instance()
        assert _Widget.peek_instance() is built
        assert _Gadget.peek_instance() is None
        _Widget.reset_instance()
        assert _Widget.peek_instance() is None

    def test_reset_instance_drops_singleton(self):
        first = _Widget.get_instance()
        _Widget.reset_instance()
        assert _Widget.get_instance() is not first

    def test_reset_instance_calls_on_reset(self):
        instance = _Widget.get_instance()
        _Widget.reset_instance()
        assert instance.reset_called is True

    def test_reset_instance_noop_when_never_created(self):
        _Widget.reset_instance()  # must not raise even though nothing was created
