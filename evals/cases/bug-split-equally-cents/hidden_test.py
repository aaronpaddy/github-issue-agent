from expense_splitter import Expense, balances, split_equally


def test_uneven_split_assigns_every_cent():
    assert split_equally(100, ["ann", "bob", "cy"]) == {"ann": 34, "bob": 33, "cy": 33}


def test_shares_always_sum_to_the_total_and_differ_by_at_most_a_cent():
    for total in range(0, 60):
        for size in range(1, 8):
            shares = split_equally(total, [f"p{i}" for i in range(size)])
            assert sum(shares.values()) == total
            assert max(shares.values()) - min(shares.values()) <= 1


def test_leftover_cents_go_to_the_first_people_in_order():
    assert split_equally(10, ["a", "b", "c", "d"]) == {"a": 3, "b": 3, "c": 2, "d": 2}


def test_balances_sum_to_zero():
    net = balances([Expense("ann", 100, ("ann", "bob", "cy"))])
    assert sum(net.values()) == 0
