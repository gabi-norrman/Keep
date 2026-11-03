"""The rewriter and the render side of the command formats.

A visible fake format shows what the rewriter finds and how it pairs an example's values with
the declared names; the Keep format over the real template, every toolkit and its own
instructions must be the identity — the proof that the finder touches calls and nothing else.
"""
import pytest

from assistant.commands.Commands import Commands
from assistant.commands.formats import Rewriter, Signatures
from assistant.commands.formats.CommandFormat import CommandFormat
from assistant.commands.formats.KeepFormat import KeepFormat, INSTRUCTIONS
from assistant.commands.formats.Signatures import Call, Declaration, Parameter, Value

NAMES = ["turn_on", "dim", "load_toolkit", "save_watch", "restart_server", "browse_eval", "latest_email"]

SAMPLE = """\
Lights: `turn_on(light_name: str)` and `dim(light_name: str, dim_percentage: int = 100)`, e.g. `dim("Kitchen, left", 1)`.
Load toolkits with `load_toolkit(name: str, ...)` — several at once: `load_toolkit("email", "web")`.
Mention `turn_on` or `turn_on()` without calling; `restart_server()` is a call. A `word(x)` is not a command.
`latest_email(folder: str = "", unread_only: bool = false)`, `save_watch(name: str, note: str, due: str = "")`, `browse_eval(js: str)`.
Not canonical: `turn_on(Kitchen lamp)`. A block: `save_watch("sofa", \"\"\"two
lines\"\"\")`. Opaque: `browse_eval(document.querySelector('a, b').href)`. Latest: `latest_email("@SaneLater", true)`.
"""

TABLE = Signatures.build_table({"sample": SAMPLE}, NAMES)


class VisibleFormat(CommandFormat):
    """Renders what it was handed, so a test sees the finder's decisions in the output."""
    name = "visible"

    def instructions(self):
        return "VISIBLE INSTRUCTIONS"

    def render_declaration(self, declaration):
        tail = ", ..." if declaration.variadic else ""
        return "<decl " + declaration.command + "(" + ", ".join(
            p.name + (f"={p.default}" if p.default else "") for p in declaration.params) + tail + ")>"

    def render_example(self, name, values, declaration):
        names = declaration.names if declaration else []
        pairs = []
        for i, v in enumerate(values):
            key = names[i] if i < len(names) else (names[-1] if declaration and declaration.variadic else "?")
            pairs.append(f"{key}={v.kind}:{v.value}")
        return "<call " + name + " " + " ".join(pairs) + ">"

    @classmethod
    def render_tool_call(cls, name, arguments): return "<tool " + name + ">"

    def is_possibly_command(self, buffer): return False
    def find_start(self, buffer): return -1
    def partial_tail(self, buffer): return -1
    def complete(self, buffer, streaming=False): return False
    def extract(self, buffer): return None, None
    def call_end(self, buffer): return -1
    def unterminated(self, buffer): return None
    def extract_all(self, text): return []


@pytest.mark.unit
class TestRewriter:

    @pytest.fixture()
    def out(self):
        return Rewriter.rewrite(SAMPLE, VisibleFormat(NAMES), TABLE)

    def test_declarations_render_with_names_defaults_and_the_variadic(self, out):
        assert "<decl turn_on(light_name)>" in out
        assert '<decl dim(light_name, dim_percentage=100)>' in out
        assert "<decl load_toolkit(name, ...)>" in out

    def test_examples_pair_values_with_the_declared_names_in_order(self, out):
        assert "<call dim light_name=string:Kitchen, left dim_percentage=bare:1>" in out, "a comma in a value stays in it"
        assert "<call load_toolkit name=string:email name=string:web>" in out, "the variadic name takes the rest"
        assert "<call latest_email folder=string:@SaneLater unread_only=bare:true>" in out
        assert "<decl restart_server()>" in out, "`name()` of a parameterless command is its declaration, never a call block"
        assert "<call save_watch name=string:sofa note=block:two\nlines>" in out

    def test_an_opaque_payload_is_one_raw_value(self, out):
        assert "<call browse_eval js=raw:document.querySelector('a, b').href>" in out

    def test_mentions_non_commands_and_unreadable_calls_stay_as_written(self, out):
        assert "Mention `turn_on` or `turn_on()` without calling" in out, \
            "`name()` of a command with parameters is neither a declaration nor an example: left as written"
        assert "A `word(x)` is not a command." in out
        assert "Not canonical: `turn_on(Kitchen lamp)`." in out

    def test_a_default_valued_argument_is_handed_over_for_the_format_to_judge(self):
        out = Rewriter.rewrite('`save_watch("sofa", "note", "")`', VisibleFormat(NAMES), TABLE)
        assert out == "<call save_watch name=string:sofa note=string:note due=string:>", \
            "the rewriter pairs every value; leaving a default out is the named formats' choice"


GATED = """\
Before.
<!-- keep -->
Only Keep's parser reads `)` as the end: `restart_server()`.
<!-- /keep -->
After.
"""


@pytest.mark.unit
class TestKeepOnlyPassages:

    def test_the_keep_format_keeps_the_passage_without_its_markers(self):
        out = Rewriter.rewrite(GATED, KeepFormat(NAMES), TABLE)
        assert out == "Before.\nOnly Keep's parser reads `)` as the end: `restart_server()`.\nAfter.\n"

    def test_every_other_format_drops_the_passage_whole(self):
        out = Rewriter.rewrite(GATED, VisibleFormat(NAMES), TABLE)
        assert out == "Before.\nAfter.\n"
        assert "restart_server" not in out, "a call inside the passage goes with it, not rendered"

    def test_an_unpaired_marker_is_left_as_written(self):
        text = "Before.\n<!-- keep -->\nNo closer. `restart_server()`\n"
        assert Rewriter.rewrite(text, VisibleFormat(NAMES), TABLE) == "Before.\n<!-- keep -->\nNo closer. <decl restart_server()>\n"
        assert Rewriter.rewrite(text, KeepFormat(NAMES), TABLE) == text

    def test_a_marker_inside_a_line_is_no_marker(self):
        text = "Inline <!-- keep --> here <!-- /keep --> stays.\n"
        assert Rewriter.rewrite(text, VisibleFormat(NAMES), TABLE) == text

    def test_a_command_the_table_does_not_know_is_left_alone(self):
        assert Rewriter.rewrite("`dim(\"x\", 1)` and `turn_on(\"y\")`", VisibleFormat(NAMES),
                                {"turn_on": TABLE["turn_on"]}) == '`dim("x", 1)` and <call turn_on light_name=string:y>'

    def test_more_values_than_declared_is_left_as_written(self):
        assert Rewriter.rewrite('`turn_on("a", "b")`', VisibleFormat(NAMES), TABLE) == '`turn_on("a", "b")`'

    def test_a_text_without_calls_is_returned_unchanged(self):
        text = "No commands here, only `turn_on` mentioned."
        assert Rewriter.rewrite(text, VisibleFormat(NAMES), TABLE) is text


@pytest.mark.unit
class TestKeepFormatRendering:

    def test_sample_is_rendered_identically(self):
        assert Rewriter.rewrite(SAMPLE, KeepFormat(NAMES), TABLE) == SAMPLE

    def test_declaration_spelling(self):
        fmt = KeepFormat(NAMES)
        decl = Declaration("latest_email", (Parameter("folder", "str", '""'), Parameter("unread_only", "bool", "false")))
        assert fmt.render_declaration(decl) == '`latest_email(folder: str = "", unread_only: bool = false)`'
        assert fmt.render_declaration(Declaration("load_toolkit", (Parameter("name", "str"),), True)) == "`load_toolkit(name: str, ...)`"
        assert fmt.render_declaration(Declaration("restart_server", ())) == "`restart_server()`"

    def test_example_keeps_each_value_in_the_form_it_was_written(self):
        fmt = KeepFormat(NAMES)
        values = [Value('Kitchen \\"lamp\\"', "string"), Value("two\nlines", "block"), Value("-2.5", "bare"), Value("a(1", "raw")]
        assert fmt.render_example("dim", values, TABLE["dim"]) == '`dim("Kitchen \\"lamp\\"", """two\nlines""", -2.5, a(1)`'

    def test_instructions_teach_the_quoted_form_and_render_identically(self):
        fmt = KeepFormat(NAMES)
        assert 'turn_on("Kitchen lamp")' in fmt.instructions()
        assert "parentheses" in fmt.instructions(), "how to mention a command without calling it"
        assert Rewriter.rewrite(INSTRUCTIONS, fmt, Signatures.declarations()) == INSTRUCTIONS


@pytest.mark.unit
class TestReadValues:

    def test_kinds_and_spelling(self):
        values = Signatures.read_values(Call("dim", '"Kitchen \\"lamp\\"", """a, b""", 1, true, -2.5', 0, 0))
        assert [(v.kind, v.text) for v in values] == [("string", 'Kitchen \\"lamp\\"'), ("block", "a, b"),
                                                      ("bare", "1"), ("bare", "true"), ("bare", "-2.5")]
        assert values[0].value == 'Kitchen "lamp"', "the runtime's reading resolves the escape"
        assert Signatures.read_values(Call("restart_server", "", 0, 0)) == []

    @pytest.mark.parametrize("inner", ["Kitchen lamp", "'x'", '"a" b', '"a",'])
    def test_not_canonical_is_none(self, inner):
        assert Signatures.read_values(Call("turn_on", inner, 0, 0)) is None

    def test_agrees_with_the_runtime_parser_on_every_real_example(self):
        """What the rewriter hands a format is what the runtime would read from the same text."""
        table = Signatures.declarations()
        for label, text in Signatures.texts().items():
            for call in Signatures.calls(text):
                if call.name not in table or Signatures.read_declaration(call) is not None or not call.inner:
                    continue
                example = Signatures.read_example(call)
                values = Signatures.read_values(call)
                assert values is not None and example.problem is None, f"{label}: `{call.name}(…)`"
                assert [v.value for v in values] == example.values, f"{label}: `{call.name}(…)`"


@pytest.mark.unit
class TestIdentityOverTheRealTexts:
    """The Keep format renders the texts the LLM reads byte for byte: the finder touches the
    calls and nothing else, and every example is canonical enough to be written back."""

    @pytest.mark.parametrize("label", sorted(Signatures.texts()))
    def test_template_and_every_toolkit_render_identically(self, label):
        text = Signatures.texts()[label]
        fmt = KeepFormat(Commands.command_names())
        expected = text.replace(Rewriter.KEEP_ONLY_OPEN + "\n", "").replace(Rewriter.KEEP_ONLY_CLOSE + "\n", "")
        assert Rewriter.rewrite(text, fmt, Signatures.declarations()) == expected, "identical but for the Keep-only markers"

    def test_the_template_carries_the_command_system_placeholder(self):
        prompt = Signatures.texts()["res/prompt.md"]
        assert "{COMMAND_SYSTEM}" in prompt
        assert prompt.index("# COMMAND SYSTEM") < prompt.index("{COMMAND_SYSTEM}") < prompt.index("## Toolkits")
        assert "python syntax" not in prompt and "NEVER express a command" not in prompt, \
            "the format-specific prose lives in the format's instructions"

    def test_declarations_and_table_are_one_cached_reading(self):
        Signatures.reset()
        declarations = Signatures.declarations()
        assert declarations is Signatures.declarations()
        assert Signatures.table() == {c: d.names for c, d in declarations.items()}
        assert declarations["load_toolkit"].variadic and declarations["restart_server"].params == ()
