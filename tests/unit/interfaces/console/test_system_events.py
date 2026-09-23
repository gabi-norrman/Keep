"""SystemEvents.markup: messaging rows get the parties in bold and the escaped body, the
channel line dropped; every other row is escaped and left alone."""
from interfaces.console.SystemEvents import markup


def test_received_message_splits_parties_and_body():
    row = "iMessage received:\nFrom Charlotte to Marcus: hej, kommer du?"
    assert markup(row) == "<b>Charlotte → Marcus</b> hej, kommer du?"


def test_media_message_reads_text_first_then_short_then_full_description():
    row = "Telegram sent:\nMarcus to Charlie: Video description: [A shape] A cube rotates.\nText message: nice"
    assert markup(row) == ('<b>Marcus → Charlie</b> nice\n[A shape]\n'
                           '<span class="description">A cube rotates.</span>')


def test_image_description_without_a_text_message():
    row = "iMessage received:\nFrom Charlotte to Marcus: Image description: [A room] A tidy room & a cat."
    assert markup(row) == ('<b>Charlotte → Marcus</b>\n[A room]\n'
                           '<span class="description">A tidy room &amp; a cat.</span>')


def test_media_placeholder_follows_the_text_on_its_own_line():
    assert markup("Telegram sent:\nMarcus to Anna: [Photo]\nText message: look") == (
        "<b>Marcus → Anna</b> look\n[Photo]")
    assert markup("Telegram sent:\nMarcus to Anna: [Video]") == "<b>Marcus → Anna</b>\n[Video]"


def test_sent_without_a_target_shows_the_sender_alone():
    assert markup("iMessage sent:\nMarcus: ok") == "<b>Marcus</b> ok"


def test_body_may_contain_colons_and_to():
    row = "iMessage received:\nFrom Anna to Marcus: note: go to the store: milk"
    assert markup(row) == "<b>Anna → Marcus</b> note: go to the store: milk"


def test_markup_in_a_body_is_escaped_not_rendered():
    assert markup("iMessage received:\nFrom Anna to Marcus: <b>hi</b> & bye") == (
        "<b>Anna → Marcus</b> &lt;b&gt;hi&lt;/b&gt; &amp; bye")


def test_other_rows_pass_through_escaped():
    assert markup("Server started up successfully.") == "Server started up successfully."
    assert markup("Prompt section <TeslaInfo> updated") == "Prompt section &lt;TeslaInfo&gt; updated"
