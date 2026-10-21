"""The prose of the prompt and the toolkits assumes no call syntax (docs/plans/FORMAT_NEUTRAL_TEXTS_PLAN.md).

The rewriter renders the calls in the active format; the sentences around them must be true in
every format. The convention: a mention is the bare name, `name()` declares a parameterless
command, the prose states facts about values and never how to type them, and prose only Keep's
syntax needs stands between `<!-- keep -->` and `<!-- /keep -->` lines.
"""
import re

import pytest

from assistant.commands.Commands import Commands
from assistant.commands.formats import Rewriter, Signatures
from assistant.commands.formats.DsmlFormat import DsmlFormat
from assistant.commands.formats.InvokeFormat import InvokeFormat
from assistant.commands.formats.QwenFormat import QwenFormat
from assistant.commands.formats.Rewriter import KEEP_ONLY_OPEN, KEEP_ONLY_CLOSE
from assistant.commands.formats.Signatures import calls, read_declaration, read_values

# Wording that describes Keep's syntax rather than a value: each has a format-neutral phrasing.
KEEP_ONLY_WORDING = re.compile(
    r"triple.quoted|in quotes|, quoted\b|quote (both|each)\b|parenthes|the command parser|"
    r"\b(first|second|third|fourth|fifth|last) argument\b|use this syntax exactly", re.I)

_NUMBER = re.compile(r"-?\d+(\.\d+)?$")


def _prose(text: str) -> str:
    """The text without its Keep-only passages: those may say what they like."""
    return Rewriter.rewrite(text, QwenFormat([]), {})


def _labelled():
    return sorted(Signatures.texts())


@pytest.mark.unit
class TestConvention:

    @pytest.mark.parametrize("label", _labelled())
    def test_a_mention_is_the_bare_name(self, label):
        """`name()` of a command with parameters is neither its declaration nor an example: the
        rewriter leaves it as written, which in a block format is a Keep call shape."""
        table = Signatures.declarations()
        text = Signatures.texts()[label]
        offending = [c.name for c in calls(text) if c.name in table and not c.inner and table[c.name].params]
        assert not offending, f"{label}: write the bare name for {sorted(set(offending))}"

    @pytest.mark.parametrize("label", _labelled())
    def test_no_wording_about_keeps_syntax_outside_a_keep_only_passage(self, label):
        prose = _prose(Signatures.texts()[label])
        hits = [f"reference line {prose.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r}" for m in KEEP_ONLY_WORDING.finditer(prose)]
        assert not hits, f"{label}: {hits}"

    @pytest.mark.parametrize("label", _labelled())
    def test_every_example_fits_its_declaration(self, label):
        """A `bool` parameter takes true/false, an `int`/`float` a number — the schema a
        named format renders beside the example says so."""
        table = Signatures.declarations()
        text = Signatures.texts()[label]
        misfits = []
        for call in calls(text):
            declaration = table.get(call.name)
            if declaration is None or read_declaration(call) is not None or not call.inner:
                continue
            values = read_values(call)
            if values is None:
                continue
            for value, parameter in zip(values, declaration.params):
                if parameter.type == "bool" and (value.kind != "bare" or value.text not in ("true", "false")):
                    misfits.append(f"{call.name}: {parameter.name} is a bool, written {value.text!r}")
                if parameter.type in ("int", "float") and (value.kind != "bare" or not _NUMBER.match(value.text)):
                    misfits.append(f"{call.name}: {parameter.name} is a {parameter.type}, written {value.text!r}")
        assert not misfits, f"{label}: {misfits}"


@pytest.mark.unit
class TestRendering:

    @pytest.mark.parametrize("fmt_class", [InvokeFormat, QwenFormat, DsmlFormat])
    def test_no_marker_and_no_call_block_for_a_parameterless_command(self, fmt_class):
        """Rendered for a block format: the Keep-only passages are gone, markers and all, and
        a parameterless command's `name()` is a declaration line, not a call in mid-sentence."""
        fmt = fmt_class(Commands.command_names())
        table = Signatures.declarations()
        parameterless = {name for name, d in table.items() if not d.params}
        for label, text in Signatures.texts().items():
            rendered = Rewriter.rewrite(text, fmt, table)
            assert KEEP_ONLY_OPEN not in rendered and KEEP_ONLY_CLOSE not in rendered, label
            blocks = re.findall(re.escape(fmt.OPEN) + r".*?" + re.escape(fmt.CLOSE), rendered, re.S)
            called = [b for b in blocks if any(f'{n}>' in b or f'"{n}"' in b for n in parameterless)]
            assert not called, f"{label}: {called[:2]}"

    def test_the_keep_format_sees_every_keep_only_passage_and_no_marker(self):
        from assistant.commands.formats.KeepFormat import KeepFormat
        fmt = KeepFormat(Commands.command_names())
        for label, text in Signatures.texts().items():
            rendered = Rewriter.rewrite(text, fmt, Signatures.declarations())
            assert KEEP_ONLY_OPEN not in rendered and KEEP_ONLY_CLOSE not in rendered, label
            assert text.count(KEEP_ONLY_OPEN) == text.count(KEEP_ONLY_CLOSE), f"{label}: unpaired marker"
