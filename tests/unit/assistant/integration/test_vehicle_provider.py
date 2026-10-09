"""The VehicleProvider contract: a spec's matching, the Fleet merging the brands' cars and
resolving a vehicle_id, the default actions a brand did not implement, `active()` gathering
the ready providers through the monitor and the hint for when none is."""
from unittest.mock import Mock

import pytest

from assistant.integration.Integration import Integration
from assistant.integration.IntegrationMonitor import IntegrationMonitor, DisabledIntegration, UnavailableIntegration
from assistant.integration.VehicleProvider import (
    Fleet, STARTING_HINT, Unsupported, VehicleError, VehicleProvider, VehicleSpec, VehicleState,
    active, fleet, no_provider_hint,
)


class FakeCars(Integration, VehicleProvider):
    def __init__(self, specs=(), missing=()):
        super().__init__()
        self._specs = list(specs)
        self._missing = list(missing)

    @property
    def name(self): return "Fake Cars"
    @property
    def config_keys(self): return ["fake.token"]
    def check_health(self): return True
    def missing_config_keys(self): return self._missing
    def vehicles(self): return self._specs
    def vehicle_state(self, spec): return VehicleState()


class OtherCars(FakeCars):
    @property
    def name(self): return "Other Cars"


class Weather(Integration):
    @property
    def name(self): return "Weather"
    @property
    def config_keys(self): return []
    def check_health(self): return True


MODEL_Y = VehicleSpec(id="home_car", label="Family Model Y", handle="VIN1", aliases=("Family Model Y", "VIN1"))
VOLVO = VehicleSpec(id="volvo", label="Charlotte's Volvo", handle="abc", is_default=True)


@pytest.fixture
def monitor():
    IntegrationMonitor.reset_instance()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(IntegrationMonitor, "_config_manager", Mock(), raising=False)
        yield IntegrationMonitor.get_instance()
    IntegrationMonitor.reset_instance()


def _register(monitor, integration):
    with monitor._lock:
        monitor._integrations[integration.name] = integration


@pytest.mark.unit
class TestVehicleSpecMatches:
    def test_the_id_the_label_and_an_alias_all_name_the_car(self):
        assert MODEL_Y.matches("home_car") and MODEL_Y.matches("Family Model Y") and MODEL_Y.matches("VIN1")

    def test_case_and_surrounding_space_do_not_matter(self):
        assert MODEL_Y.matches("  vin1 ") and MODEL_Y.matches("FAMILY MODEL Y")

    def test_an_empty_or_unknown_target_matches_nothing(self):
        assert not MODEL_Y.matches("") and not MODEL_Y.matches(None) and not MODEL_Y.matches("Model 3")


@pytest.mark.unit
class TestFleet:
    def test_the_cars_of_every_provider_in_order(self):
        tesla, volvo = FakeCars([MODEL_Y]), OtherCars([VOLVO])
        assert Fleet([tesla, volvo]).cars() == [(tesla, MODEL_Y), (volvo, VOLVO)]

    def test_a_provider_that_cannot_list_its_cars_costs_only_itself(self):
        broken = FakeCars()
        broken.vehicles = Mock(side_effect=RuntimeError("token expired"))
        volvo = OtherCars([VOLVO])
        assert Fleet([broken, volvo]).cars() == [(volvo, VOLVO)]

    def test_an_empty_id_is_the_default_car_wherever_it_is(self):
        tesla, volvo = FakeCars([MODEL_Y]), OtherCars([VOLVO])
        assert Fleet([tesla, volvo]).resolve("") == (volvo, VOLVO)
        assert Fleet([tesla, volvo]).resolve(None) == (volvo, VOLVO)

    def test_without_a_default_flag_the_first_car_is_the_default(self):
        tesla = FakeCars([MODEL_Y])
        assert Fleet([tesla]).resolve("  ") == (tesla, MODEL_Y)

    def test_an_id_finds_its_car_across_providers(self):
        tesla, volvo = FakeCars([MODEL_Y]), OtherCars([VOLVO])
        assert Fleet([tesla, volvo]).resolve("charlotte's volvo") == (volvo, VOLVO)

    def test_an_unknown_id_names_the_cars_there_are(self):
        with pytest.raises(VehicleError, match="home_car, volvo"):
            Fleet([FakeCars([MODEL_Y]), OtherCars([VOLVO])]).resolve("Model 3")

    def test_an_id_two_cars_answer_to_is_refused_naming_both(self):
        twin = VehicleSpec(id="home_car", label="The other home car")
        with pytest.raises(VehicleError, match="Fake Cars.*Other Cars"):
            Fleet([FakeCars([MODEL_Y]), OtherCars([twin])]).resolve("home_car")

    def test_no_cars_at_all_is_an_error_not_a_guess(self):
        with pytest.raises(VehicleError):
            Fleet([FakeCars()]).resolve("")

    def test_the_cars_are_listed_once_per_fleet(self):
        tesla = FakeCars([MODEL_Y])
        tesla.vehicles = Mock(return_value=[MODEL_Y])
        a_fleet = Fleet([tesla])
        a_fleet.resolve("home_car")
        a_fleet.resolve("VIN1")
        assert tesla.vehicles.call_count == 1


@pytest.mark.unit
class TestDefaultActions:
    @pytest.mark.parametrize("verb", ["max_defrost", "climate_on", "climate_off", "open_trunk"])
    def test_an_action_the_brand_lacks_is_unsupported_and_names_the_car(self, verb):
        with pytest.raises(Unsupported, match="Family Model Y") as raised:
            getattr(FakeCars(), verb)(MODEL_Y)
        assert isinstance(raised.value, VehicleError)

    def test_navigation_too(self):
        with pytest.raises(Unsupported, match="Fake Cars"):
            FakeCars().navigate_to(MODEL_Y, "Storgatan 1")


@pytest.mark.unit
class TestActive:
    def test_the_configured_providers_in_registration_order(self, monitor):
        first, second = FakeCars(), OtherCars()
        _register(monitor, first)
        _register(monitor, Weather())
        _register(monitor, second)
        assert active() == [first, second]
        assert fleet().providers == [first, second]

    def test_an_unconfigured_provider_is_left_out(self, monitor):
        ready = FakeCars()
        _register(monitor, OtherCars(missing=["fake.token"]))
        _register(monitor, ready)
        assert active() == [ready]

    def test_nothing_registered_is_an_empty_fleet(self, monitor):
        assert active() == [] and fleet().cars() == []


@pytest.mark.unit
class TestNoProviderHint:
    def test_nothing_registered_says_still_starting(self, monitor):
        _register(monitor, DisabledIntegration(Weather))
        assert no_provider_hint() == STARTING_HINT

    def test_an_unconfigured_provider_names_its_missing_keys(self, monitor):
        _register(monitor, FakeCars(missing=["fake.token"]))
        hint = no_provider_hint()
        assert "Fake Cars" in hint and "fake.token" in hint and "config_help" in hint

    def test_a_disabled_provider_gives_the_enable_line(self, monitor):
        _register(monitor, DisabledIntegration(FakeCars))
        assert 'enable_integration("Fake Cars")' in no_provider_hint()

    def test_a_broken_provider_carries_its_error(self, monitor):
        _register(monitor, UnavailableIntegration(OtherCars, KeyError("tesla")))
        hint = no_provider_hint()
        assert "OtherCars could not be started" in hint and "KeyError" in hint and "restart_integration" in hint

    def test_a_broken_non_car_integration_is_not_a_car_problem(self, monitor):
        _register(monitor, UnavailableIntegration(Weather, KeyError("weather")))
        assert no_provider_hint() == STARTING_HINT
