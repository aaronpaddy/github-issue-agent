import pytest

from expense_splitter import Transfer, settle_up


def test_balances_that_sum_above_zero_raise_value_error():
    with pytest.raises(ValueError):
        settle_up({"ann": 100, "bob": -40})


def test_balances_that_sum_below_zero_raise_value_error():
    with pytest.raises(ValueError):
        settle_up({"ann": 40, "bob": -100})


def test_balanced_input_is_unchanged():
    assert settle_up({"ann": 100, "bob": -100}) == [Transfer("bob", "ann", 100)]


def test_empty_and_all_zero_input_are_fine():
    assert settle_up({}) == []
    assert settle_up({"ann": 0, "bob": 0}) == []
