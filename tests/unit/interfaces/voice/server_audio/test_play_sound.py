import time
import numpy as np
from unittest.mock import patch


class TestPlaySoundInit:
    @patch('soundfile.read')
    def test_loads_end_sample(self, mock_sf_read):
        mock_sf_read.side_effect = [
            (np.array([0.4, 0.5, 0.6]), 44100),  # end.wav
        ]

        from interfaces.voice.server_audio.PlaySound import PlaySound
        PlaySound()

        assert mock_sf_read.call_count == 1
        assert mock_sf_read.call_args_list[0][0][0] == 'res/end.wav'

    @patch('soundfile.read')
    def test_loads_end_sample_with_scaling(self, mock_sf_read):
        original_end_sample = np.array([1.0, 0.5, 0.25])
        mock_sf_read.side_effect = [
            (original_end_sample.copy(), 44100),  # end.wav
        ]

        from interfaces.voice.server_audio.PlaySound import PlaySound
        ps = PlaySound()

        # end_sample should be scaled by 0.2
        expected = original_end_sample * 0.2
        np.testing.assert_array_almost_equal(ps.end_sample, expected)


class TestPlayMethods:

    @patch('sounddevice.play')
    @patch('soundfile.read')
    def test_playsound_end_calls_sounddevice(self, mock_sf_read, mock_sd_play):
        end_sample = np.array([1.0, 0.5, 0.25])
        fs2 = 44100
        mock_sf_read.side_effect = [
            (end_sample.copy(), fs2),
        ]

        from interfaces.voice.server_audio.PlaySound import PlaySound
        ps = PlaySound()
        ps.playsound_end()

        mock_sd_play.assert_called_once()
        call_args = mock_sd_play.call_args
        # end_sample should be scaled by 0.2
        np.testing.assert_array_almost_equal(call_args[0][0], end_sample * 0.2)
        assert call_args[0][1] == fs2
        assert call_args[1]['latency'] == 'low'


class TestPlaysoundThinking:
    _INIT_SAMPLES = [
        (np.array([0.4, 0.5, 0.6]), 44100),  # end.wav
    ]

    @patch('interfaces.voice.server_audio.PlaySound.subprocess.Popen')
    @patch('interfaces.voice.server_audio.PlaySound.random.choice')
    @patch('interfaces.voice.server_audio.PlaySound.glob.glob')
    @patch('soundfile.read')
    def test_afplays_random_thinking_sample(self, mock_sf_read, mock_glob, mock_choice, mock_popen):
        mock_sf_read.side_effect = list(self._INIT_SAMPLES)
        mock_glob.return_value = ['res/thinking/en/01_hmm.wav', 'res/thinking/en/02_lets_see.wav']
        mock_choice.return_value = 'res/thinking/en/02_lets_see.wav'

        from interfaces.voice.server_audio.PlaySound import PlaySound
        ps = PlaySound()
        ps.playsound_thinking('en')

        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == ['afplay', 'res/thinking/en/02_lets_see.wav']

    @patch('interfaces.voice.server_audio.PlaySound.subprocess.Popen')
    @patch('interfaces.voice.server_audio.PlaySound.random.choice')
    @patch('interfaces.voice.server_audio.PlaySound.glob.glob')
    @patch('soundfile.read')
    def test_selects_samples_for_requested_language(self, mock_sf_read, mock_glob, mock_choice, mock_popen):
        mock_sf_read.side_effect = list(self._INIT_SAMPLES)
        mock_glob.return_value = [
            'res/thinking/en/01_hmm.wav',
            'res/thinking/sv/01_hmm.wav',
            'res/thinking/sv/02_vi_ska_se.wav',
        ]
        mock_choice.side_effect = lambda paths: paths[0]

        from interfaces.voice.server_audio.PlaySound import PlaySound
        ps = PlaySound()
        # BCP-47 tag is accepted; only the subtag selects the folder.
        ps.playsound_thinking('sv-SE')

        # Only the Swedish samples were offered to random.choice — never English.
        mock_choice.assert_called_once_with(['res/thinking/sv/01_hmm.wav', 'res/thinking/sv/02_vi_ska_se.wav'])
        assert mock_popen.call_args[0][0] == ['afplay', 'res/thinking/sv/01_hmm.wav']

    @patch('interfaces.voice.server_audio.PlaySound.subprocess.Popen')
    @patch('interfaces.voice.server_audio.PlaySound.glob.glob')
    @patch('soundfile.read')
    def test_language_without_samples_is_noop(self, mock_sf_read, mock_glob, mock_popen):
        mock_sf_read.side_effect = list(self._INIT_SAMPLES)
        # English samples exist, but a German request has none — stay silent, no fallback.
        mock_glob.return_value = ['res/thinking/en/01_hmm.wav']

        from interfaces.voice.server_audio.PlaySound import PlaySound
        ps = PlaySound()
        ps.playsound_thinking('de')

        mock_popen.assert_not_called()

    @patch('interfaces.voice.server_audio.PlaySound.subprocess.Popen')
    @patch('interfaces.voice.server_audio.PlaySound.glob.glob')
    @patch('soundfile.read')
    def test_no_samples_is_noop(self, mock_sf_read, mock_glob, mock_popen):
        mock_sf_read.side_effect = list(self._INIT_SAMPLES)
        mock_glob.return_value = []

        from interfaces.voice.server_audio.PlaySound import PlaySound
        ps = PlaySound()
        ps.playsound_thinking('en')

        mock_popen.assert_not_called()

    @patch('interfaces.voice.server_audio.PlaySound.subprocess.Popen')
    @patch('interfaces.voice.server_audio.PlaySound.glob.glob')
    @patch('soundfile.read')
    def test_folder_listed_once_at_init_not_per_play(self, mock_sf_read, mock_glob, mock_popen):
        mock_sf_read.side_effect = list(self._INIT_SAMPLES)
        mock_glob.return_value = ['res/thinking/en/01_hmm.wav']

        from interfaces.voice.server_audio.PlaySound import PlaySound
        ps = PlaySound()
        ps.playsound_thinking('en')
        ps.playsound_thinking('en')

        # Listed once at construction; repeated plays must not re-glob the folder.
        mock_glob.assert_called_once()


class TestTimestamp:

    @patch('sounddevice.play')
    @patch('soundfile.read')
    def test_playsound_end_updates_timestamp(self, mock_sf_read, mock_sd_play):
        mock_sf_read.side_effect = [
            (np.array([0.4, 0.5, 0.6]), 44100),
        ]

        from interfaces.voice.server_audio.PlaySound import PlaySound
        PlaySound.last_time_sound_played_on_server = 0

        ps = PlaySound()
        before = time.time()
        ps.playsound_end()
        after = time.time()

        assert before <= PlaySound.last_time_sound_played_on_server <= after

    @patch('sounddevice.play')
    @patch('soundfile.read')
    def test_timestamp_is_class_variable(self, mock_sf_read, mock_sd_play):
        mock_sf_read.side_effect = [(np.array([0.4, 0.5, 0.6]), 44100)] * 2  # For two instances

        from interfaces.voice.server_audio.PlaySound import PlaySound

        ps1 = PlaySound()
        ps1.playsound_end()
        time1 = PlaySound.last_time_sound_played_on_server

        time.sleep(0.01)

        ps2 = PlaySound()
        ps2.playsound_end()
        time2 = PlaySound.last_time_sound_played_on_server

        # Class variable should have been updated by second play
        assert time2 > time1
