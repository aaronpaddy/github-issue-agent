import pytest

from expense_splitter import split_by_weights


def test_all_zero_weights_raise_value_error():
    with pytest.raises(ValueError):
        split_by_weights(100, {"ann": 0, "bob": 0})


def test_empty_weights_raise_value_error():
    with pytest.raises(ValueError):
        split_by_weights(100, {})


def test_normal_input_is_unchanged():
    assert split_by_weights(1000, {"ann": 1, "bob": 3}) == {"ann": 250, "bob": 750}


def test_leftover_cents_are_still_distributed():
    assert sum(split_by_weights(100, {"ann": 1, "bob": 1, "cy": 1}).values()) == 100


def test_a_zero_weight_member_among_positive_weights_gets_nothing():
    assert split_by_weights(100, {"ann": 0, "bob": 1}) == {"ann": 0, "bob": 100}
