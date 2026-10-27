from washdetect.pipeline import format_load_line


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
