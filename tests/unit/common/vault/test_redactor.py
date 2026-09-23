"""Redactor: secrets → `{{secret:<id>}}` on the way in, back on the way out (VAULT_PLAN §3.4)."""
import pytest

from common.vault import Redactor as redactor_module
from common.vault.Redactor import Redactor, placeholder
from common.vault.Vault import Vault

KEY = "correct horse battery staple"
MOBILE = "+46701740605"


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault.bin", KEY)


@pytest.fixture
def stocked(vault):
    vault.add("phone", "Marcus's mobile", MOBILE, id="marcus-mobile")
    vault.add("card", "Marcus's Visa", "4111 1111 1111 1111", id="marcus-visa")
    vault.add("email", "Work e-mail", "m@mlab.test", id="work-mail")
    return vault


@pytest.fixture
def redactor(stocked):
    return Redactor(stocked)


@pytest.fixture
def logged(monkeypatch):
    lines = []
    for name in ("fine", "info"):
        monkeypatch.setattr(redactor_module, name, lambda m, _n=name: lines.append(f"{_n}: {m}"))
    return lines


@pytest.mark.unit
class TestIdentityWhenEmpty:
    def test_redact_returns_the_very_same_object(self, vault):
        redactor = Redactor(vault)
        text = f"call {MOBILE} now"
        out, n = redactor.redact(text)
        assert out is text and n == 0
        assert not redactor.active

    def test_redact_messages_returns_the_very_same_list(self, vault):
        messages = [{"role": "user", "content": f"call {MOBILE} now"}]
        assert Redactor(vault).redact_messages(messages) is messages

    def test_locked_vault_is_identity_too(self, stocked, tmp_path):
        locked = Vault(tmp_path / "vault.bin", "wrong")
        text = f"call {MOBILE} now"
        assert Redactor(locked).redact(text) == (text, 0)

    def test_empty_is_silent(self, vault, logged):
        Redactor(vault).redact("x")
        assert logged == []

    def test_locked_is_logged_at_info_with_the_reason(self, stocked, tmp_path, logged):
        # Fail-open must be loud: a locked vault passes every request through unredacted.
        locked = Vault(tmp_path / "vault.bin", "wrong")
        Redactor(locked).redact("x")
        Redactor(locked).redact_messages([{"role": "user", "content": "x"}])
        assert logged == [f"info: Vault: LOCKED ({locked.error}) — nothing redacted"] * 2


@pytest.mark.unit
class TestRedact:
    def test_every_written_form_becomes_the_same_placeholder(self, redactor):
        out, n = redactor.redact("ring 070-174 06 05 eller +46 70 174 06 05 eller 0046701740605")
        assert out == "ring {{secret:marcus-mobile}} eller {{secret:marcus-mobile}} eller {{secret:marcus-mobile}}"
        assert n == 3

    def test_mixed_kinds_in_one_text(self, redactor):
        out, n = redactor.redact("Visa 4111-1111-1111-1111, mail M@MLAB.TEST, mobile 0701740605.")
        assert out == "Visa {{secret:marcus-visa}}, mail {{secret:work-mail}}, mobile {{secret:marcus-mobile}}."
        assert n == 3

    def test_nothing_to_redact_returns_equal_text_and_zero(self, redactor):
        text = "no secrets here, door code 1234"
        assert redactor.redact(text) == (text, 0)

    def test_a_value_containing_another_wins(self, vault):
        vault.add("password", "short", "abc123", id="short")
        vault.add("text", "long", "abc123456xyz", id="long")
        out, n = Redactor(vault).redact("abc123456xyz and abc123")
        assert out == "{{secret:long}} and {{secret:short}}"
        assert n == 2

    def test_no_match_inside_longer_digit_runs(self, redactor):
        text = "ref 946701740605 and 41111111111111112"
        assert redactor.redact(text) == (text, 0)

    def test_logs_ids_and_counts_never_values(self, redactor, logged):
        redactor.redact(f"{MOBILE} {MOBILE} m@mlab.test")
        joined = "\n".join(logged)
        assert "Vault: redacted 3 secret(s) [marcus-mobile ×2, work-mail]" in joined
        for forbidden in ("701740605", "mlab", "0605", "m•••"):
            assert forbidden not in joined


@pytest.mark.unit
class TestRedactMessages:
    def test_str_content_and_text_blocks_redacted_images_shared(self, redactor):
        image = {"type": "image", "source": {"type": "base64", "data": "AAAA"}}
        text_block = {"type": "text", "text": f"my mobile is {MOBILE}", "cache_control": {"type": "ephemeral"}}
        messages = [
            {"role": "system", "content": "prefix <<<CACHE_BREAKPOINT>>> suffix"},
            {"role": "user", "content": f"mail m@mlab.test"},
            {"role": "user", "content": [text_block, image]},
            {"role": "assistant", "content": None},
        ]
        out = redactor.redact_messages(messages)

        assert out is not messages and all(o is not m for o, m in zip(out, messages))
        assert out[0]["content"] == "prefix <<<CACHE_BREAKPOINT>>> suffix"
        assert out[1]["content"] == "mail {{secret:work-mail}}"
        assert out[2]["content"][0] == {"type": "text", "text": "my mobile is {{secret:marcus-mobile}}",
                                        "cache_control": {"type": "ephemeral"}}
        assert out[2]["content"][1] is image
        assert out[3]["content"] is None
        assert [m["role"] for m in out] == ["system", "user", "user", "assistant"]

    def test_originals_are_untouched(self, redactor):
        text_block = {"type": "text", "text": f"mobile {MOBILE}"}
        messages = [{"role": "user", "content": f"mail m@mlab.test"}, {"role": "user", "content": [text_block]}]
        redactor.redact_messages(messages)
        assert messages[0]["content"] == "mail m@mlab.test"
        assert text_block["text"] == f"mobile {MOBILE}"

    def test_logs_one_summary_line(self, redactor, logged):
        redactor.redact_messages([{"role": "user", "content": MOBILE}, {"role": "user", "content": MOBILE}])
        assert [l for l in logged if "redacted" in l] == ["fine: Vault: redacted 2 secret(s) [marcus-mobile ×2]"]


@pytest.mark.unit
class TestRestore:
    def test_known_placeholder_becomes_the_stored_value(self, redactor):
        assert redactor.restore("call {{secret:marcus-mobile}} now") == (f"call {MOBILE} now", 1)

    @pytest.mark.parametrize("form", [
        "{{secret:marcus-mobile}}", "{{ secret: marcus-mobile }}", "{{secret:Marcus_Mobile}}",
        "{{secret:marcus mobile}}", "{{ SECRET : MARCUS-MOBILE }}".replace("SECRET", "secret"),
    ])
    def test_lenient_id_forms(self, redactor, form):
        assert redactor.restore(form) == (MOBILE, 1)

    def test_unknown_id_is_left_visible_and_logged(self, redactor, logged):
        text = "send {{secret:invented-id}} and {{secret:marcus-mobile}}"
        assert redactor.restore(text) == (f"send {{{{secret:invented-id}}}} and {MOBILE}", 1)
        assert "info: Vault: unknown placeholder {{secret:invented-id}} left visible" in logged
        assert "fine: Vault: restored {{secret:marcus-mobile}}" in logged
        assert MOBILE not in "\n".join(logged)

    def test_text_without_braces_is_returned_as_is(self, redactor):
        text = "nothing here"
        out, n = redactor.restore(text)
        assert out is text and n == 0

    def test_other_brace_syntax_is_untouched(self, redactor):
        text = '{"tool": "x"} {{not_a_secret}} {{secret}} {{secret:}}'
        assert redactor.restore(text) == (text, 0)

    def test_restore_of_redacted_gives_the_stored_form(self, redactor):
        redacted, _ = redactor.redact("ring 070 174 06 05 eller mail M@MLAB.TEST")
        assert redactor.restore(redacted)[0] == f"ring {MOBILE} eller mail m@mlab.test"

@pytest.mark.unit
class TestFollowsTheVault:
    def test_rebuilds_when_the_vault_changes(self, vault):
        redactor = Redactor(vault)
        text = f"call {MOBILE}"
        assert redactor.redact(text) == (text, 0)
        vault.add("phone", "Mobile", MOBILE, id="mobile")
        assert redactor.redact(text) == ("call {{secret:mobile}}", 1)
        assert redactor.active
        vault.delete("mobile")
        assert redactor.redact(text) == (text, 0)
        assert redactor.restore("{{secret:mobile}}") == ("{{secret:mobile}}", 0)

    def test_rebuilds_only_on_a_version_change(self, redactor, stocked):
        redactor.redact(MOBILE)
        assert redactor._built.version == stocked.version == 3
        redactor.redact(MOBILE)
        assert redactor._built.version == 3
        stocked.update("marcus-mobile", description="renamed")
        redactor.redact(MOBILE)
        assert redactor._built.version == stocked.version == 4

    def test_a_rebuild_publishes_a_new_snapshot_and_leaves_the_old_one_intact(self, redactor, stocked):
        redactor.redact(MOBILE)
        old = redactor._built
        stocked.delete("work-mail")
        assert redactor._ensure_built() is not old
        assert "work-mail" in old.by_id and "work-mail" in old.group_ids.values()
        assert "work-mail" not in redactor._built.by_id

    def test_a_rebuild_landing_between_two_matches_cannot_mislabel_a_secret(self, vault, monkeypatch):
        # Longest canonical first: a=g0, b=g1, c=g2. Deleting `a` while a redaction is under way
        # renumbers the survivors (b=g0, c=g1); a match of `b` from the pattern in use (lastgroup
        # g1) looked up in the *new* table would come out as `c` — the one thing a vault must
        # never do. Every call has to work from the snapshot it started with.
        vault.add("text", "a", "aaaaaaaaaaaa", id="a")
        vault.add("text", "b", "bbbbbbbbbb", id="b")
        vault.add("text", "c", "cccccccc", id="c")
        redactor = Redactor(vault)
        redactor.redact("warm-up")
        real_placeholder = redactor_module.placeholder

        def placeholder_then_edit(secret_id):
            if "a" in vault:
                vault.delete("a")
                redactor._ensure_built()      # what another thread's request does at that moment
            return real_placeholder(secret_id)

        monkeypatch.setattr(redactor_module, "placeholder", placeholder_then_edit)
        assert redactor.redact("bbbbbbbbbb then bbbbbbbbbb") == ("{{secret:b}} then {{secret:b}}", 2)


@pytest.mark.unit
class TestSpans:
    def test_every_written_form_is_a_span(self, redactor):
        text = "call 070-174 06 05 or +46 70 174 06 05, mail m@mlab.test."
        spans = redactor.spans(text)
        assert [text[s:e] for s, e in spans] == ["070-174 06 05", "+46 70 174 06 05", "m@mlab.test"]

    def test_spans_are_what_redact_swaps(self, redactor):
        text = "card 4111-1111-1111-1111 and nothing else"
        (start, end), = redactor.spans(text)
        assert redactor.redact(text)[0] == text[:start] + "{{secret:marcus-visa}}" + text[end:]

    def test_empty_vault_gives_no_spans(self, vault):
        assert Redactor(vault).spans(MOBILE) == []

    def test_follows_a_vault_change(self, vault):
        redactor = Redactor(vault)
        assert redactor.spans("KEP 47L") == []
        vault.add("plate", "The Model Y", "KEP47L")
        assert redactor.spans("KEP 47L") == [(0, 7)]


@pytest.mark.unit
class TestMask:
    def test_every_written_form_gets_its_masked_form(self, redactor):
        text = "call 070-174 06 05 or +46 70 174 06 05, card 4111-1111-1111-1111, mail m@mlab.test."
        assert redactor.mask(text) == "call •••• 0605 or •••• 0605, card •••• 1111, mail m•••@mlab.test."

    def test_masked_spans_are_the_masks(self, redactor):
        masked = redactor.mask("ring 070-174 06 05 or mail m@mlab.test")
        assert [masked[s:e] for s, e in redactor.masked_spans(masked)] == ["•••• 0605", "m•••@mlab.test"]

    def test_the_longest_mask_wins(self, vault):
        vault.add("email", "Work e-mail", "m@mlab.test", id="work-mail")
        vault.add("password", "Wi-Fi", "hunter2", id="wifi")
        redactor = Redactor(vault)
        masked = redactor.mask("m@mlab.test / hunter2")
        assert masked == "m•••@mlab.test / ••••••••"
        assert [masked[s:e] for s, e in redactor.masked_spans(masked)] == ["m•••@mlab.test", "••••••••"]

    def test_empty_vault_is_an_identity(self, vault):
        assert Redactor(vault).mask(MOBILE) == MOBILE
        assert Redactor(vault).masked_spans("•••• 0605") == []

    def test_follows_a_vault_change(self, vault):
        redactor = Redactor(vault)
        assert redactor.mask("KEP 47L") == "KEP 47L"
        vault.add("plate", "The Model Y", "KEP47L")
        assert redactor.mask("KEP 47L") == "KEP •••"
        assert redactor.masked_spans("KEP •••") == [(0, 7)]
