from backend.services.option_symbol_service import _apply_straddle_width


def test_straddle_width_selects_closest_positive_strike():
    strikes = [39600, 39800, 40000, 40200, 40400]

    assert _apply_straddle_width(40000, 495, 0.5, strikes) == 40200


def test_straddle_width_selects_closest_negative_strike():
    strikes = [39600, 39800, 40000, 40200, 40400]

    assert _apply_straddle_width(40000, 495, -0.5, strikes) == 39800
