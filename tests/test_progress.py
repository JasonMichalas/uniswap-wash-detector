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
    )
    assert line == (
        "Reading swaps · block 50 of 100 (50%) · "
        "3 swaps, 4 transfers found · "
        "12s elapsed · "
        "10 blocks from network, 90 cached"
    )


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
