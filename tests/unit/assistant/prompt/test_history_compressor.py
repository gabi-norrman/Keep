"""HistoryCompressor: a browse snapshot that a later snapshot superseded renders as its header
plus a one-line note; the newest snapshot, every other row and the rows themselves stay as
they are, and the result depends on nothing but the rows."""
from unittest.mock import patch

from assistant.prompt.HistoryCompressor import HistoryCompressor
from assistant.prompt.MessageHistory import MessageHistory
from assistant.tools import SnapshotFormat


def _snapshot(url: str, elements: list[str], notes: str = "", text: str = "Some page prose",
              groups: str = "", iframes: str = "") -> str:
    lines = [f"[{i}] {e}" for i, e in enumerate(elements)]
    return (f"{notes}URL: {url}\nTitle: Checkout\nPage: h1 \"Din varukorg\"\nAlerts: \"Ogiltigt postnummer\"\n"
            f"Text: {text}\n\n{SnapshotFormat.ELEMENTS_MARKER}\n" + "\n".join(lines) + groups + iframes)


def _row(role: str, content: str, ts: float) -> tuple[dict, str, str]:
    entry = {"role": role, "content": content, "timestamp": ts, "source": None, "location": None}
    rendered_role = "user" if role != "assistant" else "assistant"
    return entry, rendered_role, content


OLDER = "result: \n" + _snapshot("https://shop.example/product", ['link "Villamätning" -> https://shop.example/p/1',
                                                                   'button "Lägg i varukorg"',
                                                                   'select "Antal" value=\'1\' options=[1,2,3]'])
NEWER = "result: \n" + _snapshot("https://shop.example/checkout", ['input[email] "E-post"', 'button "Betala"'])


class TestSupersededSnapshots:
    def test_older_snapshot_keeps_header_and_loses_elements_newer_stays_whole(self):
        rows = [_row("marcus", "buy it", 1.0), _row("assistant", 'browse_open("…")', 2.0),
                _row("command", OLDER, 3.0), _row("assistant", 'browse_act("1")', 4.0), _row("command", NEWER, 5.0)]
        out = HistoryCompressor().compress(rows)
        older = out[2][2]
        assert "URL: https://shop.example/product" in older
        assert 'Page: h1 "Din varukorg"' in older and 'Alerts: "Ogiltigt postnummer"' in older
        assert "Lägg i varukorg" not in older and SnapshotFormat.ELEMENTS_MARKER not in older
        assert "Text: " not in older, "the prose digest goes with the elements"
        assert "3 interactive elements omitted" in older and "indices are stale" in older
        assert out[4][2] == NEWER
        assert [r[2] for r in out[:2]] == [rows[0][2], rows[1][2]] and out[3][2] == rows[3][2]
        assert rows[2][2] == OLDER, "the row itself is untouched"

    def test_notes_above_the_header_survive(self):
        popup = _snapshot("https://pay.example/popup", ['button "Continue with BankID"'],
                          notes="NOTE: the page opened a popup/new window (https://pay.example/popup).\n\n")
        out = HistoryCompressor().compress([_row("command", popup, 1.0), _row("command", NEWER, 2.0)])
        assert out[0][2].startswith("NOTE: the page opened a popup/new window")

    def test_single_snapshot_is_left_alone(self):
        rows = [_row("marcus", "hi", 1.0), _row("command", NEWER, 2.0)]
        assert HistoryCompressor().compress(rows) == rows

    def test_non_command_rows_with_a_marker_are_not_snapshots(self):
        user = _row("marcus", SnapshotFormat.ELEMENTS_MARKER + "\n[0] button \"x\"", 1.0)
        out = HistoryCompressor().compress([user, _row("command", NEWER, 2.0)])
        assert out[0][2] == user[2]

    def test_collapsed_groups_count_towards_the_element_total(self):
        groups = (f"\n\n{SnapshotFormat.GROUP_SEPARATOR}\nnav: 3 items — [2] Privat, [3] Företag, [4] Kontakt"
                  f"\nfooter: 2 items — [5] Villkor, [6] Integritet")
        older = "result: \n" + _snapshot("https://shop.example/a", ['button "a"', 'button "b"'], groups=groups)
        out = HistoryCompressor().compress([_row("command", older, 1.0), _row("command", NEWER, 2.0)])
        assert "7 interactive elements omitted" in out[0][2]
        assert "Privat" not in out[0][2]

    def test_iframe_note_is_part_of_the_element_list(self):
        iframes = (f"\n\n{SnapshotFormat.IFRAMES_NOTE} (their contents are NOT in the snapshot above):\n"
                   "  - https://js.pay.example/frame.html")
        older = "result: \n" + _snapshot("https://shop.example/a", ['button "a"'], iframes=iframes)
        out = HistoryCompressor().compress([_row("command", older, 1.0), _row("command", NEWER, 2.0)])
        assert "js.pay.example" not in out[0][2]

    def test_empty_page_collapses_to_its_own_note(self):
        older = f"result: \nURL: https://shop.example/blank\nTitle: \n\n{SnapshotFormat.ELEMENTS_MARKER}\n{SnapshotFormat.NO_ELEMENTS}"
        out = HistoryCompressor().compress([_row("command", older, 1.0), _row("command", NEWER, 2.0)])
        assert "no interactive elements; a later snapshot replaced this one" in out[0][2]

    def test_batched_round_keeps_the_other_commands_result(self):
        procedure = "# Procedure: online-purchases\nWhen: buying something\n1. Check the cart"
        older = "result: \n" + _snapshot("https://shop.example/a", ['button "a"']) + "\n\n" + procedure
        out = HistoryCompressor().compress([_row("command", older, 1.0), _row("command", NEWER, 2.0)])
        assert out[0][2].endswith("\n\n" + procedure), "the blank-line separator and the second result stay"
        assert '[0] button "a"' not in out[0][2]

    def test_two_snapshots_in_one_row_collapse_all_but_the_last(self):
        first = _snapshot("https://shop.example/a", ['button "a"'])
        second = _snapshot("https://shop.example/b", ['button "b"'])
        row = _row("command", "result: \n" + first + "\n\n" + second, 1.0)
        out = HistoryCompressor().compress([row])
        assert '[0] button "a"' not in out[0][2] and '[0] button "b"' in out[0][2]


class TestStability:
    """The compressed bytes depend on the rows alone — the sticky trim window relies on it."""

    def test_compressing_twice_changes_nothing_more(self):
        rows = [_row("command", OLDER, 1.0), _row("command", NEWER, 2.0)]
        once = HistoryCompressor().compress(rows)
        assert HistoryCompressor().compress(once) == once

    def test_rows_without_a_snapshot_appended_later_leave_earlier_bytes_alone(self):
        rows = [_row("command", OLDER, 1.0), _row("command", NEWER, 2.0)]
        before = HistoryCompressor().compress(rows)
        after = HistoryCompressor().compress(rows + [_row("marcus", "done", 3.0), _row("assistant", "👍", 4.0)])
        assert after[:2] == before[:2]

    def test_a_new_snapshot_changes_only_the_one_it_supersedes(self):
        rows = [_row("command", OLDER, 1.0), _row("command", NEWER, 2.0)]
        before = HistoryCompressor().compress(rows)
        newest = "result: \n" + _snapshot("https://shop.example/done", ['link "Kvitto" -> https://shop.example/r'])
        after = HistoryCompressor().compress(rows + [_row("command", newest, 3.0)])
        assert after[0] == before[0]
        assert after[1][2] != NEWER and after[2][2] == newest


class TestInTheHistoryWindow:
    def _history(self):
        with patch.object(MessageHistory, '_load_history'):
            section = MessageHistory()
        section.trim_high_bytes = 6_000
        section.trim_low_bytes = 3_000
        return section

    def _browse_session(self, snapshots: int) -> list[dict]:
        entries = [{"role": "marcus", "content": "please buy the radon kit", "timestamp": 1.0,
                    "source": "Telegram", "location": None}]
        for i in range(snapshots):
            elements = [f'link "Item {j}" -> https://shop.example/items/{i}/{j}' for j in range(30)]
            entries.append({"role": "assistant", "content": f'browse_act("{i}")', "timestamp": 2.0 + 2 * i,
                            "source": None, "location": None})
            entries.append({"role": "command", "content": "result: \n" + _snapshot(f"https://shop.example/p{i}", elements),
                            "timestamp": 3.0 + 2 * i, "source": None, "location": None})
        return entries

    def test_superseded_snapshots_no_longer_push_the_request_out_of_the_window(self):
        section = self._history()
        section.history = self._browse_session(snapshots=6)   # ~2KB of elements each: 12KB uncompressed vs a 6KB high mark
        messages = section.get_history_messages()
        assert section._trim_cutoff == 0.0, "nothing was trimmed"
        assert "please buy the radon kit" in messages[0]["content"]
        assert messages[-1]["content"].count("Item 29") == 1, "only the newest snapshot carries its elements"

    def test_the_prompt_tab_shows_the_window_the_request_sends(self):
        section = self._history()
        section.history = self._browse_session(snapshots=3)
        shown = section.render_for_display()
        sent = "\n\n".join(m["content"] for m in section.get_history_messages())
        assert shown.count(SnapshotFormat.ELEMENTS_MARKER) == 1
        assert shown.count("interactive elements omitted") == 2
        for row in sent.split("\n\n"):
            assert row in shown

    def test_one_shot_render_compresses_the_same_way(self):
        entries = self._browse_session(snapshots=3)
        messages = MessageHistory.render_messages(entries, 100_000, MessageHistory.MAX_RENDER_CHARS)
        joined = "\n".join(m["content"] for m in messages)
        assert joined.count(SnapshotFormat.ELEMENTS_MARKER) == 1
        assert joined.count("interactive elements omitted") == 2


class TestSnapshotFormatContract:
    """The compressor parses what WebAccess emits — assembled through the real renderer."""

    def test_real_snapshot_collapses_to_header_only(self):
        from assistant.tools.WebAccess import WebAccess
        wa = WebAccess.__new__(WebAccess)
        records = [{"tag": "button", "label": "Lägg i varukorg", "region": "main"},
                   {"tag": "input", "type": "email", "label": "E-post", "value": "m@mlab.test", "region": "main"}]
        records += [{"tag": "a", "label": f"Nav {i}", "href": f"https://shop.example/n{i}", "region": "nav"} for i in range(7)]
        lines = wa._render_snapshot_lines(records)
        context = WebAccess._format_page_context({"h1": "Villamätning", "headings": ["Pris"], "alerts": [],
                                                  "text": "Mät radon i din villa"})
        older = "result: \n" + WebAccess._assemble_snapshot("NOTE: the popup window closed — you are back on the parent page.\n\n",
                                                          "https://shop.example/p", "Produkt", context, lines,
                                                          ["https://js.pay.example/frame.html"])
        newer = "result: \n" + WebAccess._assemble_snapshot("", "https://shop.example/c", "Checkout", "", ['[0] button "Betala"'], [])
        out = HistoryCompressor().compress([_row("command", older, 1.0), _row("command", newer, 2.0)])
        collapsed = out[0][2]
        assert collapsed == ("result: \nNOTE: the popup window closed — you are back on the parent page.\n\n"
                             "URL: https://shop.example/p\nTitle: Produkt\nPage: h1 \"Villamätning\" | headings: \"Pris\"\n\n"
                             "(9 interactive elements omitted: a later snapshot replaced this one, its indices are stale)")
        assert out[1][2] == newer
