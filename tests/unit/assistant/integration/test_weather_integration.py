import datetime as real_dt
from unittest.mock import patch, Mock

import pytest

from assistant.integration.integrations.WeatherIntegration import WeatherIntegration


def _make_timeseries_entry(time_str, details, next_6h_precip=None, next_6h_symbol=None):
    entry = {
        'time': time_str,
        'data': {
            'instant': {
                'details': details
            }
        }
    }
    if next_6h_precip is not None:
        entry['data']['next_6_hours'] = {
            'details': {'precipitation_amount': next_6h_precip},
            'summary': {'symbol_code': next_6h_symbol or 'cloudy'}
        }
    return entry


def _make_details(temp=5.0, pressure=1013.0, clouds=50.0, humidity=70.0,
                  wind_dir=180.0, wind_speed=3.0):
    return {
        'air_temperature': temp,
        'air_pressure_at_sea_level': pressure,
        'cloud_area_fraction': clouds,
        'relative_humidity': humidity,
        'wind_from_direction': wind_dir,
        'wind_speed': wind_speed,
    }


def _build_forecast_response(entries):
    return {
        'properties': {
            'timeseries': entries
        }
    }


@pytest.mark.unit
class TestRemoveDecimals:

    def test_rounds_half_to_even(self):
        # Python uses banker's rounding: 0.5 rounds to nearest even
        assert WeatherIntegration.remove_decimals(4.5) == 4
        assert WeatherIntegration.remove_decimals(5.5) == 6

    def test_string_input(self):
        assert WeatherIntegration.remove_decimals("5.7") == 6

@pytest.mark.unit
class TestFetchWeatherForecast:

    @pytest.fixture
    def mock_config(self):
        with patch('assistant.integration.integrations.WeatherIntegration.config') as mock_cfg:
            mock_cfg.get_home_latitude.return_value = 59.91
            mock_cfg.get_home_longitude.return_value = 10.75
            # UTC so the UTC-stamped fixtures below match the local sampling hours directly.
            mock_cfg.get_home_timezone.return_value = "UTC"
            yield mock_cfg

    def _mock_response(self, json_data, status_code=200):
        resp = Mock()
        resp.status_code = status_code
        resp.json.return_value = json_data
        return resp

    def _build_detailed_entries(self, count=8):
        """Build entries at hours that pass the detailed filter (10, 13, 16, 19, 22)."""
        detailed_hours = ["10", "13", "16", "19", "22"]
        entries = []
        day = 15
        hour_idx = 0
        for i in range(count):
            h = detailed_hours[hour_idx % len(detailed_hours)]
            time_str = f"2024-01-{day:02d}T{h}:00:00Z"
            entries.append(_make_timeseries_entry(
                time_str,
                _make_details(temp=5.0 + i, wind_speed=2.0 + i * 0.5),
                next_6h_precip=0.5 if i % 2 == 0 else 0.0,
                next_6h_symbol='rain' if i % 2 == 0 else 'fair_day'
            ))
            hour_idx += 1
            if hour_idx % len(detailed_hours) == 0:
                day += 1
        return entries

    def _build_non_detailed_entries(self, count=4, start_day=17):
        """Build entries at hours that pass the non-detailed filter (12, 18)."""
        non_detailed_hours = ["12", "18"]
        entries = []
        day = start_day
        for i in range(count):
            h = non_detailed_hours[i % len(non_detailed_hours)]
            if i > 0 and i % len(non_detailed_hours) == 0:
                day += 1
            time_str = f"2024-01-{day:02d}T{h}:00:00Z"
            entries.append(_make_timeseries_entry(
                time_str,
                _make_details(temp=0.0 + i, wind_speed=5.0),
                next_6h_precip=1.5,
                next_6h_symbol='heavyrain'
            ))
        return entries

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_includes_temperature_and_wind_speed(self, mock_get, mock_config):
        entries = [_make_timeseries_entry(
            "2024-01-15T10:00:00Z",
            _make_details(temp=7.3, wind_speed=4.8),
        )]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert "air_temperature: 7" in result
        assert "wind_speed: 5" in result

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_excludes_pressure_humidity_wind_direction(self, mock_get, mock_config):
        entries = [_make_timeseries_entry(
            "2024-01-15T10:00:00Z",
            _make_details(pressure=1013.5, humidity=80.0, wind_dir=200.0),
        )]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert "air_pressure_at_sea_level" not in result
        assert "relative_humidity" not in result
        assert "wind_from_direction" not in result

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_includes_cloud_fraction_in_detailed_entries(self, mock_get, mock_config):
        entries = [_make_timeseries_entry(
            "2024-01-15T10:00:00Z",
            _make_details(clouds=75.0),
        )]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert "cloud_area_fraction: 75" in result

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_excludes_cloud_fraction_in_non_detailed_entries(self, mock_get, mock_config):
        detailed = self._build_detailed_entries(8)
        non_detailed = [_make_timeseries_entry(
            "2024-01-17T12:00:00Z",
            _make_details(clouds=90.0),
        )]
        mock_get.return_value = self._mock_response(
            _build_forecast_response(detailed + non_detailed)
        )

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=False)

        lines = result.split('\n')
        # Find lines after the 9th "Weather at" header (the non-detailed entry)
        weather_headers = [i for i, l in enumerate(lines) if l.startswith("Weather at")]
        assert len(weather_headers) >= 9
        non_detailed_section = '\n'.join(lines[weather_headers[8]:])
        assert "cloud_area_fraction" not in non_detailed_section

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_includes_precipitation_when_positive(self, mock_get, mock_config):
        entries = [_make_timeseries_entry(
            "2024-01-15T10:00:00Z",
            _make_details(),
            next_6h_precip=2.3,
            next_6h_symbol='rain'
        )]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert "precipitation_amount: 2" in result
        assert "symbol: rain" in result

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_shows_zero_precipitation_when_source_present(self, mock_get, mock_config):
        # "0" is information: a dry forecast must be distinguishable from missing data.
        entries = [_make_timeseries_entry(
            "2024-01-15T10:00:00Z",
            _make_details(),
            next_6h_precip=0.0,
            next_6h_symbol='fair_day'
        )]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert "precipitation_amount: 0" in result
        assert "symbol: fair_day" in result

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_accumulates_precipitation_from_skipped_hours(self, mock_get, mock_config):
        # Hours that aren't rendered (11:00, 12:00) must still have their rain counted —
        # it rolls forward into the preceding rendered row (10:00). This is the fix for the
        # overnight gap that hid the morning rain.
        def hourly_entry(time_str, next1_precip, next6=None, symbol=None):
            entry = _make_timeseries_entry(
                time_str, _make_details(), next_6h_precip=next6, next_6h_symbol=symbol)
            entry['data']['next_1_hours'] = {
                'details': {'precipitation_amount': next1_precip},
                'summary': {'symbol_code': 'rain'},
            }
            return entry

        entries = [
            hourly_entry("2024-01-15T10:00:00Z", 1.0, next6=1.0, symbol='rain'),  # rendered
            hourly_entry("2024-01-15T11:00:00Z", 4.0),                            # skipped
            hourly_entry("2024-01-15T12:00:00Z", 5.0),                            # skipped
            hourly_entry("2024-01-15T13:00:00Z", 2.0, next6=0.0, symbol='cloudy'),  # rendered
        ]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        # 10:00 row window = 10:00 + skipped 11:00 + skipped 12:00 = 1 + 4 + 5 = 10mm
        assert "precipitation_amount: 10" in result
        # 13:00 row window = its own hour only = 2mm
        assert "precipitation_amount: 2" in result

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_skips_entries_at_non_matching_hours(self, mock_get, mock_config):
        entries = [
            _make_timeseries_entry("2024-01-15T08:00:00Z", _make_details(temp=1.0)),
            _make_timeseries_entry("2024-01-15T09:00:00Z", _make_details(temp=2.0)),
            _make_timeseries_entry("2024-01-15T10:00:00Z", _make_details(temp=3.0)),
            _make_timeseries_entry("2024-01-15T11:00:00Z", _make_details(temp=4.0)),
        ]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        # Only T10 should pass the filter, not T08, T09, T11
        assert "air_temperature: 3" in result
        assert result.count("Weather at") == 1

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_short_term_stops_after_9_entries(self, mock_get, mock_config):
        """Short term forecast breaks after num_entries > 8, so 9 entries total
        (8 detailed + 1 non-detailed before the break triggers)."""
        detailed = self._build_detailed_entries(8)
        non_detailed = self._build_non_detailed_entries(4)
        mock_get.return_value = self._mock_response(
            _build_forecast_response(detailed + non_detailed)
        )

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert result.count("Weather at") == 9

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_terminal_short_term_row_includes_skipped_hour_rain(self, mock_get, mock_config):
        # Regression: the final row of a short-term forecast must still sum the rain from the
        # skipped hours that complete its window. The break used to fire the moment that row
        # was rendered, truncating its window to a single hour and letting window_has_hourly
        # shadow the next_6_hours fallback — re-hiding rain in the gap after the last row.
        def hourly_entry(time_str, next1_precip, next6=None, symbol=None):
            entry = _make_timeseries_entry(
                time_str, _make_details(), next_6h_precip=next6, next_6h_symbol=symbol)
            entry['data']['next_1_hours'] = {
                'details': {'precipitation_amount': next1_precip},
                'summary': {'symbol_code': 'rain'},
            }
            return entry

        entries = self._build_detailed_entries(8)  # 8 rendered rows -> num_entries == 8
        entries += [
            hourly_entry("2024-01-16T18:00:00Z", 1.0, next6=0.5, symbol='rain'),  # 9th (terminal) row
            hourly_entry("2024-01-16T19:00:00Z", 3.0),                            # skipped, in its window
            hourly_entry("2024-01-16T20:00:00Z", 4.0),                            # skipped, in its window
            hourly_entry("2024-01-17T12:00:00Z", 9.0),                            # next shown row -> stop
        ]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert result.count("Weather at") == 9                          # still capped at 9 rows
        assert "Weather at 2024-01-17 12:00 (local):" not in result     # 10th row not rendered
        # Terminal window = 18:00 + skipped 19:00 + 20:00 = 1 + 3 + 4 = 8mm, not just its own 1mm hour.
        assert "precipitation_amount: 8" in result

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_long_term_continues_past_8_entries(self, mock_get, mock_config):
        detailed = self._build_detailed_entries(8)
        non_detailed = self._build_non_detailed_entries(4)
        mock_get.return_value = self._mock_response(
            _build_forecast_response(detailed + non_detailed)
        )

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=False)

        assert result.count("Weather at") > 8

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_non_200_raises_runtime_error(self, mock_get, mock_config):
        mock_get.return_value = self._mock_response({}, status_code=500)

        with pytest.raises(RuntimeError, match="Failed to fetch weather"):
            WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_excludes_precipitation_amount_from_instant_details(self, mock_get, mock_config):
        details = _make_details()
        details['precipitation_amount'] = 1.5
        entries = [_make_timeseries_entry("2024-01-15T10:00:00Z", details)]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        lines = [l.strip() for l in result.split('\n') if l.strip().startswith('precipitation')]
        assert len(lines) == 0

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_samples_and_renders_in_home_local_time(self, mock_get, mock_config):
        # Sampling hours are home-local, not UTC. In summer Stockholm is UTC+2, so the
        # 05:00Z entry is 07:00 local and must be rendered; 06:00Z (08:00 local) must not.
        mock_config.get_home_timezone.return_value = "Europe/Stockholm"
        entries = [
            _make_timeseries_entry("2024-06-15T05:00:00Z", _make_details(temp=11.0)),  # 07:00 local
            _make_timeseries_entry("2024-06-15T06:00:00Z", _make_details(temp=12.0)),  # 08:00 local
            _make_timeseries_entry("2024-06-15T08:00:00Z", _make_details(temp=13.0)),  # 10:00 local
        ]
        mock_get.return_value = self._mock_response(_build_forecast_response(entries))

        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        assert result.count("Weather at") == 2
        assert "Weather at 2024-06-15 07:00 (local):" in result
        assert "Weather at 2024-06-15 10:00 (local):" in result
        assert "air_temperature: 12" not in result  # the 08:00-local entry was skipped

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_sends_correct_url_with_coordinates(self, mock_get, mock_config):
        mock_get.return_value = self._mock_response(
            _build_forecast_response([
                _make_timeseries_entry("2024-01-15T10:00:00Z", _make_details())
            ])
        )

        WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)

        call_args = mock_get.call_args
        url = call_args[0][0]
        assert "lat=59.91" in url
        assert "lon=10.75" in url
        assert "User-Agent" in call_args[1]['headers']


@pytest.mark.unit
class TestFetchShortTermPrecipitation:

    @pytest.fixture
    def mock_config(self):
        with patch('assistant.integration.integrations.WeatherIntegration.config') as mock_cfg:
            mock_cfg.get_home_latitude.return_value = 59.91
            mock_cfg.get_home_longitude.return_value = 10.75
            yield mock_cfg

    @pytest.fixture
    def fixed_now(self):
        return real_dt.datetime(2024, 1, 15, 12, 0, 0, tzinfo=real_dt.timezone.utc)

    def _mock_response(self, json_data, status_code=200):
        resp = Mock()
        resp.status_code = status_code
        resp.json.return_value = json_data
        return resp

    def _make_precip_entry(self, time_str, rate):
        return {
            'time': time_str,
            'data': {
                'instant': {
                    'details': {
                        'precipitation_rate': rate
                    }
                }
            }
        }

    def _build_precip_response(self, entries):
        return {
            'properties': {
                'timeseries': entries
            }
        }

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_groups_into_15_minute_buckets(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [
            self._make_precip_entry("2024-01-15T12:00:00Z", 0.5),
            self._make_precip_entry("2024-01-15T12:05:00Z", 0.3),
            self._make_precip_entry("2024-01-15T12:15:00Z", 1.0),
        ]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        result = WeatherIntegration.fetch_short_term_precipitation()

        # Bucket 0 (0-14 min): mean(0.5, 0.3) = 0.4; bucket 1 (15-29 min): mean(1.0) = 1.0
        assert result == "Right now (next 15 min): 0.4 | +15–30 min: 1.0"

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_zero_precipitation_shows_as_zero(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [
            self._make_precip_entry("2024-01-15T12:00:00Z", 0.0),
        ]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        result = WeatherIntegration.fetch_short_term_precipitation()

        assert result == "Right now (next 15 min): 0"

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_ignores_past_timestamps(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [
            self._make_precip_entry("2024-01-15T11:50:00Z", 5.0),  # past
            self._make_precip_entry("2024-01-15T12:00:00Z", 1.0),  # now (delta=0, included)
            self._make_precip_entry("2024-01-15T12:10:00Z", 2.0),  # future
        ]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        result = WeatherIntegration.fetch_short_term_precipitation()

        # Only bucket 0 should exist with mean(1.0, 2.0) = 1.5
        assert result == "Right now (next 15 min): 1.5"

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_rounds_to_one_decimal(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [
            self._make_precip_entry("2024-01-15T12:00:00Z", 0.36),
            self._make_precip_entry("2024-01-15T12:05:00Z", 0.38),
        ]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        result = WeatherIntegration.fetch_short_term_precipitation()

        # mean(0.36, 0.38) = 0.37 → rounds to 0.4
        assert result == "Right now (next 15 min): 0.4"

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_multiple_buckets_comma_separated(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [
            self._make_precip_entry("2024-01-15T12:00:00Z", 1.0),
            self._make_precip_entry("2024-01-15T12:15:00Z", 2.0),
            self._make_precip_entry("2024-01-15T12:30:00Z", 3.0),
            self._make_precip_entry("2024-01-15T12:45:00Z", 0.0),
        ]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        result = WeatherIntegration.fetch_short_term_precipitation()

        expected = (
            "Right now (next 15 min): 1.0 | "
            "+15–30 min: 2.0 | "
            "+30–45 min: 3.0 | "
            "+45–60 min: 0"
        )
        assert result == expected

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_hour_two_buckets_collapsed(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [
            self._make_precip_entry("2024-01-15T12:00:00Z", 0.5),
            self._make_precip_entry("2024-01-15T13:00:00Z", 0.1),  # bucket 4
            self._make_precip_entry("2024-01-15T13:15:00Z", 0.2),  # bucket 5
            self._make_precip_entry("2024-01-15T13:30:00Z", 0.0),  # bucket 6
            self._make_precip_entry("2024-01-15T13:45:00Z", 1.5),  # bucket 7
        ]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        result = WeatherIntegration.fetch_short_term_precipitation()

        expected = (
            "Right now (next 15 min): 0.5 | "
            "+1h–2h: 0.1, 0.2, 0, 1.5"
        )
        assert result == expected

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_non_200_raises_runtime_error(self, mock_get, mock_config):
        mock_get.return_value = self._mock_response({}, status_code=500)

        with pytest.raises(RuntimeError, match="Failed to fetch precipitation"):
            WeatherIntegration.fetch_short_term_precipitation()

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_radar_coverage_unavailable_returns_message(self, mock_get, mock_config):
        payload = {
            'properties': {
                'meta': {'radar_coverage': 'temporarily unavailable'},
                'timeseries': [
                    {
                        'time': '2024-01-15T12:00:00Z',
                        'data': {'instant': {'details': {'air_temperature': 14.1}}}
                    }
                ]
            }
        }
        mock_get.return_value = self._mock_response(payload)

        result = WeatherIntegration.fetch_short_term_precipitation()

        assert "unavailable" in result.lower()

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_missing_precipitation_rate_skipped(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [
            {
                'time': '2024-01-15T12:00:00Z',
                'data': {'instant': {'details': {'air_temperature': 14.1}}}
            }
        ]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        result = WeatherIntegration.fetch_short_term_precipitation()

        assert "unavailable" in result.lower()

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    @patch('assistant.integration.integrations.WeatherIntegration.dt')
    def test_sends_correct_url(self, mock_dt, mock_get, mock_config, fixed_now):
        mock_dt.datetime.now.return_value = fixed_now
        mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
        mock_dt.timezone = real_dt.timezone

        entries = [self._make_precip_entry("2024-01-15T12:00:00Z", 0.0)]
        mock_get.return_value = self._mock_response(self._build_precip_response(entries))

        WeatherIntegration.fetch_short_term_precipitation()

        url = mock_get.call_args[0][0]
        assert "nowcast" in url
        assert "lat=59.91" in url
        assert "lon=10.75" in url


class TestNotConfigured:
    """Without location.home the forecast and the nowcast say so instead of asking met.no
    for lat=None (a fresh install has no home location yet)."""

    @pytest.fixture
    def no_home(self):
        with patch('assistant.integration.integrations.WeatherIntegration.config') as cfg:
            cfg.get_home_latitude.return_value = None
            cfg.get_home_longitude.return_value = None
            yield cfg

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_forecast_reports_the_missing_location_without_a_request(self, mock_get, no_home):
        result = WeatherIntegration.fetch_weather_forecast(short_term_forecast=True)
        assert result == "(Weather not configured: location.home is missing)"
        mock_get.assert_not_called()

    @patch('assistant.integration.integrations.WeatherIntegration.requests.get')
    def test_precipitation_reports_the_missing_location_without_a_request(self, mock_get, no_home):
        result = WeatherIntegration.fetch_short_term_precipitation()
        assert result == "(Precipitation not configured: location.home is missing)"
        mock_get.assert_not_called()
