"""SecretKinds: every written form of a secret normalises to one canonical and is found
by the pattern built from it; nothing matches inside a longer run (VAULT_PLAN §3.2)."""
import re
from datetime import date

import pytest

from common.vault.SecretKinds import MIN_LENGTH, VaultError, normalize, pattern

TODAY = date(2026, 9, 5)


def found(kind, canonical, text, **kw):
    match = re.search(pattern(kind, canonical, **kw), text)
    return match.group(0) if match else None


@pytest.mark.unit
class TestKinds:
    def test_unknown_kind_is_rejected(self):
        with pytest.raises(VaultError, match="unknown secret kind"):
            normalize("ssn", "123456789")
        with pytest.raises(VaultError, match="unknown secret kind"):
            pattern("ssn", "123456789")

    @pytest.mark.parametrize("kind,value", [
        ("password", "12345"), ("api_key", "abc"), ("text", "abcde"),
        ("phone", "12345"), ("account", "AB 12 3"), ("email", "a@b.c"),
    ])
    def test_shorter_than_six_after_normalisation_is_refused(self, kind, value):
        with pytest.raises(VaultError, match=f"too short to vault safely \\(min {MIN_LENGTH}\\)"):
            normalize(kind, value)

    def test_exactly_six_is_allowed(self):
        assert normalize("password", "abcdef") == "abcdef"

    def test_patterns_contain_no_capturing_groups(self):
        """The redactor wraps each pattern in its own named group and relies on lastgroup."""
        for kind, canonical in [("phone", "46701740605"), ("card", "4111111111111111"),
                                ("personal_id", "198502031234"), ("plate", "KEP47L"),
                                ("account", "SE4550000000058398257466"), ("email", "m@mlab.test"),
                                ("password", "p(a)ss"), ("api_key", "sk-(x)"), ("text", "hello(1)")]:
            assert re.compile(pattern(kind, canonical)).groups == 0, kind


@pytest.mark.unit
class TestPhone:
    CANONICAL = "46701740605"

    @pytest.mark.parametrize("typed", [
        "+46 70-174 06 05", "+46701740605", "0701740605", "070 174 06 05", "070-174 06 05",
        "0046701740605", "0046 70 174 06 05", "46701740605", "(070) 174 06 05",
    ])
    def test_every_form_normalises_to_cc_plus_national(self, typed):
        assert normalize("phone", typed) == self.CANONICAL

    def test_default_country_code_is_configurable(self):
        assert normalize("phone", "0701740605", country_code=47) == "47701740605"

    @pytest.mark.parametrize("text", [
        "ring +46 70-174 06 05 nu", "ring 0701740605 nu", "070 174 06 05", "0046701740605",
        "46701740605", "+46701740605", "tel: +46 (70) 174 06 05.", "070.174.06.05",
    ])
    def test_every_form_is_found_in_text(self, text):
        assert found("phone", self.CANONICAL, text) is not None

    def test_match_covers_the_whole_written_number(self):
        assert found("phone", self.CANONICAL, "ring +46 70-174 06 05 nu") == "+46 70-174 06 05"
        assert found("phone", self.CANONICAL, "ring 0701740605 nu") == "0701740605"

    @pytest.mark.parametrize("text", [
        "946701740605", "467017406051", "10701740605", "07017406050", "12345046701740605",
    ])
    def test_never_inside_a_longer_digit_run(self, text):
        assert found("phone", self.CANONICAL, text) is None

    def test_foreign_number_has_no_local_zero_form(self):
        canonical = normalize("phone", "+47 12 34 56 78")
        assert canonical == "4712345678"
        assert found("phone", canonical, "+47 12 34 56 78") == "+47 12 34 56 78"
        assert found("phone", canonical, "004712345678") == "004712345678"
        assert found("phone", canonical, "4712345678") == "4712345678"
        assert found("phone", canonical, "012345678") is None


@pytest.mark.unit
class TestCard:
    CANONICAL = "4111111111111111"

    @pytest.mark.parametrize("typed", ["4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111"])
    def test_digits_only(self, typed):
        assert normalize("card", typed) == self.CANONICAL

    @pytest.mark.parametrize("typed", ["4111 1111 1111", "41111111111111111111"])
    def test_outside_13_to_19_digits_is_refused(self, typed):
        with pytest.raises(VaultError, match="13-19 digits"):
            normalize("card", typed)

    @pytest.mark.parametrize("text", [
        "card 4111 1111 1111 1111 exp", "4111-1111-1111-1111", "4111111111111111", "4111.1111.1111.1111",
    ])
    def test_separators_between_groups(self, text):
        assert found("card", self.CANONICAL, text) is not None

    def test_expiry_and_cvv_are_not_part_of_the_match(self):
        text = "4111 1111 1111 1111 12/29 123"
        assert found("card", self.CANONICAL, text) == "4111 1111 1111 1111"

    @pytest.mark.parametrize("text", ["14111111111111111", "41111111111111110"])
    def test_never_inside_a_longer_digit_run(self, text):
        assert found("card", self.CANONICAL, text) is None


@pytest.mark.unit
class TestPersonalId:
    CANONICAL = "198502031234"

    @pytest.mark.parametrize("typed", ["19850203-1234", "198502031234", "19850203 1234"])
    def test_twelve_digits_pass_through(self, typed):
        assert normalize("personal_id", typed, today=TODAY) == self.CANONICAL

    def test_ten_digits_get_the_century_from_the_age_rule(self):
        assert normalize("personal_id", "850203-1234", today=TODAY) == "198502031234"
        assert normalize("personal_id", "200101-1234", today=TODAY) == "202001011234"
        assert normalize("personal_id", "260905-1234", today=TODAY) == "202609051234"
        assert normalize("personal_id", "260906-1234", today=TODAY) == "192609061234"

    def test_plus_separator_means_a_hundred_years_older(self):
        assert normalize("personal_id", "850203+1234", today=TODAY) == "188502031234"

    @pytest.mark.parametrize("typed", ["85020312", "1985020312345"])
    def test_other_lengths_are_refused(self, typed):
        with pytest.raises(VaultError, match="10 or 12 digits"):
            normalize("personal_id", typed)

    @pytest.mark.parametrize("text", [
        "pnr 19850203-1234.", "198502031234", "19850203 1234", "850203-1234", "850203+1234",
        "8502031234", "850203 1234", "1985 0203 1234",
    ])
    def test_ten_and_twelve_digit_forms_are_found(self, text):
        assert found("personal_id", self.CANONICAL, text) is not None

    @pytest.mark.parametrize("text", ["1198502031234", "198502031234 5".replace(" ", ""), "0850203-1234"])
    def test_never_inside_a_longer_digit_run(self, text):
        assert found("personal_id", self.CANONICAL, text) is None


@pytest.mark.unit
class TestPlate:
    CANONICAL = "KEP47L"

    @pytest.mark.parametrize("typed", ["KEP 47L", "kep47l", "KEP-47L", "kep 47l"])
    def test_uppercase_alnum(self, typed):
        assert normalize("plate", typed) == self.CANONICAL

    @pytest.mark.parametrize("typed", ["KEP47", "KEP47LYZ"])
    def test_outside_6_to_7_chars_is_refused(self, typed):
        with pytest.raises(VaultError, match="6-7 letters/digits"):
            normalize("plate", typed)

    @pytest.mark.parametrize("text", ["the car KEP 47L is", "kep47l", "Kep-47l", "K E P 4 7 L"])
    def test_case_insensitive_with_optional_separators(self, text):
        assert found("plate", self.CANONICAL, text) is not None

    @pytest.mark.parametrize("text", ["AKEP47L", "KEP47L1", "kep47la"])
    def test_not_inside_a_longer_alnum_run(self, text):
        assert found("plate", self.CANONICAL, text) is None


@pytest.mark.unit
class TestAccount:
    CANONICAL = "SE4550000000058398257466"

    @pytest.mark.parametrize("typed", ["SE45 5000 0000 0583 9825 7466", "se4550000000058398257466", "SE45-5000-0000-0583-9825-7466"])
    def test_alnum_only_uppercased(self, typed):
        assert normalize("account", typed) == self.CANONICAL

    @pytest.mark.parametrize("text", [
        "IBAN SE45 5000 0000 0583 9825 7466 BIC", "SE4550000000058398257466", "se45 5000 0000 0583 9825 7466",
    ])
    def test_separators_between_chars(self, text):
        assert found("account", self.CANONICAL, text) is not None

    def test_swedish_clearing_and_account_with_comma(self):
        canonical = normalize("account", "8327-9,123 456 789-0")
        assert canonical == "83279123456789" + "0"
        assert found("account", canonical, "konto 8327-9,123 456 789-0 tack") == "8327-9,123 456 789-0"

    def test_not_inside_a_longer_run(self):
        assert found("account", self.CANONICAL, "XSE4550000000058398257466") is None
        assert found("account", self.CANONICAL, "SE45500000000583982574661") is None


@pytest.mark.unit
class TestEmail:
    CANONICAL = "m@mlab.test"

    def test_lowercased(self):
        assert normalize("email", "M@MLab.test") == self.CANONICAL
        assert normalize("email", "  m@mlab.test ") == self.CANONICAL

    @pytest.mark.parametrize("typed", ["mlab.test", "@mlab.test", "m@", "m @mlab.test"])
    def test_not_an_address_is_refused(self, typed):
        with pytest.raises(VaultError, match="not an e-mail address"):
            normalize("email", typed)

    @pytest.mark.parametrize("text", ["mail M@MLAB.TEST now", "<m@mlab.test>", "m@mlab.test.", "(m@mlab.test)"])
    def test_case_insensitive(self, text):
        assert found("email", self.CANONICAL, text) is not None

    @pytest.mark.parametrize("text", ["am@mlab.test", "m@mlab.test.com", "m@mlab.testa", "x.m@mlab.test", "m@mlab.test-x"])
    def test_boundaries(self, text):
        assert found("email", self.CANONICAL, text) is None


@pytest.mark.unit
class TestVerbatimKinds:
    @pytest.mark.parametrize("kind", ["password", "api_key", "text"])
    def test_exact_case_sensitive_match_with_regex_chars_escaped(self, kind):
        assert found(kind, "P@$$w0rd.", "use P@$$w0rd. here") == "P@$$w0rd."
        assert found(kind, "P@$$w0rd.", "use p@$$w0rd. here") is None
        assert found(kind, "P@$$w0rd.", "use P@$$w0rdX here") is None
