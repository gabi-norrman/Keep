"""The music commands: resolved through the speakers to whichever system has the zone, silent when
done, the system's failure or the zones there are when not; play_music refusing another system's
list id; the hint without a system; and the Spotify sign-in, which serves the speaker systems."""
from unittest.mock import Mock, patch

import pytest

from assistant.AssistantRequest import RequestContext
from assistant.commands.Commands import Commands
from assistant.commands.Toolkits import TOOLKITS
from assistant.integration.OAuthPaste import SignIn
from assistant.integration.music import MusicSystem as music
from assistant.integration.music.MusicSystem import Speakers
from assistant.integration.music.SpotifyAccount import SpotifyAccount
from assistant.integration.music.SpotifyAuth import REDIRECT_URI
from tests.unit.assistant.integration.music.music_fakes import FakeSpeakers, OtherSpeakers

MODULE = "assistant.commands.Commands"


@pytest.fixture
def commands_instance():
    cp = Mock()
    cp.command_reply = None
    cp.command_reply_contains_error = False
    return Commands(cp)


@pytest.fixture
def sonos():
    return FakeSpeakers(["Kitchen", "Living Room"])


@pytest.fixture
def heos():
    return OtherSpeakers(["Porch"])


@pytest.fixture
def house(sonos, heos):
    with patch.object(music, "speakers", side_effect=lambda: Speakers([sonos, heos])), \
            patch.object(music, "active", return_value=[sonos, heos]):
        yield


@pytest.mark.unit
class TestPlayback:
    @pytest.mark.parametrize("command, call", [
        ("play", ("play", "Kitchen")), ("pause", ("pause", "Kitchen")), ("next", ("next_track", "Kitchen")),
    ])
    def test_a_done_action_says_nothing_and_reaches_the_zones_system(self, commands_instance, house, sonos, heos,
                                                                       command, call):
        assert not commands_instance.run(command, ["kitchen"])
        assert sonos.calls == [call]
        assert heos.calls == []

    def test_the_room_picks_the_system(self, commands_instance, house, sonos, heos):
        commands_instance.run("pause", ["Porch"])
        assert heos.calls == [("pause", "Porch")] and sonos.calls == []

    def test_volume_up_and_down_step_the_same_amount_in_opposite_directions(self, commands_instance, house, sonos):
        commands_instance.run("volume_up", ["Kitchen"])
        commands_instance.run("volume_down", ["Kitchen"])
        (_, _, up), (_, _, down) = sonos.calls
        assert up > 0 and down == -up

    def test_a_systems_failure_reaches_the_llm_as_the_commands_error(self, commands_instance, house, sonos):
        sonos.failure = "could not pause in Kitchen: speaker offline"
        result = commands_instance.run("pause", ["Kitchen"])
        assert result.startswith("pause failed") and "speaker offline" in result

    @pytest.mark.parametrize("command, params", [
        ("play", ["Garage"]), ("pause", ["Garage"]), ("next", ["Garage"]),
        ("volume_up", ["Garage"]), ("volume_down", ["Garage"]), ("play_music", ["Garage", "S-0"]),
    ])
    def test_a_room_no_system_has_names_the_zones_and_nothing_plays(self, commands_instance, house, sonos, heos,
                                                                    command, params):
        result = commands_instance.run(command, params)
        assert command in result and "Garage" in result and "Kitchen" in result and "Porch" in result
        assert sonos.calls == [] and heos.calls == []

    def test_without_a_system_the_hint_is_the_answer(self, commands_instance):
        with patch.object(music, "speakers", return_value=Speakers([])), \
                patch.object(music, "no_system_hint", return_value='Sonos & Spotify is disabled: enable_integration("Sonos & Spotify").'):
            result = commands_instance.run("play", ["Kitchen"])
        assert result.startswith("play failed") and "enable_integration" in result


@pytest.mark.unit
class TestPlayMusic:
    @pytest.mark.parametrize("music_id", ["AA", "S-0", "spotify:track:t1"])
    def test_a_spotify_id_plays_in_the_zones_system(self, commands_instance, house, heos, music_id):
        assert not commands_instance.run("play_music", ["Porch", music_id])
        assert heos.calls == [("play_id", "Porch", music_id)]

    def test_the_zones_own_list_id_plays(self, commands_instance, house, sonos):
        commands_instance.run("play_music", ["Kitchen", "F-2"])
        assert sonos.calls == [("play_id", "Kitchen", "F-2")]

    def test_an_id_of_another_systems_list_is_refused_naming_both(self, commands_instance, house, sonos, heos):
        result = commands_instance.run("play_music", ["Porch", "F-2"])
        assert result.startswith("play_music failed") and "F-2" in result
        assert sonos.name in result and heos.name in result
        assert heos.calls == [] and sonos.calls == []

    def test_without_both_arguments_nothing_plays(self, commands_instance, house, sonos):
        assert commands_instance.run("play_music", ["Kitchen"]).startswith("play_music failed")
        assert sonos.calls == []

    def test_search_runs_in_the_background_on_the_shared_account(self, commands_instance):
        account = Mock()
        with patch.object(SpotifyAccount, "get_instance", return_value=account), \
                patch.object(commands_instance, "background_command") as bg:
            commands_instance.run("search_music", ["Alice band"])
        bg.assert_called_once_with(account.search, "Alice band")


URL = "https://accounts.spotify.com/authorize?client_id=cid&state=st-1"
PASTED = f"{REDIRECT_URI}?code=AQcode&state=st-1"


def _account(finish="", playlists=3, library_error=""):
    account = Mock()
    account.login_begin.return_value = SignIn(URL, "st-1", REDIRECT_URI)
    account.login_finish.return_value = finish
    account.user_name.return_value = "Marcus"
    account.playlists.return_value = [Mock()] * playlists
    account.playlists_total.return_value = playlists
    account.library_error.return_value = library_error
    return account


@pytest.fixture
def spotify():
    """A ready speaker system and the shared account the sign-in commands reach."""
    account = _account()
    with patch.object(music, "active", return_value=[FakeSpeakers(["Kitchen"])]), \
            patch.object(SpotifyAccount, "get_instance", side_effect=lambda: account):
        yield account


@pytest.mark.unit
class TestSpotifyLogin:
    def test_both_commands_are_in_the_setup_toolkit_and_are_core_commands(self, commands_instance):
        setup = next(t for t in TOOLKITS if t.name == "setup")
        assert {"spotify_login", "spotify_login_code"} <= set(setup.commands)
        assert commands_instance._owner("spotify_login") is commands_instance
        assert commands_instance._owner("spotify_login_code") is commands_instance

    def test_login_runs_in_the_config_lane(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("spotify_login", [""]) == ""
        bg.assert_called_once_with(commands_instance._run_spotify_login, (False,), lane=Commands.CONFIG_LANE)

    def test_the_link_and_the_paste_instruction_come_back_as_the_result_and_a_note(self, commands_instance, spotify):
        with patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._run_spotify_login()
        assert URL in reply and "spotify_login_code" in reply and "127.0.0.1:8888" in reply
        assert note.call_args.args[1] == reply
        spotify.login_begin.assert_called_once_with()

    def test_a_chat_that_asked_gets_the_link_in_the_reply_alone_not_as_a_note_too(self, commands_instance, spotify):
        with patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._run_spotify_login(from_chat=True)
        assert URL in reply
        note.assert_not_called()

    def test_whether_a_chat_asked_is_captured_when_the_command_is_dispatched(self, commands_instance):
        commands_instance._current_context = RequestContext(source="telegram", conversation=4711)
        with patch.object(commands_instance, "background_command") as bg:
            commands_instance.run("spotify_login", [""])
        assert bg.call_args.args[1] == (True,)

    def test_a_missing_client_is_an_error_without_a_note(self, commands_instance, spotify):
        spotify.login_begin.side_effect = RuntimeError("api_keys.spotify.client_id and api_keys.spotify.client_secret are not set")
        with patch(f"{MODULE}.system_note") as note:
            text = commands_instance._run_spotify_login()
        assert text.startswith("spotify_login failed") and "client_id" in text
        note.assert_not_called()

    @pytest.mark.parametrize("run", ["_run_spotify_login", "_run_spotify_login_code"])
    def test_without_a_speaker_system_the_hint_is_the_answer_and_spotify_is_not_asked(self, commands_instance, run):
        account = _account()
        with patch.object(music, "active", return_value=[]), \
                patch.object(music, "no_system_hint", return_value='Sonos & Spotify is disabled: enable_integration("Sonos & Spotify").'), \
                patch.object(SpotifyAccount, "get_instance", return_value=account):
            args = (PASTED,) if run.endswith("code") else ()
            text = getattr(commands_instance, run)(*args)
        assert 'enable_integration("Sonos & Spotify")' in text
        account.login_begin.assert_not_called()
        account.login_finish.assert_not_called()

    def test_the_pasted_address_goes_to_the_background_unquoted_and_whole(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("spotify_login_code", [f'"{PASTED}"']) == ""
        bg.assert_called_once_with(commands_instance._run_spotify_login_code, (PASTED, False), lane=Commands.CONFIG_LANE)

    def test_without_anything_pasted_it_refuses(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("spotify_login_code", [""]).startswith("spotify_login_code failed")
        bg.assert_not_called()

    def test_a_finished_sign_in_names_the_user_and_the_playlist_count_in_the_result_and_a_note(self, commands_instance, spotify):
        with patch(f"{MODULE}.system_note") as note:
            text = commands_instance._run_spotify_login_code(PASTED)
        assert "signed in as Marcus" in text and "3 of the account's 3 playlists" in text and "S-0" in text
        assert "within a minute" in text and "next request" not in text, \
            "the section follows the cache on its own clock — the reply must not promise the ids for the next request"
        assert note.call_args.args[1] == text
        spotify.login_finish.assert_called_once_with(PASTED)

    def test_a_chat_that_finished_the_sign_in_reads_the_outcome_in_the_reply_alone(self, commands_instance, spotify):
        with patch(f"{MODULE}.system_note") as note:
            text = commands_instance._run_spotify_login_code(PASTED, from_chat=True)
        assert "signed in as Marcus" in text
        note.assert_not_called()

    def test_a_signed_in_account_whose_library_did_not_load_says_so_instead_of_a_count(self, commands_instance, spotify):
        spotify.playlists.return_value = []
        spotify.playlists_total.return_value = 0
        spotify.library_error.return_value = "Spotify's token endpoint could not be reached: timed out"
        with patch(f"{MODULE}.system_note") as note:
            text = commands_instance._run_spotify_login_code(PASTED)
        assert "signed in as Marcus" in text and "timed out" in text and "0 of" not in text
        assert note.call_args.args[1] == text

    def test_a_failed_sign_in_comes_back_as_text_without_a_note(self, commands_instance, spotify):
        spotify.login_finish.return_value = "the code expired or was already used — run spotify_login() for a new link"
        with patch(f"{MODULE}.system_note") as note:
            text = commands_instance._run_spotify_login_code("AQold")
        assert "did not complete" in text and "expired" in text
        note.assert_not_called()
