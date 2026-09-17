"""How far behind the terminal is: a cursor-position probe (DSR, `ESC [ 6 n`) rides at the
end of a frame and the terminal answers it (`ESC [ row ; col R`) only after it has drawn
everything ahead of it in the stream, so the round trip measures the whole pipe — TCP,
sshd's channel window, the terminal app — whatever the frames are queued in. A link that
cannot carry the frames shows as a round trip growing into seconds while the picture stands
still; a healthy one as tens of milliseconds."""
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from common.logging import info, trace

DSR_REQUEST = b"\x1b[6n"


@dataclass(frozen=True)
class LinkStatus:
    """What a tab may show: `lag` is seconds of display latency (None until the first probe is
    out, or when the terminal does not answer probes), `saturated` when it is past the line
    where the picture visibly stalls."""
    lag: Optional[float] = None
    saturated: bool = False
    unsupported: bool = False


NO_LINK_STATUS = LinkStatus()


class LinkProbe:
    """One client's probes: `request` hands back the bytes to append to a frame when one is
    due, `reply` takes a cursor report, `status` reads the latency as the larger of the last
    round trip and the age of the oldest unanswered probe, so a stall shows while it lasts,
    not only once the terminal catches up."""

    INTERVAL = 0.1          # seconds between probes; a saturated link answers late, not often
    MAX_OUTSTANDING = 64    # past this the terminal is seconds behind and more probes add nothing
    SATURATED = 0.3         # seconds of latency past which the picture visibly stands still
    GIVE_UP = 8.0           # a first probe unanswered this long means the terminal never will

    def __init__(self, name: str = "", clock=time.perf_counter):
        self.name = name
        self.clock = clock
        self.sent: Deque[float] = deque()
        self.last_sent = 0.0
        self.rtt: Optional[float] = None
        self.replies = 0
        self.unsupported = False
        self.saturated = False

    def request(self) -> bytes:
        now = self.clock()
        if self.unsupported or len(self.sent) >= self.MAX_OUTSTANDING or now - self.last_sent < self.INTERVAL:
            return b""
        self.sent.append(now)
        self.last_sent = now
        return DSR_REQUEST

    def reply(self):
        if not self.sent:
            trace(f"Link probe {self.name}: cursor report with no probe outstanding, ignored.")
            return
        self.rtt = self.clock() - self.sent.popleft()
        self.replies += 1
        if self.replies == 1:
            info(f"Link probe {self.name}: terminal answers, first round trip {self.rtt * 1000:.0f} ms.")

    def status(self) -> LinkStatus:
        if self.unsupported:
            return LinkStatus(unsupported=True)
        now = self.clock()
        oldest = now - self.sent[0] if self.sent else 0.0
        if self.replies == 0 and oldest > self.GIVE_UP:
            self.unsupported = True
            self.sent.clear()
            info(f"Link probe {self.name}: no cursor report in {oldest:.0f} s, the terminal does not "
                 f"answer DSR — display latency unknown.")
            return LinkStatus(unsupported=True)
        if self.rtt is None and not self.sent:
            return NO_LINK_STATUS
        lag = max(self.rtt or 0.0, oldest)
        saturated = lag > self.SATURATED
        if saturated != self.saturated:
            self.saturated = saturated
            if saturated:
                info(f"Link probe {self.name}: saturated, the terminal is {lag * 1000:.0f} ms behind "
                     f"({len(self.sent)} probes unanswered).")
            else:
                info(f"Link probe {self.name}: caught up, {lag * 1000:.0f} ms behind.")
        return LinkStatus(lag=lag, saturated=saturated)
