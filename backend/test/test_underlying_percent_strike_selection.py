from backend.services.option_symbol_service import _underlying_percent_premium


def test_underlying_percent_calculates_one_percent_target_premium():
    assert round(_underlying_percent_premium(24622.15, 1), 2) == 246.22


def test_underlying_percent_calculates_half_percent_target_premium():
    assert round(_underlying_percent_premium(24622.15, 0.5), 2) == 123.11
