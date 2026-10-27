from washdetect.pipeline import format_load_line, render_progress


def test_load_line_names_phase_blocks_counts_and_cache():
    line = format_load_line(
        phase="Reading swaps",
        at=50,
        end=100,
        swaps=3,
        transfers=4,
        elapsed=12.2,
        network=10,
        cached=90,
        swaps_started=True,
        transfers_started=True,
    )
    assert line == (
        "Reading swaps · block 50 of 100 (50%) · "
        "3 swaps found · 4 transfers found · "
        "12s elapsed · "
        "10 blocks from network, 90 cached"
    )


def test_steps_that_have_not_run_are_not_shown_as_zero():
    stats = {"at": 100, "end": 200, "swaps": 0, "transfers": 0}
    line = render_progress(
        "Finding pools · blocks 100 of 200",
        stats,
        elapsed=3,
        network=0,
        cached=0,
    )
    assert "swaps not started yet" in line
    assert "transfers not started yet" in line
    assert "0 swaps" not in line
    assert "0 transfers" not in line
    reading = render_progress(
        "Reading swaps: blocks 150 of 200",
        stats,
        elapsed=4,
        network=1,
        cached=0,
    )
    assert "0 swaps found" in reading
    assert "transfers not started yet" in reading


def test_a_phase_that_already_has_a_percentage_keeps_only_one():
    stats = {"at": 26_115_260, "end": 26_129_614, "swaps": 0, "transfers": 0}
    line = render_progress(
        "Indexing Uniswap v4 pools: block 21,712,328 of 26,129,614 (0%)",
        stats,
        elapsed=4,
        network=0,
        cached=0,
    )
    assert line.count("%") == 1
    assert "Indexing Uniswap v4 pools" in line
    assert "21,712,328" in line
    assert "26,115,260" not in line
    assert "(0%)" not in line


def test_a_note_without_a_block_range_has_no_percentage():
    stats = {"at": 0, "end": 0, "swaps": 0, "transfers": 0}
    line = render_progress("Resolving the block window", stats, elapsed=0, network=0, cached=0)
    assert "block 0 of 0" not in line
    assert "%" not in line
    assert line.startswith("Resolving the block window")
