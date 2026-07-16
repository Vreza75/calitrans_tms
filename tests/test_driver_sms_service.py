from services.driver_sms_service import format_phone_e164


def test_plain_ten_digit_number():
    assert format_phone_e164("8325552020") == "+18325552020"


def test_dashes_formatting():
    assert format_phone_e164("832-555-2020") == "+18325552020"


def test_parens_and_spaces_formatting():
    assert format_phone_e164("(832) 555-2020") == "+18325552020"


def test_already_e164():
    assert format_phone_e164("+18325552020") == "+18325552020"


def test_eleven_digit_with_leading_one():
    assert format_phone_e164("18325552020") == "+18325552020"


def test_too_short_returns_none():
    assert format_phone_e164("5552020") is None


def test_too_long_returns_none():
    assert format_phone_e164("183255520201234") is None


def test_blank_returns_none():
    assert format_phone_e164("") is None


def test_none_returns_none():
    assert format_phone_e164(None) is None


def test_non_numeric_junk_returns_none():
    assert format_phone_e164("no phone on file") is None


def test_non_ascii_digits_rejected():
    # Fullwidth digits (U+FF10-U+FF19) pass str.isdigit() but are not ASCII 0-9.
    # "１２３４５６７８９０" is exactly 10 fullwidth digit characters.
    # Old code would extract all 10 (isdigit()→True) and return "+1１２３４５６７８９０"
    # New code extracts only ASCII 0-9, finds "", length 0, returns None.
    assert format_phone_e164("１２３４５６７８９０") is None
