from decimal import Decimal

import pytest

from expense_splitter import to_cents


@pytest.mark.parametrize("bad", ["abc", "", "$12.50", "NaN", "Infinity", "-Infinity", "1,250.00", "12.5.0"])
def test_invalid_input_raises_value_error(bad):
    with pytest.raises(ValueError):
        to_cents(bad)


def test_the_message_names_the_offending_input():
    with pytest.raises(ValueError) as error:
        to_cents("abc")
    assert "abc" in str(error.value)


def test_valid_input_is_unchanged():
    assert to_cents("12.50") == 1250
    assert to_cents("-3") == -300
    assert to_cents(Decimal("0.99")) == 99
    assert to_cents("0.005") == 1
