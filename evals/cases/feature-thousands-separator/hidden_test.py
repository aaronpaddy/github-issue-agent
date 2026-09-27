from expense_splitter import format_cents


def test_thousands_are_grouped():
    assert format_cents(123456789) == "$1,234,567.89"
    assert format_cents(100000) == "$1,000.00"


def test_negative_amounts_are_grouped_too():
    assert format_cents(-123456) == "-$1,234.56"


def test_amounts_under_a_thousand_are_unchanged():
    assert format_cents(1250) == "$12.50"
    assert format_cents(5) == "$0.05"
    assert format_cents(99999) == "$999.99"
    assert format_cents(0) == "$0.00"
