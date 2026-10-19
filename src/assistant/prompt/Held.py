"""Readings and states shown with hysteresis, so a sensor hovering on a boundary never rewrites a prompt line.

Every changed render of a section costs a note in the history window (PromptManager.on_section_updated),
so a value printed to the decimal the sensor reports makes a note per poll. A HeldValues shows a
reading rounded to a step and keeps that shown value until the fresh reading is a full step away from
it; a HeldStates shows a state only once it has lasted a while. Both are keyed, one instance per
quantity, so one object serves every car, accessory or register of a section."""
import time


class HeldValues:
    """Readings rounded to `step` and held until the fresh one is a full step from what is shown.

    With step 1, a cabin at 20.4 → 20.6 → 20.4 shows 20 throughout and moves to 21 at 21.0; a reading
    that disappears (None) forgets the key, so it starts afresh when it is back. An integer step gives
    integers, a fractional one floats rounded to the step."""

    def __init__(self, step: float):
        self.step = step
        self._shown: dict = {}

    def show(self, key, fresh: float | None) -> float | int | None:
        if fresh is None:
            self._shown.pop(key, None)
            return None
        shown = self._shown.get(key)
        if shown is None or abs(fresh - shown) >= self.step:
            shown = self._round(fresh)
            self._shown[key] = shown
        return shown

    def forget(self, key):
        self._shown.pop(key, None)

    def _round(self, fresh: float):
        steps = round(fresh / self.step)
        if isinstance(self.step, int):
            return int(steps) * self.step
        return round(steps * self.step, 10)


class HeldStates:
    """A state shown only once it has lasted `seconds`; until then the line keeps the previous one.

    A compressor that stops for three minutes and restarts is "running" throughout with a 600 s hold;
    the first state of a key shows at once. `now` is for tests."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._shown: dict = {}
        self._candidate: dict[object, tuple[object, float]] = {}

    def show(self, key, fresh, now: float | None = None):
        if fresh is None:
            self._shown.pop(key, None)
            self._candidate.pop(key, None)
            return None
        now = time.time() if now is None else now
        if key not in self._shown:
            self._shown[key] = fresh
            return fresh
        shown = self._shown[key]
        if fresh == shown:
            self._candidate.pop(key, None)
            return shown
        candidate = self._candidate.get(key)
        if candidate is None or candidate[0] != fresh:
            self._candidate[key] = (fresh, now)
            return shown
        if now - candidate[1] >= self.seconds:
            self._shown[key] = fresh
            self._candidate.pop(key, None)
            return fresh
        return shown

    def forget(self, key):
        self._shown.pop(key, None)
        self._candidate.pop(key, None)
