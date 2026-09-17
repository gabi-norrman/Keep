import numpy as np
import pytest
from unittest.mock import Mock, patch

from interfaces.voice.server_audio.DynamicServerVolumeController import DynamicServerVolumeController

MODULE = 'interfaces.voice.server_audio.DynamicServerVolumeController'


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton instance before each test."""
    DynamicServerVolumeController.reset_instance()
    yield
    DynamicServerVolumeController.reset_instance()


class TestRmsToDb:
    def test_typical_rms_value(self):
        rms = 0.01
        db = DynamicServerVolumeController.rms_to_db(rms)
        # 20 * log10(0.01 / 1e-4) = 20 * log10(100) = 20 * 2 = 40
        assert db == pytest.approx(40, rel=0.01)

    def test_very_small_rms(self):
        rms = 1e-10
        db = DynamicServerVolumeController.rms_to_db(rms)
        # Should use the floor value 1e-10, giving 20 * log10(1e-10 / 1e-4) = 20 * log10(1e-6) = 20 * -6 = -120
        assert db == pytest.approx(-120, rel=0.01)

    def test_edge_case_rms_near_reference(self):
        rms = 1e-4  # Same as reference
        db = DynamicServerVolumeController.rms_to_db(rms)
        # 20 * log10(1e-4 / 1e-4) = 20 * log10(1) = 0
        assert db == pytest.approx(0, rel=0.01)

    def test_rms_value_one(self):
        rms = 1.0
        db = DynamicServerVolumeController.rms_to_db(rms)
        # 20 * log10(1 / 1e-4) = 20 * log10(10000) = 20 * 4 = 80
        assert db == pytest.approx(80, rel=0.01)


class TestNoiseToVolume:
    def test_noise_below_minimum_clips_to_min(self):
        controller = DynamicServerVolumeController(
            min_noise_db=20.0, max_noise_db=50.0,
            min_volume=45, max_volume=100
        )
        volume = controller.noise_to_volume(10.0)  # Below min_noise_db
        assert volume == 45

    def test_noise_above_maximum_clips_to_max(self):
        controller = DynamicServerVolumeController(
            min_noise_db=20.0, max_noise_db=50.0,
            min_volume=45, max_volume=100
        )
        volume = controller.noise_to_volume(60.0)  # Above max_noise_db
        assert volume == 100

    def test_noise_in_range_normalizes_correctly(self):
        controller = DynamicServerVolumeController(
            min_noise_db=20.0, max_noise_db=50.0,
            min_volume=40, max_volume=100
        )
        # At midpoint noise (35 dB), should be midpoint volume
        volume = controller.noise_to_volume(35.0)
        expected = 40 + (100 - 40) * 0.5  # = 40 + 30 = 70
        assert volume == 70

    def test_noise_at_minimum_returns_min_volume(self):
        controller = DynamicServerVolumeController(
            min_noise_db=20.0, max_noise_db=50.0,
            min_volume=45, max_volume=100
        )
        volume = controller.noise_to_volume(20.0)
        assert volume == 45

    def test_noise_at_maximum_returns_max_volume(self):
        controller = DynamicServerVolumeController(
            min_noise_db=20.0, max_noise_db=50.0,
            min_volume=45, max_volume=100
        )
        volume = controller.noise_to_volume(50.0)
        assert volume == 100


class TestCalculateRms:
    def test_silence_returns_zero(self):
        audio_data = np.zeros(1000)
        rms = DynamicServerVolumeController.calculate_rms(audio_data)
        assert rms == 0.0

    def test_known_signal_returns_expected_rms(self):
        # Sine wave with amplitude 1 has RMS of 1/sqrt(2) ≈ 0.707
        t = np.linspace(0, 2 * np.pi, 10000)
        audio_data = np.sin(t)
        rms = DynamicServerVolumeController.calculate_rms(audio_data)
        assert rms == pytest.approx(1.0 / np.sqrt(2), rel=0.01)

    def test_constant_signal_returns_that_value(self):
        audio_data = np.full(1000, 0.5)
        rms = DynamicServerVolumeController.calculate_rms(audio_data)
        # RMS of constant signal is the constant value itself
        assert rms == pytest.approx(0.5, rel=0.01)

    def test_handles_single_element_array(self):
        audio_data = np.array([0.3])
        rms = DynamicServerVolumeController.calculate_rms(audio_data)
        assert rms == pytest.approx(0.3, rel=0.01)


class TestSetSystemVolume:
    @patch('subprocess.run')
    def test_calls_osascript_with_correct_volume(self, mock_run):
        mock_run.return_value = Mock()
        result = DynamicServerVolumeController.set_system_volume(75)
        assert result is True
        mock_run.assert_called_once_with(
            ["osascript", "-e", "set volume output volume 75"],
            check=True,
            capture_output=True
        )

    @patch('subprocess.run')
    def test_handles_subprocess_error(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(1, 'osascript')
        result = DynamicServerVolumeController.set_system_volume(50)
        assert result is False


class TestSingleton:
    def test_get_instance_returns_same_instance(self):
        instance1 = DynamicServerVolumeController.get_instance()
        instance2 = DynamicServerVolumeController.get_instance()
        assert instance1 is instance2

    def test_reset_instance_clears_singleton(self):
        instance1 = DynamicServerVolumeController.get_instance()
        DynamicServerVolumeController.reset_instance()
        instance2 = DynamicServerVolumeController.get_instance()
        assert instance1 is not instance2

    def test_get_instance_passes_kwargs(self):
        instance = DynamicServerVolumeController.get_instance(min_volume=60, max_volume=90)
        assert instance.min_volume == 60
        assert instance.max_volume == 90


class TestStartStop:
    @patch('threading.Thread')
    def test_start_creates_threads(self, mock_thread_class):
        mock_thread = Mock()
        mock_thread_class.return_value = mock_thread

        controller = DynamicServerVolumeController()
        controller.start()

        assert controller.monitoring is True
        assert mock_thread_class.call_count == 2
        assert mock_thread.start.call_count == 2

    @patch('threading.Thread')
    def test_start_does_nothing_if_already_monitoring(self, mock_thread_class):
        controller = DynamicServerVolumeController()
        controller.monitoring = True

        controller.start()

        mock_thread_class.assert_not_called()

    def test_stop_sets_stop_event(self):
        controller = DynamicServerVolumeController()
        controller.monitoring = True
        controller._stop_event.clear()

        controller.stop()

        assert controller.monitoring is False
        assert controller._stop_event.is_set()

    def test_stop_cancels_pause_timer(self):
        controller = DynamicServerVolumeController()
        mock_timer = Mock()
        controller._pause_timer = mock_timer

        controller.stop()

        mock_timer.cancel.assert_called_once()
        assert controller._pause_timer is None


class TestPause:
    @patch('threading.Timer')
    def test_pause_sets_paused_flag(self, mock_timer_class):
        mock_timer = Mock()
        mock_timer_class.return_value = mock_timer

        controller = DynamicServerVolumeController()
        controller.pause()

        assert controller.paused is True

    @patch('threading.Timer')
    def test_pause_creates_timer_for_auto_resume(self, mock_timer_class):
        mock_timer = Mock()
        mock_timer_class.return_value = mock_timer

        controller = DynamicServerVolumeController()
        controller.pause()

        mock_timer_class.assert_called_once()
        args = mock_timer_class.call_args
        assert args[0][0] == 5.0  # 5 second delay
        mock_timer.start.assert_called_once()

    @patch('threading.Timer')
    def test_pause_resets_timer_if_called_again(self, mock_timer_class):
        mock_timer1 = Mock()
        mock_timer2 = Mock()
        mock_timer_class.side_effect = [mock_timer1, mock_timer2]

        controller = DynamicServerVolumeController()
        controller.pause()
        controller.pause()

        mock_timer1.cancel.assert_called_once()
        assert mock_timer_class.call_count == 2


class TestAutoResume:
    def test_auto_resume_clears_paused_flag(self):
        controller = DynamicServerVolumeController()
        controller.paused = True
        controller._pause_timer = Mock()

        controller._auto_resume()

        assert controller.paused is False
        assert controller._pause_timer is None


class TestResolveInputDevice:
    """The noise monitor used to crash at boot with PortAudioError when sounddevice
    reported -1 ("no default device") — sd.InputStream raises on that index, the
    thread died, and noise-adaptive volume was dead for the whole session even after
    a real device became available. _resolve_input_device translates -1 / None into
    "not ready yet" so _monitor_noise can retry instead of crashing."""

    def test_returns_explicit_device_index_when_set(self):
        controller = DynamicServerVolumeController(device_index=3)
        # Configured devices bypass the default-device probe entirely — even if the
        # OS has no default mic, the explicit index must be honored as-is.
        with patch('interfaces.voice.server_audio.DynamicServerVolumeController.sd') as mock_sd:
            mock_sd.default.device = [-1, -1]
            assert controller._resolve_input_device() == 3

    def test_returns_none_when_default_input_is_minus_one(self):
        controller = DynamicServerVolumeController()
        with patch('interfaces.voice.server_audio.DynamicServerVolumeController.sd') as mock_sd:
            mock_sd.default.device = [-1, 0]  # sounddevice's "no default input"
            assert controller._resolve_input_device() is None

    def test_returns_none_when_default_input_is_none(self):
        controller = DynamicServerVolumeController()
        with patch('interfaces.voice.server_audio.DynamicServerVolumeController.sd') as mock_sd:
            mock_sd.default.device = [None, 0]
            assert controller._resolve_input_device() is None

    def test_returns_default_input_index_when_valid(self):
        controller = DynamicServerVolumeController()
        with patch(f'{MODULE}.sd') as mock_sd, patch(f'{MODULE}.config') as mock_config:
            mock_config.get.return_value = []
            mock_sd.default.device = [2, 5]  # input=2 (valid), output=5
            assert controller._resolve_input_device() == 2

    # A Bluetooth headset becomes the macOS default input when it connects. Excluding it from
    # the outposts left it open here (2026-09-17 FTUE run): the headset stayed in its call profile.
    DEVICES = [
        {"name": "Acme Headset", "max_input_channels": 1},
        {"name": "Studio Display Speakers", "max_input_channels": 0},
        {"name": "Studio Display Microphone", "max_input_channels": 1},
    ]

    def _resolve(self, default_input, exclude, devices=None):
        controller = DynamicServerVolumeController()
        with patch(f'{MODULE}.sd') as mock_sd, patch(f'{MODULE}.config') as mock_config:
            mock_config.get.return_value = exclude
            mock_sd.default.device = [default_input, 1]
            mock_sd.query_devices.return_value = devices if devices is not None else self.DEVICES
            return controller._resolve_input_device()

    def test_excluded_default_input_falls_back_to_first_allowed_mic(self):
        assert self._resolve(0, ["acme headset"]) == 2

    def test_default_input_kept_when_not_excluded(self):
        assert self._resolve(2, ["acme headset"]) == 2

    def test_none_when_every_mic_is_excluded(self):
        assert self._resolve(0, ["acme", "studio display"]) is None

    def test_exclude_written_as_a_single_string(self):
        assert self._resolve(0, "Acme Headset") == 2

    def test_returns_none_when_probing_default_raises(self):
        controller = DynamicServerVolumeController()
        with patch('interfaces.voice.server_audio.DynamicServerVolumeController.sd') as mock_sd:
            # Access to sd.default.device blows up (corrupt portaudio state, etc).
            # Must NOT propagate — _monitor_noise treats None as "retry later".
            type(mock_sd.default).device = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
            assert controller._resolve_input_device() is None


class TestMonitorNoiseRecovery:
    """The monitor thread used to die permanently on the first PortAudioError. It
    now retries with backoff and resumes once a device appears, which is the whole
    point of the fix — without this, a brief unavailability at boot disabled
    noise-adaptive volume for the entire session."""

    def test_monitor_exits_promptly_when_stop_event_set_before_start(self):
        # If stop was already signalled (e.g. controller.stop() called before the
        # thread got scheduled), _monitor_noise must return without ever touching
        # sounddevice. Regression guard against an infinite loop on shutdown.
        controller = DynamicServerVolumeController()
        controller._stop_event.set()

        with patch('interfaces.voice.server_audio.DynamicServerVolumeController.LOCAL_DEVELOPMENT_MODE', False), \
             patch('interfaces.voice.server_audio.DynamicServerVolumeController.sd') as mock_sd:
            mock_sd.default.device = [-1, 0]
            # If the loop ever entered InputStream the test would hang here.
            controller._monitor_noise()
            mock_sd.InputStream.assert_not_called()

    def test_monitor_retries_after_no_device_then_exits_on_stop(self):
        # First _resolve_input_device call: no device (None). Loop waits on
        # _stop_event with backoff. We set _stop_event during that wait so the
        # second iteration's check exits the loop. No InputStream should ever be
        # opened — we never got to a real device.
        controller = DynamicServerVolumeController()

        # Make _stop_event.wait actually set the event after one short backoff,
        # so the next while-condition check exits the loop instead of looping
        # forever in the test.
        original_wait = controller._stop_event.wait
        wait_calls = []

        def wait_and_stop(timeout=None):
            wait_calls.append(timeout)
            controller._stop_event.set()
            return original_wait(0)

        controller._stop_event.wait = wait_and_stop

        with patch('interfaces.voice.server_audio.DynamicServerVolumeController.LOCAL_DEVELOPMENT_MODE', False), \
             patch('interfaces.voice.server_audio.DynamicServerVolumeController.sd') as mock_sd:
            mock_sd.default.device = [-1, 0]  # no default input
            controller._monitor_noise()

        # We must have waited at least once (backoff after detecting no device)
        # and never tried to open a stream.
        assert wait_calls, "must have entered the no-device backoff branch"
        assert mock_sd.InputStream.call_count == 0

    def test_monitor_local_development_mode_honors_stop_event(self):
        # LOCAL_DEVELOPMENT_MODE used to be `while True:` and only the thread's
        # daemon flag would kill it on shutdown. Now it must honor _stop_event so
        # controller.stop() unblocks the test (and prod) suite cleanly.
        controller = DynamicServerVolumeController()
        controller._stop_event.set()  # stop before entering

        with patch('interfaces.voice.server_audio.DynamicServerVolumeController.LOCAL_DEVELOPMENT_MODE', True):
            controller._monitor_noise()  # must return immediately, not loop forever

        # noise_history should not have been populated because the loop never ran.
        assert len(controller.noise_history) == 0


class TestAudioCallback:
    def test_audio_callback_adds_to_history_when_monitoring(self):
        controller = DynamicServerVolumeController()
        controller.monitoring = True
        controller.paused = False

        # Create fake audio data
        audio_data = np.random.randn(100, 1).astype(np.float32) * 0.1
        controller.audio_callback(audio_data, 100, None, None)

        assert len(controller.noise_history) == 1

    def test_audio_callback_ignores_when_paused(self):
        controller = DynamicServerVolumeController()
        controller.monitoring = True
        controller.paused = True

        audio_data = np.random.randn(100, 1).astype(np.float32) * 0.1
        controller.audio_callback(audio_data, 100, None, None)

        assert len(controller.noise_history) == 0

    def test_audio_callback_ignores_when_not_monitoring(self):
        controller = DynamicServerVolumeController()
        controller.monitoring = False
        controller.paused = False

        audio_data = np.random.randn(100, 1).astype(np.float32) * 0.1
        controller.audio_callback(audio_data, 100, None, None)

        assert len(controller.noise_history) == 0
