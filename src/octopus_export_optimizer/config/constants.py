"""Fixed constants for the Octopus Export Optimizer."""

from datetime import timedelta

# Half-hour settlement interval
SLOT_DURATION = timedelta(minutes=30)
SLOT_DURATION_HOURS = 0.5

# Octopus Energy API
OCTOPUS_API_BASE = "https://api.octopus.energy/v1"

# Default location (UK)
DEFAULT_LATITUDE = 52.0
DEFAULT_LONGITUDE = -1.0

# Solar orientation constants (degrees from north, clockwise)
NORTH = 0
SOUTH = 180
EAST = 90
WEST = 270
