"""Vault: the encrypted file, its states and CRUD (VAULT_PLAN §3.3)."""
import base64
import threading
import time
from unittest.mock import Mock

import pytest
from cryptography.fernet import InvalidToken

from common.ConfigManager import config
from common.vault import Vault as vault_module
from common.vault.Redactor import Redactor
from common.vault.Vault import HEADER, DuplicateSecret, Secret, Vault, VaultError

KEY = "correct horse battery staple"


@pytest.fixture
def path(tmp_path):
    return tmp_path / "vault.bin"


@pytest.fixture
def vault(path):
    return Vault(path, KEY)


@pytest.mark.unit
class TestFile:
    def test_missing_file_is_an_empty_enabled_vault_created_on_first_add(self, vault, path):
        assert vault.enabled and vault.error is None
        assert vault.secrets() == [] and len(vault) == 0
        assert not path.exists()
        vault.add("phone", "Marcus's mobile", "+46701740605")
        assert path.exists()

    def test_layout_is_header_salt_token(self, vault, path):
        vault.add("phone", "Marcus's mobile", "+46701740605")
        header, salt, token = path.read_bytes().split(b"\n")
        assert header == HEADER == b"FLOXYVAULT1"
        assert len(base64.b64decode(salt, validate=True)) == 16
        assert token.startswith(b"gAAAA")
        assert b"46701740605" not in path.read_bytes()

    def test_mode_0600_and_no_tmp_left_behind(self, vault, path):
        vault.add("phone", "Marcus's mobile", "+46701740605")
        assert path.stat().st_mode & 0o777 == 0o600
        assert not path.with_name("vault.bin.tmp").exists()

    def test_round_trip(self, vault, path):
        a = vault.add("phone", "Marcus's mobile", "+46 70-174 06 05")
        b = vault.add("email", "Work e-mail", "m@mlab.test", id="work-mail")
        again = Vault(path, KEY)
        assert again.enabled
        assert again.secrets() == [a, b]
        assert again.get("work-mail").value == "m@mlab.test"
        assert again.get("marcus-s-mobile").created == a.created

    def test_salt_is_kept_across_writes(self, vault, path):
        vault.add("phone", "one", "+46701740605")
        salt1 = path.read_bytes().split(b"\n")[1]
        vault.add("phone", "two", "+46701740606")
        assert path.read_bytes().split(b"\n")[1] == salt1
        assert len(Vault(path, KEY)) == 2

    def test_country_code_is_used_for_canonicals(self, path):
        vault = Vault(path, KEY, country_code=47)
        secret = vault.add("phone", "Oslo", "012345678")
        assert secret.canonical == "4712345678"
        assert Vault(path, KEY, country_code=47).get("oslo").canonical == "4712345678"

    def test_key_derivation_honours_scrypt_n(self, monkeypatch):
        # The unit conftest lowers SCRYPT_N so a test vault costs microseconds. The derivation
        # must actually read it, or the suite silently goes back to ~90 ms per vault.
        assert vault_module.SCRYPT_N < 2 ** 14, "tests/unit/conftest.py lowers this"
        salt = b"0" * 16
        token = Vault._fernet_for(KEY, salt).encrypt(b"x")
        monkeypatch.setattr(vault_module, "SCRYPT_N", vault_module.SCRYPT_N * 2)
        with pytest.raises(InvalidToken):
            Vault._fernet_for(KEY, salt).decrypt(token)

    @pytest.mark.parametrize("value", [
        "line one\nline two\nline three",
        'quote " and \\ backslash',
        "lösenordet-är-hemligt-🔐",
        "sk-live-AAAA\tBBBB",
    ])
    def test_awkward_values_survive_the_round_trip_and_still_redact(self, vault, path, value):
        # One JSON object per line on disk: a newline, a quote or non-ASCII in a text secret
        # must come back byte-for-byte, or the redactor stops matching it and the value
        # flows to the provider in the clear.
        vault.add("text", "odd", value, id="odd")
        again = Vault(path, KEY)
        assert len(again) == 1
        assert again.get("odd").value == value
        assert Redactor(again).redact(f"here: {value} end") == ("here: {{secret:odd}} end", 1)
        assert value.encode() not in path.read_bytes()


@pytest.mark.unit
class TestLockedAndDisabled:
    def test_wrong_passphrase_locks_and_refuses_writes(self, vault, path):
        vault.add("phone", "Marcus's mobile", "+46701740605")
        before = path.read_bytes()
        locked = Vault(path, "wrong")
        assert not locked.enabled
        assert "wrong vault.key or corrupt" in locked.error
        assert locked.secrets() == []
        for call in (lambda: locked.add("email", "x", "m@mlab.test"),
                     lambda: locked.update("marcus-s-mobile", description="x"),
                     lambda: locked.delete("marcus-s-mobile")):
            with pytest.raises(VaultError, match="wrong vault.key"):
                call()
        assert path.read_bytes() == before
        assert locked.version == 0
        assert locked.locked

    def test_corrupt_file_is_locked_a_healthy_vault_is_not(self, vault, path):
        assert not vault.locked
        vault.add("phone", "Marcus's mobile", "+46701740605")
        assert not vault.locked and not Vault(path, KEY).locked
        path.write_bytes(b"garbage")
        assert Vault(path, KEY).locked

    @pytest.mark.parametrize("content", [b"garbage", b"FLOXYVAULT1\nnot-base64!\ntoken", b"FLOXYVAULT2\nAAAA\nx",
                                         HEADER + b"\n" + base64.b64encode(b"0" * 16) + b"\nnot a token"])
    def test_corrupt_file_locks(self, path, content):
        path.write_bytes(content)
        locked = Vault(path, KEY)
        assert not locked.enabled and locked.error
        with pytest.raises(VaultError):
            locked.add("email", "x", "m@mlab.test")
        assert path.read_bytes() == content

    @pytest.mark.parametrize("passphrase", [None, ""])
    def test_missing_passphrase_disables(self, path, passphrase):
        disabled = Vault(path, passphrase)
        assert not disabled.enabled
        assert disabled.error == "vault.key not set in config.yaml"
        assert not disabled.locked, "no key is a choice, not a lock-out"
        with pytest.raises(VaultError, match="vault.key not set"):
            disabled.add("email", "x", "m@mlab.test")
        assert not path.exists()


@pytest.mark.unit
class TestAdd:
    def test_rejects_too_short_and_leaves_nothing_behind(self, vault, path):
        with pytest.raises(VaultError, match="too short"):
            vault.add("password", "door", "1234")
        assert len(vault) == 0 and vault.version == 0 and not path.exists()

    def test_same_canonical_is_a_duplicate_whatever_the_format(self, vault):
        vault.add("phone", "Marcus's mobile", "+46 70-174 06 05")
        with pytest.raises(DuplicateSecret) as e:
            vault.add("phone", "Mobile again", "070 174 06 05")
        assert e.value.existing_id == "marcus-s-mobile"
        assert "marcus-s-mobile" in str(e.value)
        assert len(vault) == 1

    def test_duplicate_is_detected_across_kinds(self, vault):
        vault.add("password", "pw", "SE4550000000058398257466")
        with pytest.raises(DuplicateSecret):
            vault.add("account", "acct", "SE45 5000 0000 0583 9825 7466")

    def test_value_is_stripped_but_otherwise_kept_verbatim(self, vault):
        secret = vault.add("password", "Wifi", "  Sw0rdf!sh  ")
        assert secret.value == "Sw0rdf!sh"

    def test_unknown_kind_is_refused(self, vault):
        with pytest.raises(VaultError, match="unknown secret kind"):
            vault.add("ssn", "x", "123456789")

    def test_returns_the_stored_secret(self, vault):
        secret = vault.add("email", "Work e-mail", "M@MLab.test")
        assert isinstance(secret, Secret)
        assert secret.kind == "email" and secret.description == "Work e-mail" and secret.value == "M@MLab.test"
        assert secret.canonical == "m@mlab.test"
        assert secret.created[:2] == "20" and "T" in secret.created
        assert vault.get(secret.id) == secret


@pytest.mark.unit
class TestIds:
    def test_slug_from_description(self, vault):
        assert vault.add("phone", "Marcus's mobile", "+46701740605").id == "marcus-s-mobile"
        assert vault.add("plate", "  The Model Y!  ", "KEP 47L").id == "the-model-y"
        assert vault.add("email", "Årets e-post", "m@mlab.test").id == "arets-e-post"

    def test_deduplicated_with_2_then_3(self, vault):
        assert vault.add("phone", "Mobile", "+46701740605").id == "mobile"
        assert vault.add("phone", "Mobile", "+46701740606").id == "mobile-2"
        assert vault.add("phone", "Mobile", "+46701740607").id == "mobile-3"

    def test_at_most_40_chars_including_the_suffix(self, vault):
        long = "a very long description that goes on and on and on forever"
        first = vault.add("phone", long, "+46701740605").id
        second = vault.add("phone", long, "+46701740606").id
        assert first == "a-very-long-description-that-goes-on-and"
        assert len(first) <= 40 and len(second) <= 40
        assert second.endswith("-2") and "--" not in second

    def test_empty_description_falls_back_to_the_kind(self, vault):
        assert vault.add("personal_id", "", "19850203-1234").id == "personal-id"
        assert vault.add("personal_id", "  ", "19850204-1234").id == "personal-id-2"

    def test_given_id_is_slugified_and_must_be_free(self, vault):
        assert vault.add("phone", "x", "+46701740605", id="Marcus Mobile").id == "marcus-mobile"
        with pytest.raises(VaultError, match="already exists"):
            vault.add("phone", "y", "+46701740606", id="marcus-mobile")
        with pytest.raises(VaultError, match="invalid id"):
            vault.add("phone", "y", "+46701740606", id="!!!")
        assert len(vault) == 1


@pytest.mark.unit
class TestUpdateDeleteVersion:
    def test_version_counts_every_mutation_and_nothing_else(self, vault):
        assert vault.version == 0
        secret = vault.add("phone", "Mobile", "+46701740605")
        assert vault.version == 1
        vault.update(secret.id, description="Marcus's mobile")
        assert vault.version == 2
        with pytest.raises(VaultError):
            vault.add("password", "short", "abc")
        assert vault.version == 2
        vault.delete(secret.id)
        assert vault.version == 3
        vault.secrets(), vault.get("x"), len(vault)
        assert vault.version == 3

    def test_update_description_and_value_persist(self, vault, path):
        vault.add("phone", "Mobile", "+46701740605", id="mob")
        updated = vault.update("mob", description="Marcus's mobile", value="0701740606")
        assert updated.description == "Marcus's mobile" and updated.value == "0701740606"
        assert updated.kind == "phone" and updated.id == "mob"
        assert Vault(path, KEY).get("mob") == updated

    def test_update_to_anothers_value_is_a_duplicate(self, vault):
        vault.add("phone", "a", "+46701740605", id="a")
        vault.add("phone", "b", "+46701740606", id="b")
        with pytest.raises(DuplicateSecret) as e:
            vault.update("b", value="070 174 06 05")
        assert e.value.existing_id == "a"
        assert vault.get("b").value == "+46701740606"

    def test_update_keeps_own_value_without_a_duplicate_error(self, vault):
        vault.add("phone", "a", "+46701740605", id="a")
        assert vault.update("a", description="renamed").value == "+46701740605"

    def test_update_with_a_new_id_rekeys_the_secret_in_one_commit(self, vault, path):
        vault.add("phone", "Mobile", "+46701740605", id="mob")
        before = vault.version
        updated = vault.update("mob", description="Marcus's mobile", id="Marcus Mobile")
        assert updated.id == "marcus-mobile" and updated.description == "Marcus's mobile"
        assert vault.version == before + 1
        assert vault.get("mob") is None and vault.get("marcus-mobile") == updated
        assert Vault(path, KEY).get("marcus-mobile") == updated
        assert "mob" not in Vault(path, KEY)

    def test_update_to_a_taken_id_is_refused_and_own_id_is_free(self, vault):
        vault.add("phone", "a", "+46701740605", id="a")
        vault.add("phone", "b", "+46701740606", id="b")
        with pytest.raises(VaultError, match="id 'a' already exists"):
            vault.update("b", id="a")
        assert vault.get("b").value == "+46701740606" and vault.get("a").description == "a"
        assert vault.update("b", id="b", description="still b").id == "b"
        with pytest.raises(VaultError, match="invalid id"):
            vault.update("b", id="!!!")

    def test_suggest_id_is_what_add_would_derive(self, vault):
        vault.add("phone", "Marcus's mobile", "+46701740605")     # marcus-s-mobile
        assert vault.suggest_id("Marcus mobile", "phone") == "marcus-mobile"
        assert vault.suggest_id("Marcus's mobile", "phone") == "marcus-s-mobile-2"
        assert vault.suggest_id("Marcus's mobile", "phone", exclude="marcus-s-mobile") == "marcus-s-mobile"
        assert vault.suggest_id("  ", "personal_id") == "personal-id"
        assert vault.version == 1

    def test_update_and_delete_of_unknown_id(self, vault):
        with pytest.raises(VaultError, match="no secret 'nope'"):
            vault.update("nope", description="x")
        with pytest.raises(VaultError, match="no secret 'nope'"):
            vault.delete("nope")

    def test_delete_persists(self, vault, path):
        vault.add("phone", "a", "+46701740605", id="a")
        vault.add("phone", "b", "+46701740606", id="b")
        vault.delete("a")
        assert [s.id for s in Vault(path, KEY).secrets()] == ["b"]
        assert "a" not in vault and "b" in vault

    def test_failed_save_rolls_back_memory_and_version(self, vault, path, monkeypatch):
        vault.add("phone", "a", "+46701740605", id="a")
        on_disk = path.read_bytes()
        monkeypatch.setattr(vault_module.os, "replace", Mock(side_effect=OSError("disk full")))
        with pytest.raises(OSError):
            vault.add("phone", "b", "+46701740606", id="b")
        with pytest.raises(OSError):
            vault.update("a", description="renamed")
        with pytest.raises(OSError):
            vault.delete("a")
        assert [s.id for s in vault.secrets()] == ["a"] and vault.get("a").description == "a"
        assert vault.version == 1 and vault.enabled
        assert path.read_bytes() == on_disk
        monkeypatch.undo()
        vault.add("phone", "b", "+46701740606", id="b")
        assert len(Vault(path, KEY)) == 2

    def test_concurrent_adds_all_land(self, vault, path):
        def add(n):
            vault.add("phone", f"phone {n}", f"+4670174{n:04d}", id=f"p{n}")
        threads = [threading.Thread(target=add, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(vault) == 8 and vault.version == 8
        assert len(Vault(path, KEY)) == 8


@pytest.mark.unit
class TestMasked:
    @pytest.mark.parametrize("kind,value,masked", [
        ("phone", "+46 70-174 06 05", "•••• 0605"),
        ("card", "4111 1111 1111 1111", "•••• 1111"),
        ("account", "SE45 5000 0000 0583 9825 7466", "•••• 7466"),
        ("email", "M@MLab.test", "m•••@mlab.test"),
        ("plate", "kep 47l", "KEP •••"),
        ("personal_id", "19850203-1234", "••••••••"),
        ("password", "Sw0rdf!sh", "••••••••"),
        ("api_key", "sk-live-abcdef", "••••••••"),
        ("text", "some secret text", "••••••••"),
    ])
    def test_masked_never_shows_more_than_the_rule_allows(self, vault, kind, value, masked):
        secret = vault.add(kind, "d", value)
        assert secret.masked == masked


@pytest.mark.unit
class TestSingleton:
    @pytest.fixture(autouse=True)
    def isolated(self, path, monkeypatch):
        Vault.reset_instance()
        monkeypatch.setattr(vault_module, "VAULT_PATH", path)
        yield
        Vault.reset_instance()

    def test_get_instance_reads_config_once_and_uses_vault_path(self, path, monkeypatch):
        calls = []
        monkeypatch.setattr(vault_module, "_config_settings", lambda: calls.append(1) or (KEY, 47))
        first = Vault.get_instance()
        assert Vault.get_instance() is first
        assert calls == [1]
        assert first.path == path and first.country_code == 47 and first.enabled

    def test_missing_key_in_config_disables(self, monkeypatch):
        monkeypatch.setattr(vault_module, "_config_settings", lambda: (None, 46))
        assert not Vault.get_instance().enabled
        assert Vault.get_instance().error == "vault.key not set in config.yaml"

    def test_reset_instance_forgets(self, monkeypatch):
        monkeypatch.setattr(vault_module, "_config_settings", lambda: (KEY, 46))
        first = Vault.get_instance()
        Vault.reset_instance()
        assert Vault.get_instance() is not first

    def test_concurrent_first_calls_build_exactly_one_instance(self, monkeypatch):
        # Boot has the prompt section's thread and create_llm() on the main thread arrive
        # together; the constructor runs scrypt, so the window is real. Two instances would
        # leave one LLM wrapper blind to every secret added later.
        constructions = []
        real_init = Vault.__init__

        def slow_init(self, *args, **kwargs):
            constructions.append(1)
            time.sleep(0.05)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(vault_module, "_config_settings", lambda: (KEY, 46))
        monkeypatch.setattr(Vault, "__init__", slow_init)
        barrier = threading.Barrier(4)
        seen = []

        def call():
            barrier.wait()
            seen.append(Vault.get_instance())

        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(constructions) == 1
        assert len(seen) == 4 and all(v is seen[0] for v in seen)


@pytest.mark.unit
class TestConfigSettings:
    """The real config reader behind get_instance(): the TestSingleton tests stub it out."""

    @pytest.fixture(autouse=True)
    def isolated(self, path, monkeypatch):
        Vault.reset_instance()
        monkeypatch.setattr(vault_module, "VAULT_PATH", path)
        yield
        Vault.reset_instance()

    def _config(self, monkeypatch, data: dict):
        monkeypatch.setattr(config, "_config", data)

    def test_no_vault_block_disables_instead_of_raising(self, monkeypatch):
        # ConfigManager.get raises KeyError for a missing key with a None default, and every
        # install without a vault: block goes through here at boot (create_llm, prompt thread).
        self._config(monkeypatch, {"api_keys": {}})
        assert vault_module._config_settings() == (None, vault_module.DEFAULT_COUNTRY_CODE)
        instance = Vault.get_instance()
        assert not instance.enabled and not instance.locked

    def test_key_and_country_code_are_read(self, monkeypatch):
        self._config(monkeypatch, {"vault": {"key": "s3cret", "default_country_code": 47}})
        assert vault_module._config_settings() == ("s3cret", 47)
        instance = Vault.get_instance()
        assert instance.enabled and instance.country_code == 47

    def test_empty_key_is_treated_as_absent(self, monkeypatch):
        self._config(monkeypatch, {"vault": {"key": ""}})
        assert vault_module._config_settings()[0] is None
        assert not Vault.get_instance().enabled
