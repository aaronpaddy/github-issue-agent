from expense_splitter import Transfer, format_transfers, settle_up


def test_renders_one_readable_line_per_transfer():
    transfers = [Transfer("cy", "ann", 4000), Transfer("bob", "ann", 1000)]
    assert format_transfers(transfers) == ["cy pays ann $40.00", "bob pays ann $10.00"]


def test_empty_input_gives_an_empty_list():
    assert format_transfers([]) == []


def test_works_directly_on_settle_up_output():
    net = {"ann": 5000, "bob": -1000, "cy": -4000}
    assert format_transfers(settle_up(net)) == ["cy pays ann $40.00", "bob pays ann $10.00"]


def test_small_amounts_keep_their_leading_zero():
    assert format_transfers([Transfer("a", "b", 5)]) == ["a pays b $0.05"]
