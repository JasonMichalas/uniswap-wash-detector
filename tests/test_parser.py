import pytest

from washdetect.querylang.errors import QueryError
from washdetect.querylang.parser import parse


def test_short_queries_lower_to_programs():
    percent = parse("wash_percent")
    assert "detect_cycles" in percent.to_text()
    assert "ratio" in percent.to_text()
    assert "format percent" in percent.to_text()

    wallets = parse("wash_wallets where confidence > 0.8")
    assert "confidence > 0.8" in wallets.to_text()

    holders = parse("top_holders limit 20")
    assert "sort h by balance desc" in holders.to_text()
    assert "limit s 20" in holders.to_text()

    long_term = parse("holders where held_days > 90")
    assert "held_days > 90" in long_term.to_text()

    quiet = parse("wallets where not sold_in(30d)")
    assert "not sold_in(30d)" in quiet.to_text()

    cycles = parse("cycles where size >= 3")
    assert "size >= 3" in cycles.to_text()
    assert "project d cycles" in cycles.to_text()


def test_program_round_trip():
    text = """
    n_swaps = source swaps
    n_filtered = filter n_swaps where side = "buy"
    output table n_filtered as "Buys"
    """
    again = parse(parse(text).to_text())
    assert again.to_text() == parse(text).to_text()


def test_bad_queries_explain_themselves():
    with pytest.raises(QueryError) as empty:
        parse("   ")
    assert "empty" in str(empty.value).lower()

    with pytest.raises(QueryError) as dropped:
        parse("drop table swaps")
    assert "Could not parse" in str(dropped.value)
    assert "line 1" in str(dropped.value)

    with pytest.raises(QueryError) as decorated:
        parse("wash_percent where confidence > 0.8")
    assert "wash_percent" in str(decorated.value)


def test_detect_cycles_accepts_tolerance_window_and_group_size():
    program = parse("s = source swaps\nt = source transfers\nd = detect_cycles(s, t, 0.05, 200, 12)\noutput table d as \"x\"\n")
    text = program.to_text()
    assert "detect_cycles(s, t, 0.05, 200, 12)" in text
    assert parse(text).to_text() == text


def test_comments_and_hours():
    program = parse("wallets where not sold_in(12h) # quiet wallets\n")
    assert "sold_in(12h)" in program.to_text()
