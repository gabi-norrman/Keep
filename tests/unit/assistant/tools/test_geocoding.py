import pytest
from unittest.mock import patch, Mock

from assistant.tools.Geocoding import reverse_geocode


NOMINATIM_FULL_RESPONSE = {
    "display_name": "1, Parkgatan, Centrum, Göteborg, Västra Götalands län, 411 00, Sverige",
    "address": {
        "house_number": "1",
        "road": "Parkgatan",
        "neighbourhood": "Centrum",
        "city": "Stockholm",
    },
}


@pytest.fixture
def mock_get():
    with patch("assistant.tools.Geocoding.requests.get") as m:
        yield m


def _ok_response(data):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = data
    return resp


@pytest.mark.unit
class TestReverseGeocode:
    def test_road_neighbourhood_city(self, mock_get):
        mock_get.return_value = _ok_response(NOMINATIM_FULL_RESPONSE)

        result = reverse_geocode(57.7, 11.96)

        assert result == "Parkgatan, Centrum, Stockholm"

    def test_home_city_omitted(self, mock_get):
        data = {
            "address": {
                "road": "Avenyn",
                "suburb": "Lorensberg",
                "city": "Göteborg",
            }
        }
        mock_get.return_value = _ok_response(data)

        result = reverse_geocode(57.7, 11.97)

        assert "Avenyn" in result
        assert "Lorensberg" in result
        assert "Göteborg" not in result

    def test_home_city_case_insensitive(self, mock_get):
        data = {"address": {"road": "Gata", "city": "GÖTEBORG"}}
        mock_get.return_value = _ok_response(data)

        result = reverse_geocode(57.7, 11.97)

        assert result == "Gata"

    def test_town_instead_of_city(self, mock_get):
        data = {"address": {"road": "Storgatan", "town": "Mellanstad"}}
        mock_get.return_value = _ok_response(data)

        result = reverse_geocode(57.48, 12.07)

        assert result == "Storgatan, Mellanstad"

    def test_village_instead_of_city(self, mock_get):
        data = {"address": {"road": "Byvägen", "village": "Lillby"}}
        mock_get.return_value = _ok_response(data)

        result = reverse_geocode(57.42, 12.01)

        assert result == "Byvägen, Lillby"

    def test_suburb_preferred_over_neighbourhood(self, mock_get):
        data = {"address": {"road": "Gata", "suburb": "SuburbName", "neighbourhood": "NbhName"}}
        mock_get.return_value = _ok_response(data)

        result = reverse_geocode(57.7, 11.96)

        assert "SuburbName" in result
        assert "NbhName" not in result

    def test_no_address_fields_falls_back_to_display_name(self, mock_get):
        data = {"display_name": "Some Full Address, Sweden", "address": {}}
        mock_get.return_value = _ok_response(data)

        result = reverse_geocode(57.7, 11.96)

        assert result == "Some Full Address, Sweden"

    def test_no_display_name_falls_back_to_unknown(self, mock_get):
        data = {"address": {}}
        mock_get.return_value = _ok_response(data)

        result = reverse_geocode(57.7, 11.96)

        assert result == "Unknown location"

    def test_http_error_returns_none(self, mock_get):
        resp = Mock()
        resp.status_code = 500
        mock_get.return_value = resp

        result = reverse_geocode(57.7, 11.96)

        assert result is None

    def test_network_exception_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("DNS failure")

        result = reverse_geocode(57.7, 11.96)

        assert result is None

    def test_passes_correct_params(self, mock_get):
        mock_get.return_value = _ok_response({"address": {"road": "X"}})

        reverse_geocode(57.1234, 11.5678, timeout=10)

        args, kwargs = mock_get.call_args
        assert kwargs["params"]["lat"] == 57.1234
        assert kwargs["params"]["lon"] == 11.5678
        assert kwargs["params"]["format"] == "json"
        assert kwargs["params"]["addressdetails"] == 1
        assert kwargs["timeout"] == 10
