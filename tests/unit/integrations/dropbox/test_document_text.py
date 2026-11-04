"""DocumentText: what each kind of file extracts to, with a marker line before each PDF page and slide
(the paging and the find over the text are the core's, tested in assistant/integration)."""
import pytest

from integrations.dropbox.DocumentText import Unreadable, extract, rtf_text

from .dropbox_fakes import make_docx, make_pdf, make_pptx, make_rtf


@pytest.mark.unit
class TestExtract:
    def test_utf8_text_is_decoded_and_a_bom_dropped(self):
        assert extract("notes.txt", "﻿Hej världen\n".encode("utf-8")) == "Hej världen\n"

    def test_latin1_text_is_a_fallback(self):
        assert extract("old.txt", "Hej världen".encode("latin-1")) == "Hej världen"

    def test_an_unknown_extension_that_looks_like_text_is_read_as_text(self):
        assert extract("Makefile.custom", b"all:\n\techo hi\n") == "all:\n\techo hi\n"

    def test_a_binary_of_an_unknown_kind_is_refused_as_binary(self):
        with pytest.raises(Unreadable, match="binary"):
            extract("blob.dat", b"\x00\x01\x02\xff" * 10)

    def test_images_and_spreadsheets_are_refused_by_kind(self):
        with pytest.raises(Unreadable, match=r"\.jpg"):
            extract("photo.jpg", b"\xff\xd8\xff")
        with pytest.raises(Unreadable, match=r"\.xlsx"):
            extract("budget.xlsx", b"PK\x03\x04")

    def test_a_pdf_gives_its_pages_in_order_each_under_a_marker(self):
        text = extract("lease.pdf", make_pdf(["The deposit is two months rent", "Signed in Stockholm"]))
        assert text.split("\n\n") == ["[p. 1]\nThe deposit is two months rent", "[p. 2]\nSigned in Stockholm"]

    def test_a_pdf_without_a_text_layer_says_it_is_a_scan(self):
        with pytest.raises(Unreadable, match="no text layer"):
            extract("scan.pdf", make_pdf(["", "", ""]))

    def test_a_broken_pdf_is_unreadable_not_a_crash(self):
        with pytest.raises(Unreadable, match="could not be read"):
            extract("broken.pdf", b"%PDF-1.4 garbage")

    def test_a_docx_gives_its_paragraphs_and_table_rows_with_tabs(self):
        text = extract("minutes.docx", make_docx(["Minutes", "Present: Marcus, Charlotte"], [["Item", "Owner"], ["Roof", "Frances"]]))
        assert text == "Minutes\nPresent: Marcus, Charlotte\nItem\tOwner\nRoof\tFrances"

    def test_a_zip_that_is_not_a_document_is_unreadable(self):
        with pytest.raises(Unreadable):
            extract("odd.docx", b"not a zip at all")

    def test_a_pptx_gives_its_slides_in_number_order_under_markers(self):
        text = extract("deck.pptx", make_pptx([["Plan", "Step one"], ["Step two"]]))
        assert text == "[slide 1]\nPlan\nStep one\n\n[slide 2]\nStep two"

    def test_a_textedit_rtf_is_plain_text_with_the_swedish_letters_decoded(self):
        text = extract("recept.rtf", make_rtf(["Friterad kyckling å la Åsa", "", "1 kg kycklinglår, smör och ägg"]))
        assert text == "Friterad kyckling å la Åsa\n\n1 kg kycklinglår, smör och ägg"
        assert "\\" not in text and "fonttbl" not in text and "Helvetica" not in text

    def test_a_word_rtf_in_another_code_page_decodes_in_that_one(self):
        text = extract("dopis.rtf", make_rtf(["Příliš žluťoučký kůň", "Řádek dva"], code_page=1250, cocoa=False))
        assert text == "Příliš žluťoučký kůň\nŘádek dva"

    def test_an_rtf_is_known_by_its_header_whatever_the_name(self):
        assert extract("TXT.rtf", make_rtf(["Inuti bundeln"])) == "Inuti bundeln"
        assert extract("notes.txt", make_rtf(["Fel ändelse"])) == "Fel ändelse"
        assert extract("plain.rtf", "Ingen RTF alls\n".encode()) == "Ingen RTF alls\n", "a .rtf without the header is text"

    def test_unicode_escapes_fields_and_tabs_in_an_rtf(self):
        data = (b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Calibri;}}\\pard Pris\\tab \\u8364?10\\par "
                b"{\\field{\\*\\fldinst HYPERLINK \"http://x.example\"}{\\fldrslt x.example}}\\par "
                b"\\uc2 \\u229??ngest {\\b fet} \\'e4r\\par}")
        assert rtf_text(data) == "Pris\t€10\nx.example\nångest fet är"

    def test_a_long_utf8_text_with_an_unknown_extension_is_still_text(self):
        # Swedish prose over 8 KB: the 8192-byte head ends inside an "å" for every odd offset.
        text = "a" + "å" * 5000
        assert extract("README", text.encode("utf-8")) == text

    def test_a_word_rtf_unicode_escape_drops_its_hex_fallback_and_a_negative_code_wraps(self):
        # Word writes the fallback as a code-page byte, not a "?"; a code above 32767 is written negative.
        assert rtf_text(b"{\\rtf1\\ansi Sm\\u246\\'f6r}") == "Smör"
        assert rtf_text(b"{\\rtf1\\ansi\\uc2 \\u8364\\'80\\'ac10}") == "€10"
        assert rtf_text(b"{\\rtf1\\ansi \\u-3913?}") == chr(65536 - 3913)

    def test_the_unicode_fallback_count_ends_with_its_group(self):
        assert rtf_text(b"{\\rtf1\\ansi {\\u246}\\'f6x}") == "ööx"

    def test_a_mac_rtf_decodes_in_mac_roman_and_an_unknown_code_page_falls_back_to_cp1252(self):
        assert rtf_text(b"{\\rtf1\\mac \\'8c}") == "å", "0x8c is å in Mac Roman, Œ in cp1252"
        assert rtf_text(b"{\\rtf1\\ansi\\ansicpg99999 \\'e5}") == "å"

    def test_a_word_table_in_an_rtf_is_rows_of_tabbed_cells(self):
        data = b"{\\rtf1\\ansi\\trowd \\intbl Item\\cell Owner\\cell\\row \\intbl Roof\\cell Frances\\cell\\row}"
        assert rtf_text(data) == "Item\tOwner\t\nRoof\tFrances", "a cell ends in a tab, a row in a newline, the end trimmed"

