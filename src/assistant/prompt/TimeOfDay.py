from __future__ import annotations
from enum import Enum
from datetime import datetime


class TimeOfDay(Enum):
    """Classifies the current clock time as night, morning/late-evening or day."""

    NIGHT = "NIGHT"
    MORNING_OR_LATE_EVENING = "MORNING_OR_LATE_EVENING"
    DAY = "DAY"

    @classmethod
    def get_current(cls) -> TimeOfDay:
        """
        Returns the time period based on the given datetime (or current time if None).

        Weekdays (Mon-Fri):
        - NIGHT: 22:30-07:00
        - MORNING_OR_LATE_EVENING: 07:00-07:45 or 20:30-22:30
        - DAY: Rest

        Weekends (Sat-Sun):
        - NIGHT: 22:30-09:00
        - MORNING_OR_LATE_EVENING: 09:00-10:00 or 21:00-22:30
        - DAY: Rest
        """
        dt = datetime.now()

        # Get time in minutes since midnight for easier comparison
        minutes = dt.hour * 60 + dt.minute

        # Check if it's a weekend (Saturday=5, Sunday=6)
        is_weekend = dt.weekday() >= 5

        if is_weekend:
            # Weekend schedule
            if minutes >= 22 * 60 + 30 or minutes < 9 * 60:  # 22:30-09:00
                return cls.NIGHT
            elif (8.5 * 60 <= minutes < 9.5 * 60) or (21 * 60 <= minutes < 22 * 60 + 30):  # 09:00-10:00 or 21:00-22:30
                return cls.MORNING_OR_LATE_EVENING
            else:
                return cls.DAY
        else:
            # Weekday schedule
            if minutes >= 22 * 60 + 30 or minutes < 7 * 60:  # 22:30-07:00
                return cls.NIGHT
            elif (7 * 60 <= minutes < 7 * 60 + 45) or (
                    20 * 60 + 30 <= minutes < 22 * 60 + 30):  # 07:00-07:45 or 20:30-22:30
                return cls.MORNING_OR_LATE_EVENING
            else:
                return cls.DAY
