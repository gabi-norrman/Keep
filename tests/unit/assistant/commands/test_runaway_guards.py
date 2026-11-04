"""The repeat and degenerate-value guards of the command processor (issue #123): a reply that
writes the same call over and over, or rescued `<invoke>` blocks holding punctuation alone,
is cut where the runaway shows instead of at the 15-command cap."""
import pytest

from assistant.commands.CommandProcessor import CommandProcessor
from assistant.commands.formats.KeepFormat import KeepFormat
from assistant.commands.formats.RescueFormat import RescueFormat


def invoke(name: str, **values: str) -> str:
    lines = [f'<invoke name="{name}">'] + [f'<parameter name="{k}">{v}</parameter>' for k, v in values.items()] + ["</invoke>"]
    return "\n".join(lines) + "\n\n"


@pytest.fixture
def processor():
    processor = CommandProcessor(test_suite_mode=True)
    processor.format = RescueFormat.wrap(KeepFormat(processor.possible_commands))
    processor.calls = []
    dispatch = processor.invoke_command
    processor.invoke_command = lambda name, params, dm: (processor.calls.append((name, params)), dispatch(name, params, dm))
    yield processor
    processor.kill()


def feed(processor, *chunks) -> str:
    shown = [processor.process(chunk, None) for chunk in chunks]
    shown.append(processor.flush_buffer(None))
    processor.command_queue.join()
    return "".join(s for s in shown if s)


@pytest.mark.unit
class TestRepeatGuard:
    def test_a_stop_and_wait_call_written_twice_is_cut_at_the_second(self, processor):
        """Prod 2026-11-04: `load_watch` 15 times in a row to an "ok"."""
        raw = "load_watch(radon)\nload_watch(radon)\nload_watch(radon)\nDone.\n"
        shown = feed(processor, raw)
        assert shown == "" and processor.reply_cut
        assert processor.calls == [("load_watch", ["radon"])]
        assert processor.kept_of(raw).strip() == "load_watch(radon)"
        assert "load_watch" in processor.command_reply and "2 times" in processor.command_reply
        assert not processor.command_reply_contains_error   # nothing failed: a note, not an error report

    def test_the_15_empty_server_logs_of_10_26_stop_at_the_first_repeat(self, processor):
        raw = invoke("server_logs", from_time="12:00", pattern="x") + invoke("server_logs") * 14
        feed(processor, raw)
        assert processor.reply_cut
        assert [name for name, _ in processor.calls] == ["server_logs", "server_logs"]   # with args, then one bare
        assert len(processor.format.extract_all(processor.kept_of(raw))) == 2

    def test_an_accumulating_call_may_repeat_up_to_the_allowance(self, processor):
        allowance = CommandProcessor.MAX_IDENTICAL_CALLS
        shown = feed(processor, "volume_up(kitchen)\n" * allowance + "Louder now.")
        assert not processor.reply_cut and len(processor.calls) == allowance
        assert "Louder now." in shown

    def test_one_past_the_allowance_cuts_the_reply(self, processor):
        allowance = CommandProcessor.MAX_IDENTICAL_CALLS
        raw = "volume_up(kitchen)\n" * (allowance + 5) + "Louder now."
        shown = feed(processor, raw)
        assert shown == "" and processor.reply_cut
        assert len(processor.calls) == allowance
        assert processor.kept_of(raw).count("volume_up") == allowance
        assert f"{allowance + 1} times" in processor.command_reply

    def test_the_same_command_with_other_arguments_is_no_repeat(self, processor):
        feed(processor, "\n".join(f"turn_on(lamp {i})" for i in range(8)) + "\n")
        assert not processor.reply_cut and len(processor.calls) == 8

    def test_a_call_between_two_copies_breaks_the_run(self, processor):
        feed(processor, "volume_up(kitchen)\nvolume_up(hall)\n" * 4)
        assert not processor.reply_cut and len(processor.calls) == 8

    def test_the_run_is_the_replys_not_the_processors(self, processor):
        feed(processor, "load_watch(radon)\n")
        processor.clear_command_reply()
        processor.start_new_reply()
        feed(processor, "load_watch(radon)\n")
        assert not processor.reply_cut and len(processor.calls) == 2


@pytest.mark.unit
class TestDegenerateValueGuard:
    JUNK = '"\n}\n]'

    def test_rescued_browse_eval_blocks_of_punctuation_cut_the_reply_after_the_real_call(self, processor):
        """Prod 2026-10-29 16:34: one real `browse_eval`, then 313 blocks of `"`, `}` and `]`."""
        raw = invoke("browse_eval", js="return document.title") + invoke("browse_eval", js=self.JUNK) * 20
        feed(processor, raw)
        assert processor.reply_cut
        assert processor.calls == [("browse_eval", ["return document.title"])]
        assert len(processor.format.extract_all(processor.kept_of(raw))) == 1
        assert "browse_eval" in processor.command_reply and not processor.command_reply_contains_error

    def test_a_rescued_call_with_real_values_runs(self, processor):
        feed(processor, invoke("turn_on", light_name="lamp"))
        assert not processor.reply_cut and processor.calls == [("turn_on", ["lamp"])]

    def test_a_rescued_call_with_no_values_is_left_alone(self, processor):
        feed(processor, invoke("load_watch"))
        assert not processor.reply_cut and [name for name, _ in processor.calls] == ["load_watch"]

    def test_a_call_in_the_taught_syntax_is_not_checked(self, processor):
        feed(processor, 'remember("}")\n')
        assert not processor.reply_cut and processor.calls == [("remember", ["}"])]

    def test_the_rescue_counter_starts_over_with_the_reply(self, processor):
        feed(processor, invoke("turn_on", light_name="lamp"))
        assert processor.format.rescued == 1
        processor.start_new_reply()
        assert processor.format.rescued == 0
