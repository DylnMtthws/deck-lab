import pytest

from sabermetrics.assistant_store import validate_constraints
from sabermetrics.swap_evaluation import OBJECTIVES


@pytest.mark.parametrize("objective", ["", *OBJECTIVES])
def test_objective_preserved(objective):
    assert validate_constraints({"objective": objective})["objective"] == objective


@pytest.mark.parametrize(
    "objective", ["win_rate", 123, False, [], ["qualified_opening_rate"]]
)
def test_unknown_objective_rejected(objective):
    with pytest.raises(ValueError):
        validate_constraints({"objective": objective})
