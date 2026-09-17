import pytest

from common import ConfigSchema

EXAMPLE = """\
api_keys:
  openai: YOUR_OPENAI_API_KEY_HERE
  spotify:
    client_id: YOUR_SPOTIFY_CLIENT_ID_HERE

email:
  address: your_gmail_address@gmail.com
  password: YOUR_GMAIL_APP_PASSWORD  # Generate at https://myaccount.google.com/apppasswords

# Photos library indexing (stills are always indexed).
#photos:
#  videos:
#    enabled: true              # index library videos too
#    max_size_mb: 150           # largest rendition

tesla:
  tessie_access_token: YOUR_TESSIE_ACCESS_TOKEN
  vehicles:
    - name: home_car
      vin: YOUR_TESLA_VIN

# Which interfaces the server starts.
# A disabled interface's module is never imported.
interfaces:
  console:  { enabled: true, port: 5151 }

#x:
#  search_reasoning_effort: low  # grok reasoning effort
#                                # the search runs server-side

location:
  home:
    latitude: 34.67
owner_name: YOUR_NAME
"""


@pytest.fixture(autouse=True)
def example(tmp_path, monkeypatch):
    path = tmp_path / "config.example.yaml"
    path.write_text(EXAMPLE)
    monkeypatch.setattr(ConfigSchema, "EXAMPLE_PATH", path)
    ConfigSchema.reset()
    yield path
    ConfigSchema.reset()


@pytest.mark.unit
class TestSections:
    def test_lists_sections_in_file_order_including_commented_out(self):
        assert ConfigSchema.sections() == ["api_keys", "email", "photos", "tesla", "interfaces", "x",
                                           "location", "owner_name"]

    def test_section_text_keeps_the_block_and_its_comments(self):
        text = ConfigSchema.section_text("email")
        assert text == ("email:\n  address: your_gmail_address@gmail.com\n"
                        "  password: YOUR_GMAIL_APP_PASSWORD  # Generate at https://myaccount.google.com/apppasswords")

    def test_leading_prose_comments_belong_to_the_section_below(self):
        text = ConfigSchema.section_text("interfaces")
        assert text.startswith("# Which interfaces the server starts.\n# A disabled interface's module is never imported.\ninterfaces:")
        assert "interfaces" not in ConfigSchema.section_text("tesla")
        assert "Which interfaces" not in ConfigSchema.section_text("tesla")

    def test_commented_out_section_comes_back_uncommented(self):
        text = ConfigSchema.section_text("photos")
        assert text == ("# Photos library indexing (stills are always indexed).\n"
                        "photos:\n  videos:\n    enabled: true              # index library videos too\n"
                        "    max_size_mb: 150           # largest rendition")

    def test_lookup_is_case_insensitive(self):
        assert ConfigSchema.section_text("EMAIL") == ConfigSchema.section_text("email")

    def test_unknown_section_is_none(self):
        assert ConfigSchema.section_text("gmail") is None

    def test_scalar_top_level_key_is_a_section(self):
        assert ConfigSchema.section_text("owner_name") == "owner_name: YOUR_NAME"


@pytest.mark.unit
class TestTypes:
    def test_leaf_types_follow_the_example(self):
        assert ConfigSchema.leaf_type("email.password") is str
        assert ConfigSchema.leaf_type("interfaces.console.port") is int
        assert ConfigSchema.leaf_type("interfaces.console.enabled") is bool
        assert ConfigSchema.leaf_type("location.home.latitude") is float
        assert ConfigSchema.leaf_type("tesla.vehicles") is list
        assert ConfigSchema.leaf_type("api_keys.spotify") is dict

    def test_commented_out_sections_have_types_too(self):
        assert ConfigSchema.leaf_type("photos.videos.enabled") is bool
        assert ConfigSchema.leaf_type("photos.videos.max_size_mb") is int
        assert ConfigSchema.leaf_type("x.search_reasoning_effort") is str

    def test_unknown_key_has_no_type(self):
        assert ConfigSchema.leaf_type("email.imap_host") is None
        assert ConfigSchema.leaf_type("nope.x") is None

    def test_example_value(self):
        assert ConfigSchema.example_value("interfaces.console.port") == 5151
        assert ConfigSchema.example_value("owner_name") == "YOUR_NAME"


@pytest.mark.unit
class TestLeaves:
    def test_section_expands_to_its_scalar_and_list_leaves(self):
        assert ConfigSchema.leaves("tesla") == ["tesla.tessie_access_token", "tesla.vehicles"]
        assert ConfigSchema.leaves("api_keys") == ["api_keys.openai", "api_keys.spotify.client_id"]

    def test_leaf_expands_to_itself_and_unknown_to_nothing(self):
        assert ConfigSchema.leaves("email.password") == ["email.password"]
        assert ConfigSchema.leaves("email.nope") == []


@pytest.mark.unit
class TestKnownKeys:
    def test_keys_under_a_known_section_are_known_even_when_the_example_lacks_them(self):
        assert ConfigSchema.is_known_key("email.password")
        assert ConfigSchema.is_known_key("email.imap_host")
        assert ConfigSchema.is_known_key("photos.videos.enabled")
        assert ConfigSchema.is_known_key("owner_name")

    def test_unknown_section_or_malformed_key_is_not(self):
        assert not ConfigSchema.is_known_key("gmail.password")
        assert not ConfigSchema.is_known_key("email/password")
        assert not ConfigSchema.is_known_key("Email.Password")
        assert not ConfigSchema.is_known_key("")


@pytest.mark.unit
def test_real_example_parses_and_has_the_known_sections():
    ConfigSchema.reset()
    with pytest.MonkeyPatch.context() as mp:
        from common.Paths import AISERVER_DIR
        mp.setattr(ConfigSchema, "EXAMPLE_PATH", AISERVER_DIR / "config.example.yaml")
        ConfigSchema.reset()
        names = ConfigSchema.sections()
        assert {"email", "icloud_email", "tesla", "ai", "vault"} <= set(names)
        assert "photos" not in names   # its videos block is defaults only, nothing the LLM should be offered to tune
        assert ConfigSchema.leaf_type("ai.fast.llm") is str
        assert ConfigSchema.section_text("email").startswith("email:")
        # Optional keys ship uncommented: a commented-out leaf has no type, and config_set on
        # it would store text where a list belongs (exclude_mics, 2026-09-15).
        assert ConfigSchema.is_scalar_list("local_outposts.exclude_mics") is True
        assert ConfigSchema.leaf_type("local_outposts.auto_recovery") is bool
    ConfigSchema.reset()


@pytest.mark.unit
class TestIndexedKeys:
    def test_split_and_join(self):
        assert ConfigSchema.split_key("icloud.devices[0].id") == ["icloud", "devices", 0, "id"]
        assert ConfigSchema.split_key("owner_name") == ["owner_name"]
        assert ConfigSchema.join_key(["icloud", "devices", 0, "id"]) == "icloud.devices[0].id"
        assert ConfigSchema.join_key(["tesla", "vehicles"]) == "tesla.vehicles"

    @pytest.mark.parametrize("bad", ["icloud.devices[]", "icloud.devices[a]", "icloud.[0]", "icloud..devices"])
    def test_malformed_keys(self, bad):
        assert ConfigSchema.is_known_key(bad) is False
        assert ConfigSchema.example_value(bad) is None
        with pytest.raises(ValueError):
            ConfigSchema.split_key(bad)

    def test_any_index_reads_the_examples_first_item(self):
        assert ConfigSchema.example_value("tesla.vehicles[0].vin") == "YOUR_TESLA_VIN"
        assert ConfigSchema.example_value("tesla.vehicles[7].vin") == "YOUR_TESLA_VIN"
        assert ConfigSchema.leaf_type("tesla.vehicles[3]") is dict
        assert ConfigSchema.leaf_type("tesla.vehicles") is list
        assert ConfigSchema.example_value("tesla.vehicles[0].nope") is None
        assert ConfigSchema.example_value("owner_name[0]") is None

    def test_parens_are_an_accepted_spelling_of_the_index(self):
        """The command parser rewrites [0] to (0) for most commands; the key must survive it."""
        assert ConfigSchema.split_key("icloud.devices(0).id") == ["icloud", "devices", 0, "id"]
        assert ConfigSchema.normalize_key("icloud.devices(0).id") == "icloud.devices[0].id"
        assert ConfigSchema.normalize_key(" tesla.vehicles[1].vin ") == "tesla.vehicles[1].vin"
        assert ConfigSchema.normalize_key("icloud.devices[]") == "icloud.devices[]"
        assert ConfigSchema.example_value("tesla.vehicles(0).vin") == "YOUR_TESLA_VIN"
        assert ConfigSchema.is_known_key("tesla.vehicles(0).vin") is True

    def test_indexed_keys_are_known_by_their_section(self):
        assert ConfigSchema.is_known_key("tesla.vehicles[1].name") is True
        assert ConfigSchema.is_known_key("tesla[0].vehicles") is True
        assert ConfigSchema.is_known_key("nope.list[0].x") is False
