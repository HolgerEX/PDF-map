"""Shared constants for the application."""

PAPER_SIZES_MM = {
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
    "A5": (210.0, 148.0),
    "A6": (148.0, 105.0),
    "Letter": (279.0, 216.0),
}

TILE_PROVIDERS = {
    "OpenStreetMap (default)": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "CyclOSM (bicycle)": "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
    "OpenCycleMap": "https://{s}.tile.opencyclemap.org/cycle/{z}/{x}/{y}.png",
}

MAP_SCALES = {
    "1:5,000 (City)": 5_000,
    "1:10,000 (Town)": 10_000,
    "1:25,000 (Regional)": 25_000,
    "1:50,000 (Country)": 50_000,
    "1:100,000 (Long distance)": 100_000,
    "1:500,000 (Very Long distance)": 500_000,
}

DEFAULT_SCALE = 25_000
DEFAULT_PAPER_SIZE = "A4"
DEFAULT_OVERLAP_M = 500.0
DEFAULT_DPI = 300
