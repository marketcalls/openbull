from sqlalchemy import Column, Integer, String, Text

from backend.database import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)


# ---- Well-known keys -------------------------------------------------------

# Trading mode: single source of truth for "is the whole instance currently
# dispatching to live broker APIs or to the sandbox execution engine?".
# Values: "live" | "sandbox". Default assumed "live" when the row is absent.
TRADING_MODE_KEY = "trading_mode"
TRADING_MODE_LIVE = "live"
TRADING_MODE_SANDBOX = "sandbox"
VALID_TRADING_MODES = (TRADING_MODE_LIVE, TRADING_MODE_SANDBOX)
