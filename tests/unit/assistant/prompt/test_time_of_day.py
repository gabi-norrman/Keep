import pytest
from datetime import datetime
from unittest.mock import patch
from assistant.prompt.TimeOfDay import TimeOfDay


@pytest.mark.unit
class TestTimeOfDay:
    """Tests for TimeOfDay classification logic"""

    def test_weekday_night_time(self):
        """Test night classification on weekdays"""
        # Monday 23:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 6, 23, 0)  # Monday
            assert TimeOfDay.get_current() == TimeOfDay.NIGHT

        # Tuesday 03:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 7, 3, 0)  # Tuesday
            assert TimeOfDay.get_current() == TimeOfDay.NIGHT

        # Wednesday 06:59
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 8, 6, 59)  # Wednesday
            assert TimeOfDay.get_current() == TimeOfDay.NIGHT

    def test_weekday_morning_evening(self):
        """Test morning/late evening classification on weekdays"""
        # Thursday 07:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 9, 7, 0)  # Thursday
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

        # Thursday 07:30
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 9, 7, 30)  # Thursday
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

        # Friday 20:30
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 10, 20, 30)  # Friday
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

        # Friday 22:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 10, 22, 0)  # Friday
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

    def test_weekday_day_time(self):
        """Test day classification on weekdays"""
        # Monday 10:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 6, 10, 0)  # Monday
            assert TimeOfDay.get_current() == TimeOfDay.DAY

        # Wednesday 15:30
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 8, 15, 30)  # Wednesday
            assert TimeOfDay.get_current() == TimeOfDay.DAY

        # Friday 20:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 10, 20, 0)  # Friday
            assert TimeOfDay.get_current() == TimeOfDay.DAY

    def test_weekend_night_time(self):
        """Test night classification on weekends"""
        # Saturday 23:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 11, 23, 0)  # Saturday
            assert TimeOfDay.get_current() == TimeOfDay.NIGHT

        # Sunday 02:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 12, 2, 0)  # Sunday
            assert TimeOfDay.get_current() == TimeOfDay.NIGHT

        # Saturday 08:59
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 11, 8, 59)  # Saturday
            assert TimeOfDay.get_current() == TimeOfDay.NIGHT

    def test_weekend_morning_evening(self):
        """Test morning/late evening classification on weekends"""
        # Saturday 09:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 11, 9, 0)  # Saturday
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

        # Sunday 09:15 (within morning window before 09:30)
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 12, 9, 15)  # Sunday
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

        # Saturday 21:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 11, 21, 0)  # Saturday
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

    def test_weekend_day_time(self):
        """Test day classification on weekends"""
        # Saturday 12:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 11, 12, 0)  # Saturday
            assert TimeOfDay.get_current() == TimeOfDay.DAY

        # Sunday 16:00
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 12, 16, 0)  # Sunday
            assert TimeOfDay.get_current() == TimeOfDay.DAY

    def test_boundary_conditions(self):
        """Test exact boundary times"""
        # Weekday: 07:45 should be MORNING_OR_LATE_EVENING
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 6, 7, 44)  # Monday 07:44
            assert TimeOfDay.get_current() == TimeOfDay.MORNING_OR_LATE_EVENING

        # Weekday: 07:45 should transition to DAY
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 6, 7, 45)  # Monday 07:45
            assert TimeOfDay.get_current() == TimeOfDay.DAY

        # Weekend: 10:00 should be DAY
        with patch('assistant.prompt.TimeOfDay.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 1, 11, 10, 0)  # Saturday 10:00
            assert TimeOfDay.get_current() == TimeOfDay.DAY
