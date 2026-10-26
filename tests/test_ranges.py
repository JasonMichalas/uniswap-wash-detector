from washdetect.ranges import covers, merge_intervals, missing_intervals


def test_merge_touches_and_overlaps():
    assert merge_intervals([(5, 10), (0, 4), (12, 12)]) == [(0, 10), (12, 12)]
    assert merge_intervals([(0, 5), (3, 8)]) == [(0, 8)]


def test_missing_intervals():
    assert missing_intervals([], 0, 100) == [(0, 100)]
    assert missing_intervals([(0, 50)], 0, 100) == [(51, 100)]
    assert missing_intervals([(40, 100)], 0, 100) == [(0, 39)]
    assert missing_intervals([(0, 100)], 20, 40) == []
    assert missing_intervals([(0, 20), (80, 100)], 0, 100) == [(21, 79)]
    assert missing_intervals([(0, 20), (21, 40)], 0, 40) == []
    assert covers([(10, 20)], 10, 20)
    assert not covers([(10, 20)], 10, 21)
