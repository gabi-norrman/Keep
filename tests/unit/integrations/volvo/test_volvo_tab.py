"""The Volvo tab: the sign-in state, the link shown on Ctrl-L, the pasted address taken on
Ctrl-E and handed to the integration, and the sign-out."""
import time
from unittest.mock import Mock

import pytest

from interfaces.console.framework.TUIScreen import TUIScreen

from assistant.integration.OAuthPaste import SignIn
from assistant.integration.VehicleProvider import VehicleSpec
from integrations.volvo.VolvoAuth import REDIRECT_URI
from integrations.volvo.VolvoTab import VolvoTab

URL = "https://volvoid.eu.volvocars.com/as/authorization.oauth2?response_type=code&client_id=app-id&redirect_uri=http%3A%2F%2Flocalhost%3A18723%2F&scope=openid+conve%3Avehicle_relation&state=st-1&code_challenge=abc&code_challenge_method=S256"
PASTED = f"{REDIRECT_URI}?code=c0de&state=st-1"


def make_tab(signed_in=True, again=False, cars=("Family XC40",), finish=""):
    api = Mock()
    api.signed_in.return_value = signed_in
    api.auth.needs_sign_in_again.return_value = again
    api.auth.expires_at.return_value = None
    api.vehicles.return_value = [VehicleSpec(id=c.lower(), label=c, handle=f"YV1{i:014d}") for i, c in enumerate(cars)]
    api.get_auth_status.return_value = {"status": "authenticated" if signed_in and not again else "needs_auth", "message": ""}
    api.login_begin.return_value = SignIn(URL, "st-1", REDIRECT_URI)
    api.login_finish.return_value = finish
    tab = VolvoTab()
    tab._api = api
    tab._run = lambda fn: fn()
    tab._check_health_now = lambda: None
    tab._check_auth_status()
    return tab, api


def draw(tab, width, height=40):
    screen = TUIScreen(width, height)
    tab._last_check_time = time.time() + 1e6   # no background check during the test
    tab.draw(screen, 0)


def type_text(tab, text):
    for ch in text:
        tab.handle_input(ch)


def tile(tab, title):
    return next(t for t in tab._tiles() if t[0] == title)


@pytest.mark.unit
class TestTiles:
    def test_the_cars_tile_counts_the_configured_cars_and_names_them(self):
        tab, _ = make_tab(cars=("Family XC40", "Marcus's V60"))
        title, value, _, rows = tile(tab, "Cars")
        assert value == "2" and "Family XC40" in rows[0][0] and "V60" in rows[0][0]

    def test_the_id_tile_follows_the_sign_in_state(self):
        assert tile(make_tab()[0], "Volvo ID")[1] == "signed in"
        assert tile(make_tab(signed_in=False)[0], "Volvo ID")[1] == "not signed in"
        assert tile(make_tab(again=True)[0], "Volvo ID")[1] == "sign in again"

    def test_a_signed_in_tab_hints_at_signing_out_an_unsigned_one_at_the_link(self):
        assert "Ctrl-O" in make_tab()[0]._entry_hint()[0]
        assert "Ctrl-L" in make_tab(signed_in=False)[0]._entry_hint()[0]
        assert "Ctrl-L" in make_tab(again=True)[0]._entry_hint()[0]


@pytest.mark.unit
class TestSignIn:
    def test_ctrl_l_shows_the_whole_link_wrapped_over_rows(self):
        tab, api = make_tab(signed_in=False)

        tab.handle_input('\x0c')

        api.login_begin.assert_called_once_with()
        fields = tab._fields()
        assert "".join(f.value for f in fields[:-1]) == URL and fields[0].label == "Open"
        assert fields[-1].waiting and "Ctrl-E" in fields[-1].value

    @pytest.mark.parametrize("width", [80, 140])
    def test_the_link_wraps_to_the_width_the_tile_gives_a_value_so_no_row_is_cut(self, width):
        tab, _ = make_tab(signed_in=False)
        tab.handle_input('\x0c')

        draw(tab, width)

        rows = [f.value for f in tab._fields()[:-1]]
        assert "".join(rows) == URL
        assert all(len(row) <= tab._value_w for row in rows) and len(rows[0]) == tab._value_w
        assert tab._value_w < 60 if width == 80 else tab._value_w > 80

    def test_ctrl_e_then_the_pasted_address_and_enter_finish_the_sign_in(self):
        tab, api = make_tab(signed_in=False)
        tab.handle_input('\x0c')

        tab.handle_input('\x05')
        assert tab._input_mode and tab._fields()[0].waiting
        type_text(tab, PASTED)
        tab.handle_input('\r')

        api.login_finish.assert_called_once_with(PASTED)
        assert not tab._input_mode and tab._sign_in is None and "success" in tab._status_message

    def test_a_failed_finish_keeps_the_link_and_shows_the_reason(self):
        tab, api = make_tab(signed_in=False, finish="the pasted address belongs to another sign-in")
        tab.handle_input('\x0c')
        tab.handle_input('\x05')
        type_text(tab, PASTED)
        tab.handle_input('\r')

        assert tab._sign_in is not None and "another sign-in" in tab._status_message

    def test_enter_on_an_empty_entry_submits_nothing_and_escape_cancels(self):
        tab, api = make_tab(signed_in=False)
        tab.handle_input('\x05')
        tab.handle_input('\r')
        api.login_finish.assert_not_called()
        type_text(tab, "http://")
        tab.handle_input('escape')
        assert not tab._input_mode and tab._input_text == ""


@pytest.mark.unit
class TestSignOut:
    def test_ctrl_o_signs_out_and_drops_a_pending_link(self):
        tab, api = make_tab()
        tab.handle_input('\x0c')

        tab.handle_input('\x0f')

        api.sign_out.assert_called_once_with()
        assert tab._sign_in is None and "Signed out" in tab._status_message
