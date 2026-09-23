"""The DSR link probe: a cursor report answers an outstanding probe instead of reaching the
input callback, the latency is the larger of the last round trip and the oldest unanswered
probe, and a terminal that never answers is given up on."""
from interfaces.console.framework.LinkProbe import LinkProbe, DSR_REQUEST, NO_LINK_STATUS
from interfaces.console.framework.TUIServer import TUIServer
from interfaces.console.tabs.CrystalTab import CrystalTab, COLOR_SATURATED, COLOR_STATUS
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.framework.LinkProbe import LinkStatus

ADDR = ("test", 0)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def probe():
    clock = Clock()
    return LinkProbe("t", clock), clock


class TestProbeTiming:
    def test_one_probe_per_interval(self):
        p, clock = probe()
        assert p.request() == DSR_REQUEST
        assert p.request() == b""
        clock.now += LinkProbe.INTERVAL * 1.5
        assert p.request() == DSR_REQUEST
        assert len(p.sent) == 2

    def test_reply_measures_the_round_trip(self):
        p, clock = probe()
        p.request()
        clock.now += 0.04
        p.reply()
        status = p.status()
        assert abs(status.lag - 0.04) < 1e-9 and not status.saturated and not status.unsupported

    def test_unanswered_probe_shows_while_it_lasts(self):
        p, clock = probe()
        p.request()
        clock.now += 0.02
        p.reply()
        clock.now += LinkProbe.INTERVAL * 1.5
        p.request()
        clock.now += 0.5
        status = p.status()
        assert abs(status.lag - 0.5) < 1e-9 and status.saturated

    def test_recovery_drops_the_lag_again(self):
        p, clock = probe()
        p.request()
        clock.now += 1.0
        p.reply()
        assert p.status().saturated
        clock.now += LinkProbe.INTERVAL * 1.5
        p.request()
        clock.now += 0.03
        p.reply()
        assert not p.status().saturated

    def test_outstanding_probes_are_capped(self):
        p, clock = probe()
        for _ in range(LinkProbe.MAX_OUTSTANDING + 5):
            p.request()
            clock.now += LinkProbe.INTERVAL * 1.5
        assert len(p.sent) == LinkProbe.MAX_OUTSTANDING

    def test_terminal_that_never_answers_is_given_up_on(self):
        p, clock = probe()
        p.request()
        clock.now += LinkProbe.GIVE_UP + 1
        assert p.status().unsupported
        assert p.request() == b"" and not p.sent

    def test_a_late_first_reply_is_not_given_up_on(self):
        p, clock = probe()
        p.request()
        clock.now += LinkProbe.GIVE_UP - 1
        assert not p.status().unsupported
        p.reply()
        clock.now += LinkProbe.GIVE_UP
        assert not p.status().unsupported

    def test_stray_report_is_ignored(self):
        p, _ = probe()
        p.reply()
        assert p.rtt is None and p.status() == NO_LINK_STATUS


class TestServerInput:
    def _server(self):
        keys = []
        srv = TUIServer(draw_screen_callback=lambda screen, frame: None,
                        input_callback=lambda key, addr: keys.append(key))
        return srv, keys

    def test_cursor_report_answers_the_probe_not_the_keys(self):
        srv, keys = self._server()
        p, clock = probe()
        p.request()
        clock.now += 0.05
        rest = srv._process_input_buffer("a\x1b[12;34Rb", ADDR, p)
        assert rest == "" and keys == ["a", "b"]
        assert p.replies == 1 and abs(p.rtt - 0.05) < 1e-9

    def test_cursor_report_without_a_probe_is_a_key(self):
        srv, keys = self._server()
        p, _ = probe()
        srv._process_input_buffer("\x1b[12;34R", ADDR, p)
        assert keys == ["\x1b[12;34R"] and p.replies == 0
        srv._process_input_buffer("\x1b[1;2R", ADDR)
        assert keys[-1] == "\x1b[1;2R"

    def test_link_status_is_the_worst_client(self):
        srv, _ = self._server()
        fast, clock_a = probe()
        slow, clock_b = probe()
        fast.request(); clock_a.now += 0.01; fast.reply()
        slow.request(); clock_b.now += 0.8; slow.reply()
        srv.link_probes = {object(): fast, object(): slow}
        status = srv.link_status()
        assert abs(status.lag - 0.8) < 1e-9 and status.saturated

class TestCrystalStatusLine:
    def _status_cells(self, link):
        screen = TUIScreen(160, 30)
        screen.link_status = link
        tab = CrystalTab()
        tab.aa = 1
        tab.draw(screen, 0)
        row = screen.next_frame[screen.height - 2]
        text = "".join(c.char for c in row)
        return text, row

    def test_shows_the_lag(self):
        text, row = self._status_cells(LinkStatus(lag=0.042))
        at = text.index("link 42 ms")
        assert row[at].style.fg == COLOR_STATUS

    def test_saturated_is_red(self):
        text, row = self._status_cells(LinkStatus(lag=1.3, saturated=True))
        at = text.index("link 1300 ms")
        assert row[at].style.fg == COLOR_SATURATED
        assert row[text.index("space pause")].style.fg == COLOR_STATUS

    def test_placeholders(self):
        assert "link --" in self._status_cells(NO_LINK_STATUS)[0]
        assert "link n/a" in self._status_cells(LinkStatus(unsupported=True))[0]


class FakeSock:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        pass


class TestProbeGating:
    """Probes ride frames only while the drawn tab shows the link status; other tabs cost
    the link nothing."""

    def test_no_probe_when_no_tab_wants_it(self):
        srv = TUIServer(draw_screen_callback=lambda screen, frame: None)
        sock = FakeSock()
        srv.clients = {sock}
        srv.link_probes = {sock: LinkProbe("x")}
        srv._broadcast(b"frame")
        assert sock.sent == [b"frame"]

    def test_probe_rides_the_frame_when_wanted(self):
        srv = TUIServer(draw_screen_callback=lambda screen, frame: None)
        sock = FakeSock()
        srv.clients = {sock}
        srv.link_probes = {sock: LinkProbe("x")}
        srv.probing = True
        srv._broadcast(b"frame")
        assert sock.sent == [b"frame" + DSR_REQUEST]

class TestSaturationLogging:
    def test_saturation_is_tracked_across_its_edges(self):
        p, clock = probe()
        p.request()
        clock.now += 0.02
        p.reply()
        assert not p.status().saturated and not p.saturated
        clock.now += LinkProbe.INTERVAL * 1.5
        p.request()
        clock.now += LinkProbe.SATURATED + 0.1
        assert p.status().saturated and p.saturated
        p.reply()
        clock.now += LinkProbe.INTERVAL * 1.5
        p.request()
        clock.now += 0.02
        p.reply()
        assert not p.status().saturated and not p.saturated
