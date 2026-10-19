import pytest

from app.risk_manager import allow_new_position, validate_risk_parameters


def test_only_one_open_position_allowed_by_default():
    assert allow_new_position(0)
    assert not allow_new_position(1)


def test_invalid_position_counts_fail_closed():
    assert not allow_new_position(-1)
    assert not allow_new_position(0, 0)


@pytest.mark.parametrize("sl,tp", [(0, 2), (-1, 2), (1, 0), (1, 100), (100, 2)])
def test_invalid_risk_percentages_rejected(sl, tp):
    with pytest.raises(ValueError):
        validate_risk_parameters(sl, tp)


def test_valid_risk_percentages():
    validate_risk_parameters(1, 2)
