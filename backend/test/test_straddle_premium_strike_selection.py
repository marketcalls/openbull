import pytest
from pydantic import ValidationError

from backend.schemas.strategy_module import Leg
from backend.services.option_symbol_service import _straddle_premium_target


def test_straddle_premium_target_uses_percent_of_atm_straddle():
    assert _straddle_premium_target(495, 50) == 247.5


def test_straddle_premium_leg_accepts_match_mode_and_percent():
    leg = Leg(
        id=1,
        segment="options",
        expiry="weekly",
        lots=1,
        position="S",
        option_type="CE",
        strike_mode="straddle_premium",
        straddle_premium_value=50,
        straddle_premium_match="premium_near",
    )

    assert leg.straddle_premium_value == 50
    assert leg.straddle_premium_match == "premium_near"


def test_straddle_premium_leg_requires_match_mode():
    with pytest.raises(ValidationError):
        Leg(
            id=1,
            segment="options",
            expiry="weekly",
            lots=1,
            position="S",
            option_type="CE",
            strike_mode="straddle_premium",
            straddle_premium_value=50,
        )
