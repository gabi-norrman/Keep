"""StoredReplyReader: a stored assistant reply read back as its stream showed it — the
prose without the command calls, an echo per call."""
import pytest

from assistant.commands.StoredReplyReader import StoredReplyReader


@pytest.fixture(scope="module")
def reader():
    return StoredReplyReader()


def test_a_reply_without_commands_is_all_prose(reader):
    reply = reader.read("Parked at <b>Kyrkogatan</b>, spot <code>B4</code>.")
    assert (reply.prose, reply.before, reply.after) == ("Parked at <b>Kyrkogatan</b>, spot <code>B4</code>.", [], [])


def test_a_block_quoted_argument_is_taken_out_whole(reader):
    """The text of a save_watch — parentheses, quotes and line breaks in it — is the LLM's, not the user's."""
    reply = reader.read('save_watch("ferry-strike", """Strike (day 2), "no boats"\nuntil further notice.""", '
                        '"2026-10-07", "6h")\n\nOn the watch list.')
    assert reply.prose == "On the watch list."
    assert len(reply.before) == 1 and "ferry-strike" in reply.before[0] and "no boats" not in reply.before[0]


def test_echoes_keep_their_place_around_the_prose(reader):
    reply = reader.read('google("ferry strike")\nBack on Friday.\n\nremember(Marcus follows the ferry strike)')
    assert reply.prose == "Back on Friday."
    assert len(reply.before) == 1 and "ferry strike" in reply.before[0]
    assert len(reply.after) == 1


@pytest.mark.parametrize("wrapped", ['`google("ferry strike")`', '<code>google("ferry strike")</code>',
                                     '```\ngoogle("ferry strike")\n```'])
def test_the_wrapping_of_a_call_goes_with_it(reader, wrapped):
    reply = reader.read(f"Looking.\n{wrapped}\nDone.")
    assert reply.prose == "Looking.\n\nDone." and len(reply.after) == 1


def test_code_tags_in_the_prose_after_a_call_survive(reader):
    assert reader.read('google("ferry strike")\nSpot <code>B4</code>.').prose == "Spot <code>B4</code>."


def test_a_call_that_never_closes_stays_prose(reader):
    reply = reader.read("I could run google( but will not")
    assert reply.prose == "I could run google( but will not" and not reply.before and not reply.after

