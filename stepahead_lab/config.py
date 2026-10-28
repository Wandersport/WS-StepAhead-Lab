"""Deliberately contains no trading endpoints, keys, or order methods."""
from decimal import Decimal

TARGET_WALLET = "0xe29042f5d913dcc4015aab3455c13c58514ca33f"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BTC_SERIES_SLUG = "btc-up-or-down-5m"
BTC_MARKET_PREFIX = "btc-updown-5m-"
CRYPTO_FEE_RATE = Decimal("0.07")
# WARNING: rebates are deliberately omitted from decisions: actual allocation is unknowable.
