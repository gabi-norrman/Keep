"""interfaces.voice.microphones.FalsePositiveFlood — was Commands.report_fp_flood; the
broadcast tests moved here verbatim from test_commands.TestReportFPFlood."""
from unittest.mock import MagicMock, patch

from interfaces.voice.microphones.FalsePositiveFlood import report_false_positive_flood

MOD = "interfaces.voice.microphones.FalsePositiveFlood"


class TestReportFalsePositiveFlood:
    def test_broadcasts_wakeword_via_run_coroutine_threadsafe(self):
        mock_future = MagicMock()
        with patch(f"{MOD}.outpost_server") as mock_outpost, \
             patch(f"{MOD}.asyncio.run_coroutine_threadsafe", return_value=mock_future) as mock_rcts, \
             patch(f"{MOD}.SpeechAnalyzer"):
            mock_outpost.loop = MagicMock()
            mock_outpost.loop.is_running.return_value = True
            mock_outpost.loop.is_closed.return_value = False
            report_false_positive_flood()
            mock_rcts.assert_called_once()
            mock_future.add_done_callback.assert_called_once()

    def test_same_loop_uses_create_task(self):
        mock_task = MagicMock()
        with patch(f"{MOD}.outpost_server") as mock_outpost, \
             patch(f"{MOD}.asyncio.get_running_loop") as mock_get_running_loop, \
             patch(f"{MOD}.asyncio.run_coroutine_threadsafe") as mock_rcts, \
             patch(f"{MOD}.SpeechAnalyzer"):
            mock_loop = MagicMock()
            mock_loop.is_running.return_value = True
            mock_loop.is_closed.return_value = False
            mock_loop.create_task.return_value = mock_task
            mock_outpost.loop = mock_loop
            mock_get_running_loop.return_value = mock_loop

            report_false_positive_flood()

            mock_loop.create_task.assert_called_once()
            mock_task.add_done_callback.assert_called_once()
            mock_rcts.assert_not_called()

    def test_no_loop_skips_broadcast(self):
        with patch(f"{MOD}.outpost_server") as mock_outpost, \
             patch(f"{MOD}.asyncio.run_coroutine_threadsafe") as mock_rcts, \
             patch(f"{MOD}.SpeechAnalyzer"):
            mock_outpost.loop = None
            report_false_positive_flood()
            mock_rcts.assert_not_called()

    def test_non_running_loop_skips_broadcast(self):
        with patch(f"{MOD}.outpost_server") as mock_outpost, \
             patch(f"{MOD}.asyncio.run_coroutine_threadsafe") as mock_rcts, \
             patch(f"{MOD}.SpeechAnalyzer"):
            mock_outpost.loop = MagicMock()
            mock_outpost.loop.is_running.return_value = False
            mock_outpost.loop.is_closed.return_value = False
            report_false_positive_flood()
            mock_rcts.assert_not_called()

    def test_calls_speech_analyzer(self):
        with patch(f"{MOD}.outpost_server") as mock_outpost, \
             patch(f"{MOD}.asyncio.run_coroutine_threadsafe", return_value=MagicMock()), \
             patch(f"{MOD}.SpeechAnalyzer") as mock_sa:
            mock_outpost.loop = MagicMock()
            mock_outpost.loop.is_running.return_value = True
            mock_outpost.loop.is_closed.return_value = False
            report_false_positive_flood()
            mock_sa.notify_false_positive_flood.assert_called_once()
