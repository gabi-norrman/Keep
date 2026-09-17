"""RestoringStream: placeholders are restored whole, ordinary text is never delayed
(VAULT_PLAN §3.5)."""
import pytest

from common.vault.Redactor import Redactor, RestoringStream
from common.vault.Vault import Vault

KEY = "correct horse battery staple"
MOBILE = "+46701740605"
TEXT = "call {{secret:marcus-mobile}} now"
LENIENT = "call {{ secret : marcus-mobile }} now"     # the padded form restore() also accepts
EXPECTED = f"call {MOBILE} now"


@pytest.fixture
def redactor(tmp_path):
    vault = Vault(tmp_path / "vault.bin", KEY)
    vault.add("phone", "Marcus's mobile", MOBILE, id="marcus-mobile")
    vault.add("password", "abcdef", "SECRET-VALUE", id="abcdef")
    return Redactor(vault)


def run(chunks, redactor):
    return list(RestoringStream(iter(chunks), redactor))


def assert_never_partial(chunks):
    for chunk in chunks:
        for length in range(1, len("{{secret:") + 1):
            assert "{{secret:"[:length] not in chunk or "{{secret:" in chunk, chunk
        assert MOBILE not in chunk or chunk.count(MOBILE) == chunk.count("+46701740605")


@pytest.mark.unit
class TestSplits:
    @pytest.mark.parametrize("i", range(len(TEXT) + 1))
    def test_every_two_chunk_split_restores_the_value_in_one_piece(self, redactor, i):
        out = run([TEXT[:i], TEXT[i:]], redactor)
        assert "".join(out) == EXPECTED
        assert_never_partial(out)
        assert sum(MOBILE in chunk for chunk in out) == 1

    def test_one_char_per_chunk(self, redactor):
        out = run(list(TEXT), redactor)
        assert "".join(out) == EXPECTED
        assert_never_partial(out)
        assert sum(MOBILE in chunk for chunk in out) == 1
        assert out[:4] == ["c", "a", "l", "l"]

    def test_whole_text_in_one_chunk(self, redactor):
        assert run([TEXT], redactor) == [EXPECTED]

    def test_two_placeholders_in_one_stream(self, redactor):
        out = run(["{{secret:marcus-mobile}} and {{sec", "ret:abcdef}}!"], redactor)
        assert "".join(out) == f"{MOBILE} and SECRET-VALUE!"
        assert out[0] == f"{MOBILE} and "

    @pytest.mark.parametrize("i", range(len(LENIENT) + 1))
    def test_every_two_chunk_split_of_a_padded_placeholder_restores_whole(self, redactor, i):
        out = run([LENIENT[:i], LENIENT[i:]], redactor)
        assert "".join(out) == EXPECTED
        assert sum(MOBILE in chunk for chunk in out) == 1
        assert all("{{" not in chunk for chunk in out), out

    def test_padded_placeholder_one_char_per_chunk(self, redactor):
        out = run(list(LENIENT), redactor)
        assert "".join(out) == EXPECTED
        assert sum(MOBILE in chunk for chunk in out) == 1
        assert out[:5] == ["c", "a", "l", "l", " "]


@pytest.mark.unit
class TestHoldBack:
    def test_plain_text_is_not_delayed(self, redactor):
        pulled = []

        def inner():
            for chunk in ["hello ", "world", " never"]:
                pulled.append(chunk)
                yield chunk

        stream = iter(RestoringStream(inner(), redactor))
        assert next(stream) == "hello " and pulled == ["hello "]
        assert next(stream) == "world" and pulled == ["hello ", "world"]

    def test_json_brace_is_flushed_at_once(self, redactor):
        stream = iter(RestoringStream(iter(['{"tool": "x"}', "rest"]), redactor))
        assert next(stream) == '{"tool": "x"}'

    def test_lone_brace_at_chunk_end_is_held_for_the_next_chunk(self, redactor):
        assert run(["abc{", "def"], redactor) == ["abc", "{def"]
        assert run(["abc{{", "def"], redactor) == ["abc", "{{def"]
        assert run(["abc{{sec", "def"], redactor) == ["abc", "{{secdef"]
        assert run(["abc{{ sec", "def"], redactor) == ["abc", "{{ secdef"]
        assert run(["abc{{ secret ", "def"], redactor) == ["abc", "{{ secret def"]

    def test_something_that_can_no_longer_be_a_placeholder_is_flushed(self, redactor):
        assert run(["abc{ {", "def"], redactor) == ["abc{ ", "{def"]
        assert run(["abc{{x", "def"], redactor) == ["abc{{x", "def"]
        assert run(["abc{{ x", "def"], redactor) == ["abc{{ x", "def"]

    def test_open_placeholder_is_held_until_closed(self, redactor):
        out = run(["see {{secret:abc", "de", "f}} ok"], redactor)
        assert out == ["see ", "SECRET-VALUE ok"]

    def test_unterminated_placeholder_released_after_80_chars(self, redactor):
        long_tail = "{{secret:" + "x" * 71
        assert len(long_tail) == 80
        assert run([long_tail], redactor) == [long_tail]
        held = "{{secret:" + "x" * 70
        assert run([held, "y"], redactor) == [held + "y"]

    def test_unterminated_placeholder_released_at_newline(self, redactor):
        assert run(["{{secret:abc\n", "more"], redactor) == ["{{secret:abc\n", "more"]

    def test_end_of_stream_flushes_whatever_is_pending(self, redactor):
        assert run(["text {{sec"], redactor) == ["text ", "{{sec"]
        assert run(["{{secret:abc"], redactor) == ["{{secret:abc"]
        assert run(["{"], redactor) == ["{"]

    def test_unknown_id_passes_through_visibly(self, redactor):
        assert run(["{{secret:no", "pe}} x"], redactor) == ["{{secret:nope}} x"]

    def test_empty_chunks_and_empty_stream(self, redactor):
        assert run([], redactor) == []
        assert run(["", "a{", "", "b"], redactor) == ["a", "{b"]

    def test_hold_from(self):
        assert RestoringStream.hold_from("abc") == 3
        assert RestoringStream.hold_from("abc{") == 3
        assert RestoringStream.hold_from("abc{{secret:") == 3
        assert RestoringStream.hold_from("abc{{secret:x}}") == 15
        assert RestoringStream.hold_from("abc{{secret:x}}{") == 15
        assert RestoringStream.hold_from('{"tool":') == 8
        assert RestoringStream.hold_from("{{secret:" + "x" * 71) == 80
        assert RestoringStream.hold_from("{{secret:x\n") == 11

    def test_hold_from_padded_forms(self):
        assert RestoringStream.hold_from("abc{{ ") == 3
        assert RestoringStream.hold_from("abc{{ sec") == 3
        assert RestoringStream.hold_from("abc{{ secret") == 3
        assert RestoringStream.hold_from("abc{{ secret :") == 3
        assert RestoringStream.hold_from("abc{{ secret : x") == 3
        assert RestoringStream.hold_from("abc{{ secret : x }}") == 19
        assert RestoringStream.hold_from("abc{ {") == 5
        assert RestoringStream.hold_from("abc{{x") == 6
        assert RestoringStream.hold_from("abc{{ x") == 7
        assert RestoringStream.hold_from("abc{{\nsecret") == 12
