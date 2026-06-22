from backend.services.option_symbol_service import _apply_atm_percent


def test_atm_percent_selects_closest_positive_call_strike():
    strikes = [41700, 41800, 42100, 42200, 42400, 42600, 43000]

    assert _apply_atm_percent(42400, 0.5, strikes) == 42600


def test_atm_percent_selects_closest_positive_put_strike():
    strikes = [41700, 41800, 42100, 42200, 42400, 42600, 43000]

    assert _apply_atm_percent(42400, 1.5, strikes) == 43000


def test_atm_percent_selects_closest_negative_call_strike():
    strikes = [41700, 41800, 42100, 42200, 42400, 42600, 43000]

    assert _apply_atm_percent(42400, -0.5, strikes) == 42200


def test_atm_percent_selects_closest_negative_put_strike():
    strikes = [41700, 41800, 42100, 42200, 42400, 42600, 43000]

    assert _apply_atm_percent(42400, -1.5, strikes) == 41800
