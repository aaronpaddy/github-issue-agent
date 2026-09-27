from expense_splitter import format_cents


def _format(cents, code):
    try:
        return format_cents(cents, code)
    except TypeError:
        return format_cents(cents, currency=code)


def test_euro_symbol():
    assert _format(1250, "EUR") == "\u20ac12.50"


def test_pound_symbol():
    assert _format(1250, "GBP") == "\u00a312.50"


def test_dollars_still_work_by_default_and_explicitly():
    assert format_cents(1250) == "$12.50"
    assert _format(1250, "USD") == "$12.50"
