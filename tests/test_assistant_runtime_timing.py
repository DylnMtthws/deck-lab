from sabermetrics.assistant_runtime_timing import timing_coverage


def test_nested_and_overlapping_intervals_do_not_double_count_wall_time():
    receipt = timing_coverage(
        12,
        [
            {"start_seconds": 1, "end_seconds": 9},
            {"start_seconds": 2, "end_seconds": 6},
            {"start_seconds": 8, "end_seconds": 11},
            {"elapsed_seconds": 5},
        ],
    )
    assert receipt["covered_wall_seconds"] == 10
    assert receipt["unattributed_wall_seconds"] == 2
    assert receipt["overlapping_interval_seconds"] == 5
    assert receipt["rows_without_intervals"] == 1


def test_missing_and_invalid_intervals_remain_unattributed():
    receipt = timing_coverage(10, [{"start_seconds": 4, "end_seconds": 2}])
    assert receipt["covered_wall_seconds"] == 0
    assert receipt["unattributed_wall_seconds"] == 10
