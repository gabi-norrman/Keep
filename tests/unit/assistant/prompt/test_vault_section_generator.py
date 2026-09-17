"""VaultSectionGenerator: the `{VAULT}` prompt section lists placeholders, never values (VAULT_PLAN §3.7)."""
from unittest.mock import Mock, patch

import pytest

from assistant.prompt.sections.VaultSectionGenerator import VaultSectionGenerator
from common.vault.Vault import Vault

PHONE = "+46 70-174 06 05"
EMAIL = "Anna.Example@mlab.test"
EMPTY = "The vault is empty."


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault.bin", passphrase="test")


@pytest.fixture
def stocked(vault):
    vault.add("phone", "Marcus's mobile", PHONE, id="marcus-mobile")
    vault.add("email", "Anna's e-mail", EMAIL, id="anna-mail")
    return vault


def section(vault, prompt_manager=None) -> VaultSectionGenerator:
    return VaultSectionGenerator(prompt_manager, vault=vault)


@pytest.mark.unit
class TestRender:
    def test_section_name_is_vault(self, vault):
        assert section(vault).section_name == "Vault"

    def test_lists_placeholder_description_and_kind_sorted_by_id(self, stocked):
        assert section(stocked)._fetch_content() == (
            "The placeholders that exist, each with its description and kind:\n"
            "- {{secret:anna-mail}} — Anna's e-mail (email)\n"
            "- {{secret:marcus-mobile}} — Marcus's mobile (phone)"
        )

    def test_never_contains_a_value_canonical_or_masked_form(self, stocked):
        text = section(stocked)._fetch_content()
        for secret in stocked.secrets():
            assert secret.value not in text
            assert secret.canonical not in text
            assert secret.masked not in text
        assert "0605" not in text and "174" not in text
        assert "mlab" not in text and "@" not in text

    def test_empty_vault(self, vault):
        assert section(vault)._fetch_content() == EMPTY

    def test_disabled_vault_renders_as_empty(self, tmp_path):
        disabled = Vault(tmp_path / "vault.bin", passphrase=None)
        assert not disabled.enabled
        assert section(disabled)._fetch_content() == EMPTY

    def test_locked_vault_says_so_instead_of_empty(self, stocked):
        # A mistyped key must not fail open in silence: the LLM is told, so the user hears it.
        locked = Vault(stocked.path, passphrase="wrong")
        text = section(locked)._fetch_content()
        assert text.startswith("The vault is LOCKED: wrong vault.key or corrupt vault.bin.")
        assert "no placeholders" in text and "vault.key in config.yaml" in text
        assert PHONE not in text and "marcus-mobile" not in text and "anna" not in text

    def test_default_vault_is_the_singleton_resolved_lazily(self, vault):
        with patch.object(Vault, "get_instance", return_value=vault) as get_instance:
            gen = VaultSectionGenerator(None)
            get_instance.assert_not_called()
            assert gen.vault is vault
            get_instance.assert_called_once()


@pytest.mark.unit
class TestMonitoring:
    def test_first_check_renders_and_records_the_version(self, vault):
        gen = section(vault)
        gen._refresh_if_changed()
        assert gen.get_content() == EMPTY
        assert gen.last_version == vault.version

    def test_unchanged_version_is_a_no_op(self, vault):
        gen = section(vault)
        gen._refresh_if_changed()
        with patch.object(gen, "_update_content") as update:
            gen._refresh_if_changed()
        update.assert_not_called()

    def test_version_bump_re_renders_with_the_new_secret(self, vault):
        gen = section(vault)
        gen._refresh_if_changed()
        vault.add("phone", "Marcus's mobile", PHONE, id="marcus-mobile")
        gen._refresh_if_changed()
        assert "{{secret:marcus-mobile}}" in gen.get_content()
        assert PHONE not in gen.get_content()
        assert gen.last_version == vault.version

    def test_delete_re_renders_as_empty(self, stocked):
        gen = section(stocked)
        gen._refresh_if_changed()
        for secret in stocked.secrets():
            stocked.delete(secret.id)
        gen._refresh_if_changed()
        assert gen.get_content() == EMPTY

    def test_each_change_notifies_the_prompt_manager_by_section_name(self, vault):
        prompt_manager = Mock()
        gen = section(vault, prompt_manager)
        gen._refresh_if_changed()
        prompt_manager.on_section_updated.assert_called_once_with("Vault")
        vault.add("phone", "Marcus's mobile", PHONE)
        gen._refresh_if_changed()
        assert prompt_manager.on_section_updated.call_count == 2

    @patch("assistant.prompt.sections.VaultSectionGenerator.VaultSectionGenerator._wait", side_effect=StopIteration)
    def test_monitoring_loop_checks_then_sleeps_one_second(self, mock_sleep, vault):
        gen = section(vault)
        with patch.object(gen, "_update_content") as update, pytest.raises(StopIteration):
            gen._start_monitoring()
        update.assert_called_once()
        mock_sleep.assert_called_once_with(1)

    @patch("assistant.prompt.sections.VaultSectionGenerator.VaultSectionGenerator._wait", side_effect=StopIteration)
    def test_monitoring_loop_survives_a_failing_check(self, mock_sleep, vault):
        gen = section(vault)
        with patch.object(gen, "_refresh_if_changed", side_effect=RuntimeError("boom")), pytest.raises(StopIteration):
            gen._start_monitoring()
        mock_sleep.assert_called_once()

    def test_stop_event_ends_the_loop_without_sleeping(self, vault):
        gen = section(vault)
        gen._stop_event.set()
        with patch("assistant.prompt.sections.VaultSectionGenerator.VaultSectionGenerator._wait") as mock_sleep:
            gen._start_monitoring()
        mock_sleep.assert_not_called()
