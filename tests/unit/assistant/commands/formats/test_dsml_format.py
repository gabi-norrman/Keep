"""The DeepSeek DSML format in the V4.1 spelling: rendering the texts, reading the model's calls, streaming."""
import json

import pytest

from assistant.commands.CommandProcessor import CommandProcessor
from assistant.commands.formats import Signatures
from assistant.commands.formats.DsmlFormat import (DsmlFormat, OPEN, CLOSE, INVOKE_OPEN, INVOKE_CLOSE, PARAMETER_OPEN,
                                                   PARAMETER_CLOSE, render_call, render_tool_call)
from assistant.commands.formats.Signatures import Declaration, Value, calls, read_declaration, read_values

NAMES = ["turn_on", "dim", "load_toolkit", "homekit_cover", "restart_server", "browse_eval"]
TABLE = {"turn_on": ["light_name"], "dim": ["light_name", "dim_percentage"], "load_toolkit": ["name"],
         "homekit_cover": ["ref", "position", "dry_run"], "restart_server": [], "browse_eval": ["js"]}
VARIADIC = {"load_toolkit"}


@pytest.fixture
def fmt():
    return DsmlFormat(NAMES, table=TABLE, variadic=VARIADIC)


def declaration(text: str) -> Declaration:
    return read_declaration(calls(text)[0])


def block(name: str, *triples: tuple[str, str, bool]) -> str:
    return render_call(name, list(triples))


def parameter(name: str, value: str, string: bool = True) -> str:
    return f'{PARAMETER_OPEN} name="{name}" string="{"true" if string else "false"}">{value}{PARAMETER_CLOSE}'


class TestSpelling:
    def test_the_v41_spelling_with_spaces_is_the_contract_with_ds4_server(self):
        # ds4-server parses and streams DeepSeek V4.1's spelling (DS41_TOOL_CALLS_START in
        # ds4_server.c), not V4's `<｜DSML｜tool_calls>`; the format must emit exactly these.
        assert OPEN == "<｜DSML｜ calls>" and CLOSE == "</｜DSML｜ calls>"
        assert INVOKE_OPEN == "<｜DSML｜ invoke" and INVOKE_CLOSE == "</｜DSML｜ invoke>"
        assert PARAMETER_OPEN == "<｜DSML｜ parameter" and PARAMETER_CLOSE == "</｜DSML｜ parameter>"

    def test_a_rendered_call_has_the_shape_ds4_streams(self):
        # The streamed content of the probe (plan, 2026-10-20), byte for byte.
        assert block("turn_on", ("light_name", "kitchen lamp", True)) == (
            '<｜DSML｜ calls>\n<｜DSML｜ invoke name="turn_on">\n'
            '<｜DSML｜ parameter name="light_name" string="true">kitchen lamp</｜DSML｜ parameter>\n'
            '</｜DSML｜ invoke>\n</｜DSML｜ calls>')


# --- rendering -----------------------------------------------------------------------------

class TestRendering:
    def test_declaration_is_the_bare_function_object_deepseeks_encoder_lists(self, fmt):
        """The contract with DeepSeek's encoding.py: a tool under `### Available Tool Schemas` is
        `{"name": …, "parameters": …}` on a line of its own — no OpenAI wrapper, no backticks."""
        rendered = fmt.render_declaration(declaration("`dim(light_name: str, dim_percentage: int)`"))
        assert rendered.startswith('{"name": "dim"') and "`" not in rendered
        schema = json.loads(rendered)
        assert set(schema) == {"name", "parameters"}
        parameters = schema["parameters"]
        assert parameters["required"] == ["light_name", "dim_percentage"]
        assert parameters["properties"]["dim_percentage"] == {"type": "integer"}

    def test_a_default_makes_the_parameter_optional_and_a_variadic_one_an_array(self, fmt):
        parameters = json.loads(fmt.render_declaration(
            declaration("`homekit_cover(ref: str, position: open|close|0-100, dry_run: str = \"\")`")))["parameters"]
        assert parameters["required"] == ["ref", "position"]
        assert parameters["properties"]["position"] == {"type": "string", "description": "open|close|0-100"}
        assert parameters["properties"]["dry_run"]["default"] == ""
        variadic = json.loads(fmt.render_declaration(declaration("`load_toolkit(name: str, ...)`")))
        assert variadic["parameters"]["properties"]["name"] == {"type": "array", "items": {"type": "string"}}

    def test_example_pairs_values_with_the_declared_names_strings_raw_and_numbers_as_json(self, fmt):
        call = calls('`dim("Kitchen lamp", 40)`')[0]
        rendered = fmt.render_example("dim", read_values(call), declaration("`dim(light_name: str, dim_percentage: int)`"))
        assert rendered == block("dim", ("light_name", "Kitchen lamp", True), ("dim_percentage", "40", False))
        assert parameter("dim_percentage", "40", string=False) in rendered

    def test_example_resolves_an_escaped_quote_and_renders_a_block_value_whole(self, fmt):
        decl = declaration("`turn_on(light_name: str)`")
        assert fmt.render_example("turn_on", [Value('Say \\"hi\\"', "string")], decl) == block("turn_on", ("light_name", 'Say "hi"', True))
        rendered = fmt.render_example("turn_on", [Value("line one\nline two", "block")], decl)
        assert parameter("light_name", "line one\nline two") in rendered

    def test_variadic_example_puts_every_value_in_one_json_list(self, fmt):
        call = calls('`load_toolkit("email", "web")`')[0]
        rendered = fmt.render_example("load_toolkit", read_values(call), declaration("`load_toolkit(name: str, ...)`"))
        assert rendered == block("load_toolkit", ("name", '["email", "web"]', False))

    def test_a_closing_tag_inside_a_string_value_is_escaped_the_way_ds4_documents_and_read_back(self, fmt):
        decl = declaration("`turn_on(light_name: str)`")
        value = f"a {PARAMETER_CLOSE} b &lt;{PARAMETER_CLOSE[1:]} c"
        rendered = fmt.render_example("turn_on", [Value(value, "string")], decl)
        assert rendered.count(PARAMETER_CLOSE) == 1  # the real closer only
        assert fmt.extract(rendered)[0][1] == [value]

    def test_instructions_teach_the_block_and_name_no_real_command_call(self, fmt):
        """The rewriter runs over the filled-in instructions: a backticked call of a real command
        in them would be rendered, so the text must carry none."""
        text = fmt.instructions()
        assert OPEN in text and CLOSE in text and INVOKE_OPEN in text and 'string="true|false"' in text
        assert "<｜DSML｜tool_calls>" not in text  # the V4 spelling would teach a block ds4 still parses but the format does not
        assert not [c for c in calls(text) if c.name in Signatures.table()]

    def test_real_texts_render_without_a_keep_declaration_left(self):
        from assistant.commands.formats import Rewriter
        from assistant.commands.Commands import Commands
        fmt = DsmlFormat(Commands.command_names())
        table = Signatures.declarations()
        for label, text in Signatures.texts().items():
            rendered = Rewriter.rewrite(text, fmt, table)
            left = [c for c in calls(rendered) if read_declaration(c) is not None and c.name in table]
            assert not left, f"{label}: {[c.name for c in left]}"


# --- parsing -------------------------------------------------------------------------------

class TestParsing:
    def test_round_trip_declaration_to_model_call_to_positional_params(self, fmt):
        decl = declaration("`dim(light_name: str, dim_percentage: int)`")
        rendered = fmt.render_example("dim", [Value("Kitchen lamp", "string"), Value("40", "bare")], decl)
        (name, params, display), rest = fmt.extract(rendered)
        assert (name, params) == ("dim", ["Kitchen lamp", "40"])
        assert display == "Kitchen lamp, 40" and rest == ""

    def test_parameters_in_the_models_order_are_put_in_the_declared_order(self, fmt):
        text = block("dim", ("dim_percentage", "1", False), ("light_name", "Hall lamp", True))
        assert fmt.extract(text)[0][1] == ["Hall lamp", "1"]

    def test_json_values_are_decoded_to_the_string_the_command_takes(self, fmt):
        text = block("homekit_cover", ("ref", '"A1B2"', False), ("position", "40", False), ("dry_run", "true", False))
        assert fmt.extract(text)[0][1] == ["A1B2", "40", "true"]

    def test_a_json_value_that_does_not_parse_is_kept_as_written(self, fmt):
        assert fmt.extract(block("turn_on", ("light_name", "Hall lamp", False)))[0][1] == ["Hall lamp"]

    def test_a_parameter_without_the_string_attribute_is_raw(self, fmt):
        text = f'{OPEN}\n{INVOKE_OPEN} name="turn_on">\n{PARAMETER_OPEN} name="light_name">"Lamp"{PARAMETER_CLOSE}\n{INVOKE_CLOSE}\n{CLOSE}'
        assert fmt.extract(text)[0][1] == ['"Lamp"']

    def test_an_optional_parameter_left_out_in_the_middle_is_an_empty_string_and_trailing_ones_are_dropped(self, fmt):
        assert fmt.extract(block("homekit_cover", ("ref", "A1B2", True), ("dry_run", "dry_run", True)))[0][1] == ["A1B2", "", "dry_run"]
        assert fmt.extract(block("homekit_cover", ("ref", "A1B2", True), ("position", "40", False)))[0][1] == ["A1B2", "40"]

    def test_an_undeclared_command_or_parameter_keeps_the_models_order(self, fmt):
        assert fmt.extract(block("mystery", ("b", "2", False), ("a", "1", False)))[0][1] == ["2", "1"]
        assert fmt.extract(block("dim", ("brightness", "5", False), ("light_name", "Lamp", True)))[0][1] == ["5", "Lamp"]

    @pytest.mark.parametrize("value, string", [('["email", "web"]', False), ("email, web", True), ("email,web", True)])
    def test_a_variadic_parameter_is_split_into_positional_values(self, fmt, value, string):
        assert fmt.extract(block("load_toolkit", ("name", value, string)))[0][1] == ["email", "web"]

    def test_a_string_value_keeps_its_newlines_and_spaces(self, fmt):
        text = block("browse_eval", ("js", "\nreturn 1;\n", True))
        assert fmt.extract(text)[0][1] == ["\nreturn 1;\n"]

    def test_several_invokes_in_one_block_come_back_in_order_through_rest(self, fmt):
        text = (f'{OPEN}\n{INVOKE_OPEN} name="turn_on">\n{parameter("light_name", "A")}\n{INVOKE_CLOSE}\n'
                f'{INVOKE_OPEN} name="dim">\n{parameter("dim_percentage", "5", False)}\n{parameter("light_name", "B")}\n{INVOKE_CLOSE}\n{CLOSE} tail')
        first, rest = fmt.extract(text)
        assert first[:2] == ("turn_on", ["A"])
        second, rest2 = fmt.extract(rest)
        assert second[:2] == ("dim", ["B", "5"]) and rest2 == " tail"
        assert fmt.extract_all(text) == [("turn_on", ["A"]), ("dim", ["B", "5"])]

    def test_text_before_and_after_the_block_is_located_and_kept(self, fmt):
        text = "Sure. " + block("turn_on", ("light_name", "Lamp", True)) + " Done."
        assert not fmt.is_possibly_command(text)
        assert fmt.find_start(text) == len("Sure. ")
        assert fmt.extract(text)[1] == " Done."

    def test_an_incomplete_block_is_possibly_a_command_but_not_complete(self, fmt):
        partial = f'{OPEN}\n{INVOKE_OPEN} name="turn_on">\n{PARAMETER_OPEN} name="light_name" string="true">Kit'
        assert fmt.is_possibly_command(partial) and not fmt.complete(partial, streaming=True)
        assert fmt.extract(partial) == (None, None)
        assert fmt.unterminated(partial) == "turn_on"

    @pytest.mark.parametrize("buffer, possible", [("", True), ("  ", True), ("<", True), ("<｜DSML", True),
                                                  ("<｜DSML｜ call", True), ("<｜DSML｜tool_calls>", False),
                                                  ("<b>", False), ("Hello", False)])
    def test_a_prefix_of_the_opener_may_still_become_a_call(self, fmt, buffer, possible):
        assert fmt.is_possibly_command(buffer) is possible

    def test_partial_tail_is_the_opener_prefix_at_the_end_of_text(self, fmt):
        assert fmt.partial_tail("Turning it on <｜DS") == len("Turning it on ")
        assert fmt.partial_tail("<｜DS") == -1
        assert fmt.partial_tail("Turning it on <b") == -1
        assert fmt.partial_tail("Turning it on") == -1

    def test_a_block_without_an_invoke_is_no_call(self, fmt):
        assert fmt.extract(f"{OPEN}\nnothing\n{CLOSE}\nDone.") == (None, "\nDone.")
        assert fmt.unterminated(f"{OPEN}\nnoth") is None

    def test_a_bare_name_or_plain_text_is_never_unterminated(self, fmt):
        assert fmt.unterminated("turn_on") is None
        assert fmt.unterminated("just text") is None


class TestRenderToolCall:
    def test_a_parsed_tool_call_is_written_back_with_strings_raw_and_the_rest_as_json(self, fmt):
        text = render_tool_call("dim", '{"light_name": "Kitchen lamp", "dim_percentage": 40}')
        assert parameter("light_name", "Kitchen lamp") in text and parameter("dim_percentage", "40", False) in text
        assert fmt.extract(text)[0][1] == ["Kitchen lamp", "40"]

    def test_decoded_arguments_and_a_list_value_round_trip(self, fmt):
        text = render_tool_call("load_toolkit", {"name": ["email", "web"]})
        assert fmt.extract(text)[0][1] == ["email", "web"]

    def test_arguments_that_are_not_a_json_object_become_one_value(self, fmt):
        assert fmt.extract(render_tool_call("turn_on", "not json"))[0][1] == ["not json"]
        assert fmt.extract(render_tool_call("restart_server", ""))[0][:2] == ("restart_server", [])


# --- streaming through the real processor --------------------------------------------------

class TestStreaming:
    REPLY = ("Sure thing.\n" + block("dim", ("dim_percentage", "40", False), ("light_name", "Kitchen lamp", True))
             + "\n" + block("load_toolkit", ("name", '["email", "web"]', False)) + "\nDone.")

    @pytest.fixture
    def processor(self):
        processor = CommandProcessor(test_suite_mode=True)
        processor.format = DsmlFormat(processor.possible_commands, table=TABLE, variadic=VARIADIC)
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

    def test_split_at_every_character_extracts_both_calls_and_cuts_the_text_after_the_load(self, processor):
        # load_toolkit is stop-and-wait: the prose after it is dropped (the after-the-fetch guard).
        out = self._stream(processor, list(self.REPLY))
        assert processor.calls == [("dim", ["Kitchen lamp", "40"]), ("load_toolkit", ["email", "web"])]
        assert OPEN not in out and INVOKE_OPEN not in out
        assert out.startswith("Sure thing.") and "Done." not in out and processor.reply_cut

    def test_the_whole_reply_in_one_chunk_gives_the_same_result(self, processor):
        out = self._stream(processor, [self.REPLY])
        assert processor.calls == [("dim", ["Kitchen lamp", "40"]), ("load_toolkit", ["email", "web"])]
        assert OPEN not in out and out.startswith("Sure thing.") and "Done." not in out and processor.reply_cut

    def test_the_server_shape_a_blank_line_then_the_block_is_a_call_and_no_text(self, processor):
        # ds4 streams "\n\n" and then the block; the processor must show nothing and run the call.
        out = self._stream(processor, ["\n\n", block("turn_on", ("light_name", "kitchen lamp", True))])
        assert processor.calls == [("turn_on", ["kitchen lamp"])]
        assert not out.strip()

    def test_a_stream_cut_inside_a_block_reports_the_unterminated_command(self, processor):
        from unittest.mock import Mock
        dialog = Mock(is_killed=False)
        cut = self.REPLY[:self.REPLY.index(PARAMETER_CLOSE)]
        out = self._stream(processor, list(cut), dialog)
        assert processor.calls == []
        assert out.strip() == "Sure thing."
        assert "dim was not run" in processor.take_unterminated_command_report()


class TestCut:
    def test_a_block_of_several_invokes_is_cut_inside_and_closed_again(self, fmt):
        invokes = "\n".join(f'{INVOKE_OPEN} name="turn_on">\n{PARAMETER_OPEN} name="light_name" string="true">lamp {i}'
                            f'{PARAMETER_CLOSE}\n{INVOKE_CLOSE}' for i in range(3))
        kept = fmt.cut(f"{OPEN}\n{invokes}\n{CLOSE}\nDone.", 2)
        assert [p for _, p in fmt.extract_all(kept)] == [["lamp 0"], ["lamp 1"]]
        assert kept.endswith(CLOSE) and "Done." not in kept
