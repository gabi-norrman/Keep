"""PlejdLink without Bluetooth: the site choice, what an advertisement does to the manager,
the state store, `run` without a loop, and the thread giving up on a refused login."""
from unittest.mock import patch

import pytest

from integrations.plejd.PlejdModel import MANUFACTURER_ID
from integrations.plejd.PlejdLink import NotConnected, PlejdLink, SiteChoice, choose_site
from pyplejd import AuthenticationError

MODULE = "integrations.plejd.PlejdLink"
SITES = [{"siteId": "abc123", "title": "Villa Nyström", "deviceCount": 12},
         {"siteId": "def456", "title": "Sommarstugan", "deviceCount": 3}]


@pytest.mark.unit
class TestChooseSite:
    def test_the_only_site_needs_no_choice(self):
        assert choose_site(SITES[:1], "")["siteId"] == "abc123"

    def test_a_wanted_site_is_found_by_title_or_id_regardless_of_case(self):
        assert choose_site(SITES, "sommarstugan")["siteId"] == "def456"
        assert choose_site(SITES, "ABC123")["title"] == "Villa Nyström"

    def test_several_sites_without_a_choice_name_them(self):
        with pytest.raises(SiteChoice) as e:
            choose_site(SITES, "")
        assert "Villa Nyström" in str(e.value) and "Sommarstugan" in str(e.value) and "plejd.site" in str(e.value)

    def test_a_wanted_site_the_account_lacks_names_the_ones_it_has(self):
        with pytest.raises(SiteChoice) as e:
            choose_site(SITES, "Kontoret")
        assert "Kontoret" in str(e.value) and "Sommarstugan" in str(e.value)
        with pytest.raises(SiteChoice):
            choose_site([], "")


class Hardware:
    def __init__(self):
        self.seen = []

    def see(self, rssi, device):
        self.seen.append((rssi, device))
        return len(self.seen) == 1


class Manager:
    def __init__(self, hardware):
        self.hardware = hardware
        self.devices = []
        self.connected = False


class Advertisement:
    def __init__(self, mac, rssi=-60, company=MANUFACTURER_ID):
        self.manufacturer_data = {company: b"\x01\x02\x03\x04" + bytes.fromhex(mac)[::-1] + b"\x00\x00"}
        self.rssi = rssi


class Device:
    def __init__(self, name):
        self.name = name
        self.identifier = ("dev", name)


@pytest.mark.unit
class TestAdvertisements:
    @pytest.fixture
    def link(self):
        link = PlejdLink("m@mlab.test", "secret")
        link.manager = Manager({"AABBCCDDEEFF": Hardware()})
        return link

    def test_a_unit_of_the_site_is_handed_to_pyplejd_and_the_first_sighting_asks_for_a_connect(self, link):
        link._on_advertisement("ble-device", Advertisement("aabbccddeeff", rssi=-55))
        assert link.manager.hardware["AABBCCDDEEFF"].seen == [(-55, "ble-device")]
        assert link.units_seen == 1 and link._ping_wanted
        link._ping_wanted = False
        link._on_advertisement("ble-device", Advertisement("aabbccddeeff", rssi=-70))
        assert link.units_seen == 1 and not link._ping_wanted

    def test_a_stranger_and_another_manufacturer_are_ignored(self, link):
        link._on_advertisement("x", Advertisement("001122334455"))
        link._on_advertisement("x", Advertisement("aabbccddeeff", company=76))
        assert link.units_seen == 0 and not link._ping_wanted
        assert link.manager.hardware["AABBCCDDEEFF"].seen == []

    def test_before_the_site_is_loaded_nothing_happens(self):
        link = PlejdLink("m@mlab.test", "secret")
        link._on_advertisement("x", Advertisement("aabbccddeeff"))
        assert link.units_seen == 0


@pytest.mark.unit
class TestStates:
    def test_states_are_kept_per_device_as_copies(self):
        link = PlejdLink("m@mlab.test", "secret")
        state = {"state": True, "dim": 128}
        link._on_state(Device("Taklampa"), state)
        state["state"] = False
        assert link.states() == {("dev", "Taklampa"): {"state": True, "dim": 128}}
        link.states()[("dev", "Taklampa")]["dim"] = 0
        assert link.states()[("dev", "Taklampa")]["dim"] == 128

    def test_run_without_a_loop_is_not_connected_and_closes_the_coroutine(self):
        async def work():
            return 1

        coro = work()
        with pytest.raises(NotConnected):
            PlejdLink("m@mlab.test", "secret").run(coro, 1)
        with pytest.raises(RuntimeError):     # closed: cannot be awaited any more
            coro.send(None)


@pytest.mark.unit
class TestThread:
    def test_a_refused_login_ends_the_thread_with_the_reason(self):
        async def refused(username, password):
            raise AuthenticationError("Invalid username/password")

        link = PlejdLink("m@mlab.test", "wrong")
        with patch(f"{MODULE}.get_sites", refused):
            link.start()
            link._thread.join(2)
            assert not link._thread.is_alive()
        assert not link.wait_ready(0)
        assert "login refused" in link.error and "plejd.password" in link.error
        link.stop()
