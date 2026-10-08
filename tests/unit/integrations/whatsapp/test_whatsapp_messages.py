from integrations.whatsapp import WhatsAppMessages as msgs
from integrations.whatsapp.WhatsAppMessages import Incoming
from . import fakes
from .fakes import ALICE, BOB, GROUP, OWNER


class Names:
    def person(self, jid, push_name):
        return {ALICE: "Alice", BOB: "Bob"}.get(msgs.jid_parts(jid)[0]) or push_name or f"+{msgs.jid_parts(jid)[0]}"

    def group(self, jid):
        return "Family"


def incoming(message, chat=ALICE, sender=ALICE, from_me=False, push_name="", server="s.whatsapp.net"):
    return Incoming(timestamp=1_700_000_000.0, chat=f"{chat}@{server}", sender=f"{sender}@s.whatsapp.net",
                    from_me=from_me, push_name=push_name, message=message, from_history=False)


class TestJids:
    def test_device_and_agent_suffixes_are_dropped(self):
        assert msgs.jid_parts(f"{ALICE}:12@s.whatsapp.net") == (ALICE, "s.whatsapp.net")
        assert msgs.jid_parts(f"{ALICE}.0:3@s.whatsapp.net")[0] == ALICE

    def test_status_updates_and_channels_are_not_conversations(self):
        assert msgs.is_skipped_chat("status@broadcast")
        assert msgs.is_skipped_chat("120363@newsletter")
        assert not msgs.is_skipped_chat(f"{GROUP}@g.us") and not msgs.is_skipped_chat(f"{ALICE}@s.whatsapp.net")

    def test_a_phone_number_jid_gives_the_number_a_hidden_one_does_not(self):
        assert msgs.phone_of(f"{ALICE}@s.whatsapp.net") == f"+{ALICE}"
        assert msgs.phone_of("98765@lid") is None and msgs.phone_of(f"{GROUP}@g.us") is None


class TestContent:
    def test_plain_and_extended_text(self):
        assert msgs.content_of(fakes.text_message("hi")).text == "hi"
        assert msgs.content_of(fakes.extended_message("a link https://x.test")).text == "a link https://x.test"

    def test_a_photo_carries_its_caption_and_media(self):
        content = msgs.content_of(fakes.image_message("look", "image/png"))
        assert content.text == "look" and content.media.kind == msgs.KIND_IMAGE and content.media.mimetype == "image/png"

    def test_a_video_is_media_too(self):
        assert msgs.content_of(fakes.video_message()).media.kind == msgs.KIND_VIDEO

    def test_a_voice_note_and_a_document_are_placeholders_with_their_facts(self):
        assert "Voice note" in msgs.content_of(fakes.audio_message(7, ptt=True)).text
        assert "7 s" in msgs.content_of(fakes.audio_message(7, ptt=True)).text
        assert "Audio" in msgs.content_of(fakes.audio_message(0, ptt=False)).text
        assert "invoice.pdf" in msgs.content_of(fakes.document_message("invoice.pdf")).text

    def test_a_protocol_message_has_no_content(self):
        assert msgs.content_of(fakes.protocol_message()).empty

    def test_a_disappearing_message_is_unwrapped(self):
        assert msgs.content_of(fakes.ephemeral(fakes.text_message("gone soon"))).text == "gone soon"


class TestFormat:
    def test_a_direct_message_received(self):
        line = msgs.format_entry(incoming(fakes.text_message("lunch?")), Names(), "Marcus", msgs.content_of(fakes.text_message("lunch?")))
        assert line == "WhatsApp received:\nFrom Alice to Marcus: lunch?"

    def test_a_group_message_names_the_group_as_the_target(self):
        inc = incoming(fakes.text_message("hi all"), chat=GROUP, sender=BOB, server="g.us")
        line = msgs.format_entry(inc, Names(), "Marcus", msgs.content_of(inc.message))
        assert line == "WhatsApp received:\nFrom Bob to Family: hi all"

    def test_an_unknown_sender_falls_back_to_the_push_name_then_the_number(self):
        inc = incoming(fakes.text_message("yo"), chat="46701740699", sender="46701740699", push_name="Zed")
        assert "From Zed to Marcus" in msgs.format_entry(inc, Names(), "Marcus", msgs.content_of(inc.message))
        inc = incoming(fakes.text_message("yo"), chat="46701740699", sender="46701740699")
        assert "From +46701740699 to Marcus" in msgs.format_entry(inc, Names(), "Marcus", msgs.content_of(inc.message))

    def test_an_own_message_names_the_recipient_or_the_group(self):
        inc = incoming(fakes.text_message("on my way"), from_me=True, sender=OWNER)
        assert msgs.format_entry(inc, Names(), "Marcus", msgs.content_of(inc.message)) == "WhatsApp sent:\nMarcus to Alice: on my way"
        inc = incoming(fakes.text_message("dinner at 7"), chat=GROUP, sender=OWNER, from_me=True, server="g.us")
        assert msgs.format_entry(inc, Names(), "Marcus", msgs.content_of(inc.message)) == "WhatsApp sent:\nMarcus to Family: dinner at 7"

    def test_a_note_to_self_says_so(self):
        inc = incoming(fakes.text_message("buy milk"), chat=OWNER, sender=OWNER, from_me=True)
        assert "Marcus to Marcus (note to self): buy milk" in msgs.format_entry(inc, Names(), "Marcus", msgs.content_of(inc.message))

    def test_a_described_photo_with_a_caption_keeps_both(self):
        inc = incoming(fakes.image_message("our cat"))
        line = msgs.format_entry(inc, Names(), "Marcus", msgs.content_of(inc.message), media_text="A grey cat on a sofa")
        assert line.endswith("A grey cat on a sofa\nText message: our cat")
