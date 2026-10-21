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
    def test_a_declaration_is_the_function_line_of_anthropics_tool_listing(self, fmt):
        import json
        decl = declaration("`homekit_cover(ref: str, position: open|close|0-100, dry_run: str = \"\")`")
        line = fmt.render_declaration(decl)
        assert line.startswith("<function>{") and line.endswith("}</function>")
        schema = json.loads(line[len("<function>"):-len("</function>")])
        assert set(schema) == {"name", "parameters"}, "no OpenAI `function` wrapper, no description — the prose is it"
        assert schema["name"] == "homekit_cover"
        params = schema["parameters"]
        assert params["required"] == ["ref", "position"] and "default" in params["properties"]["dry_run"]
        assert params["properties"]["position"]["description"] == "open|close|0-100"
        variadic = json.loads(fmt.render_declaration(declaration("`load_toolkit(name: str, ...)`"))[10:-11])
        assert variadic["parameters"]["properties"]["name"] == {"type": "array", "items": {"type": "string"}}

    def test_no_keep_shaped_call_survives_in_a_rendered_text(self, fmt):
        """The leak the schema line closes: a `name(param: type)` declaration is a fillable call shape."""
        from assistant.commands.formats import Rewriter
        text = "`dim(light_name: str, dim_percentage: int)` dims. Mention `dim`. Try `dim(\"Lamp\", 40)`. `stop()` stops."
        table = {"dim": declaration("`dim(light_name: str, dim_percentage: int)`"), "stop": Declaration("stop", ())}
        rendered = Rewriter.rewrite(text, fmt, table)
        assert "dim(" not in rendered and "stop(" not in rendered
        assert "Mention `dim`." in rendered
        assert rendered.count("<function>{") == 2 and rendered.count(OPEN) == 1, \
            "`stop()` is the declaration of a parameterless command, not a call block"

    def test_a_value_equal_to_the_declared_default_is_left_out(self, fmt):
        decl = declaration("`save_watch(name: str, note: str, due: str = \"\", every: str = \"\")`")
        rendered = fmt.render_example("save_watch", read_values(calls('`save_watch("x", "n", "", "1d")`')[0]), decl)
        assert rendered == block("save_watch", ("name", "x"), ("note", "n"), ("every", "1d")), \
            "the texts write \"\" to skip an optional parameter the Keep way; a named format leaves it out"
        decl = declaration("`latest_email(folder: str = \"\", unread_only: bool = false)`")
        rendered = fmt.render_example("latest_email", read_values(calls('`latest_email("", true)`')[0]), decl)
        assert rendered == block("latest_email", ("unread_only", "true"))

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

    def test_real_texts_render_every_example_as_a_block_and_every_declaration_as_a_function_line(self):
        from assistant.commands.formats import Rewriter
        from assistant.commands.Commands import Commands
        fmt = InvokeFormat(Commands.command_names())
        table = Signatures.declarations()
        for label, text in Signatures.texts().items():
            rendered = Rewriter.rewrite(text, fmt, table)
            examples = [c for c in calls(rendered) if c.name in table and read_declaration(c) is None
                        and (c.inner or not table[c.name].params)]
            assert not examples, f"{label}: {[c.name for c in examples]}"
            declared = {c.name for c in calls(text) if read_declaration(c) is not None and c.name in table}
            declarations = [c for c in calls(text) if c.name in table
                            and (read_declaration(c) is not None or (not c.inner and not table[c.name].params))]
            assert rendered.count("<function>{") == len(declarations), label
            assert not [c for c in calls(rendered) if c.name in table and read_declaration(c) is not None], label
            for name in declared:
                assert f'"name": "{name}"' in rendered, (label, name)


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


class TestBareInvoke:
    """Opus 5.5 leaves the <function_calls> wrapper out about one reply in three on the full
    prompt: the bare invoke is read as a block of one, exactly as the wrapped one is."""

    BARE = f'{INVOKE_OPEN} name="turn_on">\n{parameter("light_name", "Lamp")}\n{INVOKE_CLOSE}'

    def test_a_bare_invoke_is_read_like_a_wrapped_one(self, fmt):
        assert fmt.extract(self.BARE + " Done.") == (("turn_on", ["Lamp"], "Lamp"), " Done.")
        assert fmt.extract(block("turn_on", ("light_name", "Lamp")) + " Done.")[0] == ("turn_on", ["Lamp"], "Lamp")
        assert fmt.call_end(self.BARE + " Done.") == len(self.BARE)
        assert fmt.extract_all("Sure. " + self.BARE + "\n" + block("dim", ("light_name", "Lamp"), ("dim_percentage", "40"))) \
            == [("turn_on", ["Lamp"]), ("dim", ["Lamp", "40"])]

    def test_located_held_back_and_completed_like_a_wrapped_one(self, fmt):
        assert fmt.find_start("Sure. " + self.BARE) == len("Sure. ")
        assert fmt.partial_tail("Turning it on <inv") == len("Turning it on ")
        assert fmt.is_possibly_command("<inv") and fmt.is_possibly_command(self.BARE[:30])
        assert not fmt.complete(self.BARE[:-3], streaming=True) and fmt.complete(self.BARE)
        assert fmt.unterminated(self.BARE[:-3]) == "turn_on"

    def test_a_wrapped_block_is_still_read_whole_when_its_invoke_follows_the_opener(self, fmt):
        # Inside the wrapper the invoke must not be taken for a bare one: the block's rest is after </function_calls>.
        two = block("turn_on", ("light_name", "Lamp")) + "\nDone."
        assert fmt.extract(two) == (("turn_on", ["Lamp"], "Lamp"), "\nDone.")

    def test_an_invoke_like_word_that_never_closes_is_released_as_text(self, fmt):
        assert fmt.is_possibly_command("<invoked>")
        assert not fmt.complete("<invoked> by the alarm, the lights came on.")
        assert fmt.unterminated("<invoked> by the alarm") is None

    def test_a_bare_element_without_a_name_is_dropped_like_an_empty_block(self, fmt):
        assert fmt.extract(f"{INVOKE_OPEN}>\n{INVOKE_CLOSE}\nDone.") == (None, "\nDone.")


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

    def test_the_measured_bare_reply_streams_to_the_call_and_no_text(self, processor):
        # What Opus 5.5 streamed on 2026-10-21 for "What's in the server logs?": the invoke alone, no wrapper.
        reply = f'{INVOKE_OPEN} name="restart_server">\n{INVOKE_CLOSE}'
        out = self._stream(processor, list(reply))
        assert processor.calls == [("restart_server", [])]
        assert out.strip() == ""

    def test_a_bare_invoke_and_a_wrapped_block_in_one_reply_both_run(self, processor):
        reply = ("On it. " + f'{INVOKE_OPEN} name="turn_on">\n{parameter("light_name", "Lamp")}\n{INVOKE_CLOSE}'
                 + "\n" + block("dim", ("light_name", "Lamp"), ("dim_percentage", "40")) + "\nDone.")
        out = self._stream(processor, [reply[:9], reply[9:50], reply[50:]])
        assert processor.calls == [("turn_on", ["Lamp"]), ("dim", ["Lamp", "40"])]
        assert out.strip().startswith("On it.") and out.strip().endswith("Done.") and INVOKE_OPEN not in out

    def test_a_stream_cut_inside_a_block_reports_the_unterminated_command(self, processor):
        from unittest.mock import Mock
        dialog = Mock(is_killed=False)
        cut = self.REPLY[:self.REPLY.index(PARAMETER_CLOSE)]
        out = self._stream(processor, list(cut), dialog)
        assert processor.calls == []
        assert out.strip() == "Sure thing."
        assert "dim was not run" in processor.take_unterminated_command_report()


class TestCut:
    """`cut` keeps a reply up to its n-th call, the block closed — what the processor's
    command cap stores of a runaway reply (2026-10-21: 51 bare search invokes in one turn)."""

    @staticmethod
    def _bare(n: int) -> str:
        return "\n\n".join(f'{INVOKE_OPEN} name="turn_on">\n{parameter("light_name", f"lamp {i}")}\n{INVOKE_CLOSE}'
                           for i in range(n))

    @staticmethod
    def _wrapped(n: int) -> str:
        invokes = "\n".join(f'{INVOKE_OPEN} name="turn_on">\n{parameter("light_name", f"lamp {i}")}\n{INVOKE_CLOSE}'
                            for i in range(n))
        return f"{OPEN}\n{invokes}\n{CLOSE}"

    def test_bare_invokes_are_cut_after_the_nth_and_an_opener_started_after_it_is_dropped(self, fmt):
        text = "Checking.\n\n" + self._bare(4) + "\n\n" + INVOKE_OPEN + ' name="tur'
        kept = fmt.cut(text, 2)
        assert kept.startswith("Checking.")
        assert [p for _, p in fmt.extract_all(kept)] == [["lamp 0"], ["lamp 1"]]
        assert kept.endswith(INVOKE_CLOSE)

    def test_a_wrapped_block_is_cut_inside_and_closed_again(self, fmt):
        kept = fmt.cut(self._wrapped(4) + "\nDone.", 2)
        assert [p for _, p in fmt.extract_all(kept)] == [["lamp 0"], ["lamp 1"]]
        assert kept.endswith(CLOSE) and kept.count(OPEN) == 1 and "Done." not in kept

    def test_blocks_before_the_cut_are_kept_as_written(self, fmt):
        text = self._wrapped(2) + "\n" + self._wrapped(3)
        kept = fmt.cut(text, 4)
        assert kept.startswith(self._wrapped(2))
        assert len(fmt.extract_all(kept)) == 4 and kept.endswith(CLOSE)

    def test_a_reply_within_the_cap_comes_back_whole(self, fmt):
        text = "Sure.\n\n" + self._wrapped(3) + "\nDone."
        assert fmt.cut(text, 3) == text
        assert fmt.cut(text, 10) == text
        assert fmt.cut("No calls at all.", 10) == "No calls at all."
