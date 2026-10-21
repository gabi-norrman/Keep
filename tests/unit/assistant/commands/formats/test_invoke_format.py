"""Claude's `<function_calls>` / `<invoke>` format: rendering the texts, reading the model's calls, streaming."""
import pytest

from assistant.commands.CommandProcessor import CommandProcessor
from assistant.commands.formats import Signatures
from assistant.commands.formats.CommandFormats import CommandFormats
from assistant.commands.formats.InvokeFormat import (InvokeFormat, OPEN, CLOSE, INVOKE_OPEN, INVOKE_CLOSE,
                                                     PARAMETER_OPEN, PARAMETER_CLOSE, render_call)
from assistant.commands.formats.Signatures import Declaration, Value, calls, read_declaration, read_values

NAMES = ["turn_on", "dim", "load_toolkit", "homekit_cover", "restart_server", "browse_eval"]
TABLE = {"turn_on": ["light_name"], "dim": ["light_name", "dim_percentage"], "load_toolkit": ["name"],
         "homekit_cover": ["ref", "position", "dry_run"], "restart_server": [], "browse_eval": ["js"]}
VARIADIC = {"load_toolkit"}


@pytest.fixture
def fmt():
    return InvokeFormat(NAMES, table=TABLE, variadic=VARIADIC)


def declaration(text: str) -> Declaration:
    return read_declaration(calls(text)[0])


def block(name: str, *pairs: tuple[str, str]) -> str:
    return render_call(name, list(pairs))


def parameter(name: str, value: str) -> str:
    return f'{PARAMETER_OPEN} name="{name}">{value}{PARAMETER_CLOSE}'


class TestSpelling:
    def test_the_block_is_the_one_the_anthropic_api_passes_through_as_text(self):
        # The probe of 2026-10-20 (plan, step 7): Opus 5.5 and Haiku 5.5 wrote exactly this shape
        # from the instruction and the API handed it back as text with no tools declared.
        assert block("dim", ("light_name", "kitchen lamp"), ("dim_percentage", "40")) == (
            '<function_calls>\n<invoke name="dim">\n<parameter name="light_name">kitchen lamp</parameter>\n'
            '<parameter name="dim_percentage">40</parameter>\n</invoke>\n</function_calls>')

    def test_invoke_is_the_name_the_command_format_config_key_takes(self):
        assert CommandFormats.by_name(" Invoke ") is InvokeFormat


# --- rendering -----------------------------------------------------------------------------

class TestRendering:
    def test_a_declaration_keeps_keeps_signature_text(self, fmt):
        decl = declaration("`homekit_cover(ref: str, position: open|close|0-100, dry_run: str = \"\")`")
        assert fmt.render_declaration(decl) == '`homekit_cover(ref: str, position: open|close|0-100, dry_run: str = "")`'
        assert fmt.render_declaration(declaration("`load_toolkit(name: str, ...)`")) == "`load_toolkit(name: str, ...)`"

    def test_example_pairs_values_with_the_declared_names_without_quotes(self, fmt):
        call = calls('`dim("Kitchen lamp", 40)`')[0]
        rendered = fmt.render_example("dim", read_values(call), declaration("`dim(light_name: str, dim_percentage: int)`"))
        assert rendered == block("dim", ("light_name", "Kitchen lamp"), ("dim_percentage", "40"))

    def test_example_resolves_an_escaped_quote_and_renders_a_block_value_whole(self, fmt):
        decl = declaration("`turn_on(light_name: str)`")
        assert fmt.render_example("turn_on", [Value('Say \\"hi\\"', "string")], decl) == block("turn_on", ("light_name", 'Say "hi"'))
        rendered = fmt.render_example("turn_on", [Value("line one\nline two", "block")], decl)
        assert parameter("light_name", "line one\nline two") in rendered

    def test_variadic_example_puts_every_value_in_one_json_list(self, fmt):
        call = calls('`load_toolkit("email", "web")`')[0]
        rendered = fmt.render_example("load_toolkit", read_values(call), declaration("`load_toolkit(name: str, ...)`"))
        assert rendered == block("load_toolkit", ("name", '["email", "web"]'))

    def test_a_value_beyond_the_declaration_gets_a_positional_name(self, fmt):
        rendered = fmt.render_example("turn_on", [Value("A", "string"), Value("B", "string")], declaration("`turn_on(light_name: str)`"))
        assert parameter("arg2", "B") in rendered

    def test_instructions_teach_the_block_and_name_no_real_command_call(self, fmt):
        """The rewriter runs over the filled-in instructions: a backticked call of a real command
        in them would be rendered, so the text must carry none."""
        text = fmt.instructions()
        assert OPEN in text and CLOSE in text and INVOKE_OPEN in text and PARAMETER_OPEN in text
        assert "```" not in text
        assert not [c for c in calls(text) if c.name in Signatures.table()]

    def test_real_texts_render_every_example_as_a_block_and_keep_the_declarations(self):
        from assistant.commands.formats import Rewriter
        from assistant.commands.Commands import Commands
        fmt = InvokeFormat(Commands.command_names())
        table = Signatures.declarations()
        for label, text in Signatures.texts().items():
            rendered = Rewriter.rewrite(text, fmt, table)
            examples = [c for c in calls(rendered) if c.name in table and read_declaration(c) is None
                        and (c.inner or not table[c.name].params)]
            assert not examples, f"{label}: {[c.name for c in examples]}"
            before = {c.name for c in calls(text) if read_declaration(c) is not None and c.name in table}
            after = {c.name for c in calls(rendered) if read_declaration(c) is not None and c.name in table}
            assert after == before, label


# --- parsing -------------------------------------------------------------------------------

class TestParsing:
    def test_round_trip_declaration_to_model_call_to_positional_params(self, fmt):
        decl = declaration("`dim(light_name: str, dim_percentage: int)`")
        rendered = fmt.render_example("dim", [Value("Kitchen lamp", "string"), Value("40", "bare")], decl)
        (name, params, display), rest = fmt.extract(rendered)
        assert (name, params) == ("dim", ["Kitchen lamp", "40"])
        assert display == "Kitchen lamp, 40" and rest == ""

    def test_parameters_in_the_models_order_are_put_in_the_declared_order(self, fmt):
        text = block("dim", ("dim_percentage", "1"), ("light_name", "Hall lamp"))
        assert fmt.extract(text)[0][1] == ["Hall lamp", "1"]

    def test_an_optional_parameter_left_out_in_the_middle_is_an_empty_string_and_trailing_ones_are_dropped(self, fmt):
        assert fmt.extract(block("homekit_cover", ("ref", "A1B2"), ("dry_run", "dry_run")))[0][1] == ["A1B2", "", "dry_run"]
        assert fmt.extract(block("homekit_cover", ("ref", "A1B2"), ("position", "40")))[0][1] == ["A1B2", "40"]

    def test_an_undeclared_command_or_parameter_keeps_the_models_order(self, fmt):
        assert fmt.extract(block("mystery", ("b", "2"), ("a", "1")))[0][1] == ["2", "1"]
        assert fmt.extract(block("dim", ("brightness", "5"), ("light_name", "Lamp")))[0][1] == ["5", "Lamp"]

    @pytest.mark.parametrize("value", ['["email", "web"]', "email, web", "email,web"])
    def test_a_variadic_parameter_is_split_into_positional_values(self, fmt, value):
        assert fmt.extract(block("load_toolkit", ("name", value)))[0][1] == ["email", "web"]

    def test_a_value_is_raw_text_quotes_included(self, fmt):
        assert fmt.extract(block("turn_on", ("light_name", '"Lamp"')))[0][1] == ['"Lamp"']

    def test_a_value_on_its_own_lines_loses_one_newline_each_side_and_keeps_the_rest(self, fmt):
        text = f'{OPEN}\n{INVOKE_OPEN} name="browse_eval">\n{PARAMETER_OPEN} name="js">\nreturn 1;\n\n{PARAMETER_CLOSE}\n{INVOKE_CLOSE}\n{CLOSE}'
        assert fmt.extract(text)[0][1] == ["return 1;\n"]

    def test_the_tag_spelling_tolerates_extra_blanks_and_an_upper_case_name(self, fmt):
        text = f'{OPEN}\n<invoke   name="Turn_On" >\n<parameter  name="light_name" >Lamp</parameter>\n</invoke>\n{CLOSE}'
        assert fmt.extract(text)[0][:2] == ("turn_on", ["Lamp"])

    def test_several_invokes_in_one_block_come_back_in_order_through_rest(self, fmt):
        text = (f'{OPEN}\n{INVOKE_OPEN} name="turn_on">\n{parameter("light_name", "A")}\n{INVOKE_CLOSE}\n'
                f'{INVOKE_OPEN} name="dim">\n{parameter("dim_percentage", "5")}\n{parameter("light_name", "B")}\n{INVOKE_CLOSE}\n{CLOSE} tail')
        first, rest = fmt.extract(text)
        assert first[:2] == ("turn_on", ["A"])
        second, rest2 = fmt.extract(rest)
        assert second[:2] == ("dim", ["B", "5"]) and rest2 == " tail"
        assert fmt.extract_all(text) == [("turn_on", ["A"]), ("dim", ["B", "5"])]

    def test_text_before_and_after_the_block_is_located_and_kept(self, fmt):
        text = "Sure. " + block("turn_on", ("light_name", "Lamp")) + " Done."
        assert not fmt.is_possibly_command(text)
        assert fmt.find_start(text) == len("Sure. ")
        assert fmt.extract(text)[1] == " Done."

    def test_an_incomplete_block_is_possibly_a_command_but_not_complete(self, fmt):
        partial = f'{OPEN}\n{INVOKE_OPEN} name="turn_on">\n{PARAMETER_OPEN} name="light_name">Kit'
        assert fmt.is_possibly_command(partial) and not fmt.complete(partial, streaming=True)
        assert fmt.extract(partial) == (None, None)
        assert fmt.unterminated(partial) == "turn_on"

    @pytest.mark.parametrize("buffer, possible", [("", True), ("  ", True), ("<", True), ("<function", True),
                                                  ("<function_calls", True), ("<function_result>", False),
                                                  ("<tool_call>", False), ("<b>", False), ("Hello", False)])
    def test_a_prefix_of_the_opener_may_still_become_a_call(self, fmt, buffer, possible):
        assert fmt.is_possibly_command(buffer) is possible

    def test_partial_tail_is_the_opener_prefix_at_the_end_of_text(self, fmt):
        assert fmt.partial_tail("Turning it on <func") == len("Turning it on ")
        assert fmt.partial_tail("<func") == -1
        assert fmt.partial_tail("Turning it on <b") == -1
        assert fmt.partial_tail("Turning it on") == -1

    def test_a_block_without_an_invoke_is_no_call(self, fmt):
        assert fmt.extract(f"{OPEN}\nnothing\n{CLOSE}\nDone.") == (None, "\nDone.")
        assert fmt.unterminated(f"{OPEN}\nnoth") is None

    def test_a_bare_name_or_plain_text_is_never_unterminated(self, fmt):
        assert fmt.unterminated("turn_on") is None
        assert fmt.unterminated("just text") is None


class TestRenderToolCall:
    def test_a_parsed_tool_call_is_written_back_as_the_block_and_read_again(self, fmt):
        text = InvokeFormat.render_tool_call("dim", '{"light_name": "Kitchen lamp", "dim_percentage": 40}')
        assert parameter("light_name", "Kitchen lamp") in text and parameter("dim_percentage", "40") in text
        assert fmt.extract(text)[0][1] == ["Kitchen lamp", "40"]

    def test_decoded_arguments_and_a_list_value_round_trip(self, fmt):
        text = InvokeFormat.render_tool_call("load_toolkit", {"name": ["email", "web"]})
        assert fmt.extract(text)[0][1] == ["email", "web"]

    def test_arguments_that_are_not_a_json_object_become_one_value(self, fmt):
        assert fmt.extract(InvokeFormat.render_tool_call("turn_on", "not json"))[0][1] == ["not json"]
        assert fmt.extract(InvokeFormat.render_tool_call("restart_server", ""))[0][:2] == ("restart_server", [])


# --- streaming through the real processor --------------------------------------------------

class TestStreaming:
    REPLY = ("Sure thing.\n\n" + block("dim", ("dim_percentage", "40"), ("light_name", "Kitchen lamp"))
             + "\n" + block("load_toolkit", ("name", '["email", "web"]')) + "\nDone.")

    @pytest.fixture
    def processor(self):
        processor = CommandProcessor(test_suite_mode=True)
        processor.format = InvokeFormat(processor.possible_commands, table=TABLE, variadic=VARIADIC)
        processor.calls = []
        processor.invoke_command = lambda name, params, dm: processor.calls.append((name, params))
        yield processor
        processor.kill()

    @staticmethod
    def _stream(processor, chunks, dialog_manager=None):
        out = ""
        for chunk in chunks:
            if (text := processor.process(chunk, dialog_manager)) is not None:
                out += text
        if (text := processor.flush_buffer(dialog_manager)) is not None:
            out += text
        return out

    def test_split_at_every_character_extracts_both_calls_and_releases_the_text_only(self, processor):
        out = self._stream(processor, list(self.REPLY))
        assert processor.calls == [("dim", ["Kitchen lamp", "40"]), ("load_toolkit", ["email", "web"])]
        assert OPEN not in out and INVOKE_OPEN not in out
        assert out.startswith("Sure thing.") and out.rstrip().endswith("Done.")

    def test_the_whole_reply_in_one_chunk_gives_the_same_result(self, processor):
        out = self._stream(processor, [self.REPLY])
        assert processor.calls == [("dim", ["Kitchen lamp", "40"]), ("load_toolkit", ["email", "web"])]
        assert OPEN not in out and out.startswith("Sure thing.") and out.rstrip().endswith("Done.")

    def test_the_probed_reply_a_sentence_a_blank_line_and_the_block(self, processor):
        # What Opus 5.5 streamed in the probe: the sentence first, then the block and nothing after.
        reply = "Dimming the kitchen lamp to 40 percent.\n\n" + block("dim", ("light_name", "kitchen lamp"), ("dim_percentage", "40"))
        out = self._stream(processor, [reply[:17], reply[17:40], reply[40:]])
        assert processor.calls == [("dim", ["kitchen lamp", "40"])]
        assert out.strip() == "Dimming the kitchen lamp to 40 percent."

    def test_a_stream_cut_inside_a_block_reports_the_unterminated_command(self, processor):
        from unittest.mock import Mock
        dialog = Mock(is_killed=False)
        cut = self.REPLY[:self.REPLY.index(PARAMETER_CLOSE)]
        out = self._stream(processor, list(cut), dialog)
        assert processor.calls == []
        assert out.strip() == "Sure thing."
        assert "dim was not run" in processor.take_unterminated_command_report()
