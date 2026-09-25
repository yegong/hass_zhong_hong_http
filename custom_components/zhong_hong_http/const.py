"""Constants for the Zhonghong HTTP integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "zhong_hong_http"
MANUFACTURER: Final = "Zhonghong"
MODEL_GATEWAY: Final = "VRF Admin Panel"
MODEL_INDOOR_UNIT: Final = "VRF Indoor Unit"

DEFAULT_USERNAME: Final = "admin"
DEFAULT_PASSWORD: Final = ""
DEFAULT_PORT: Final = 80
CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 10
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 15
GATEWAY_INFO_INTERVAL: Final = timedelta(minutes=5)

MAX_PAGES: Final = 128
MAX_RESPONSE_BYTES: Final = 1024 * 1024
REQUEST_TIMEOUT: Final = 10.0
QUERY_TIMEOUT: Final = 60.0

MODE_COOL: Final = 1
MODE_DRY: Final = 2
MODE_FAN_ONLY: Final = 4
MODE_HEAT: Final = 8
SUPPORTED_MODES: Final = frozenset({MODE_COOL, MODE_DRY, MODE_FAN_ONLY, MODE_HEAT})

FAN_HIGH: Final = 1
FAN_MEDIUM: Final = 2
FAN_LOW: Final = 4
SUPPORTED_FAN_SPEEDS: Final = frozenset({FAN_HIGH, FAN_MEDIUM, FAN_LOW})
