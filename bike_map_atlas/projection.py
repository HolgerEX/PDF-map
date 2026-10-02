"""Coordinate transformation and projection utilities."""

from __future__ import annotations

import math
from functools import lru_cache


def utm_zone_for_longitude(lon: float) -> int:
    """Calculate UTM zone from longitude."""
    return min(60, max(1, int((lon + 180.0) / 6.0) + 1))


def utm_epsg(lat: float, lon: float) -> int:
    """Get EPSG code for UTM zone at given lat/lon."""
    zone = utm_zone_for_longitude(lon)
    return 32600 + zone if lat >= 0 else 32700 + zone


def latlon_to_utm(lat: float, lon: float) -> tuple[float, float, int]:
    """Convert lat/lon to UTM easting, northing, zone (handwritten, local approximation)."""
    zone = utm_zone_for_longitude(lon)
    a = 6378137.0
    k0 = 0.9996
    ecc = 0.00669438
    ecc2 = ecc / (1 - ecc)

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0_rad = math.radians((zone - 1) * 6 - 180 + 3)

    N = a / math.sqrt(1 - ecc * math.sin(lat_rad) ** 2)
    T = math.tan(lat_rad) ** 2
    C = ecc2 * math.cos(lat_rad) ** 2
    A = math.cos(lat_rad) * (lon_rad - lon0_rad)

    M = a * (
        (1 - ecc / 4 - 3 * ecc ** 2 / 64 - 5 * ecc ** 3 / 256) * lat_rad
        - (3 * ecc / 8 + 3 * ecc ** 2 / 32 + 45 * ecc ** 3 / 1024) * math.sin(2 * lat_rad)
        + (15 * ecc ** 2 / 256 + 45 * ecc ** 3 / 1024) * math.sin(4 * lat_rad)
        - (35 * ecc ** 3 / 3072) * math.sin(6 * lat_rad)
    )

    easting = (
        k0
        * N
        * (
            A
            + (1 - T + C) * A ** 3 / 6
            + (5 - 18 * T + T ** 2 + 72 * C - 58 * ecc2) * A ** 5 / 120
        )
        + 500000
    )
    northing = k0 * (
        M
        + N
        * math.tan(lat_rad)
        * (
            A ** 2 / 2
            + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
            + (61 - 58 * T + T ** 2 + 600 * C - 330 * ecc2) * A ** 6 / 720
        )
    )
    return easting, northing, zone


def utm_to_latlon(easting: float, northing: float, zone: int) -> tuple[float, float]:
    """Convert UTM easting/northing back to lat/lon (local approximation)."""
    a = 6378137.0
    ecc = 0.00669438
    ecc2 = ecc / (1 - ecc)
    k0 = 0.9996

    x = easting - 500000
    y = northing

    lon0_rad = math.radians((zone - 1) * 6 - 180 + 3)

    M = y / k0
    mu = M / (a * (1 - ecc / 4 - 3 * ecc ** 2 / 64 - 5 * ecc ** 3 / 256))

    e1 = (1 - math.sqrt(1 - ecc)) / (1 + math.sqrt(1 - ecc))

    lat_rad = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
    )

    N = a / math.sqrt(1 - ecc * math.sin(lat_rad) ** 2)
    T = math.tan(lat_rad) ** 2
    C = ecc2 * math.cos(lat_rad) ** 2
    D = x / (N * k0)

    lon_rad = (
        lon0_rad
        + (D - (1 + 2 * T + C) * D ** 3 / 6 + (5 - 2 * C + 28 * T - 3 * ecc2 + 8 * ecc2 * T) * D ** 5 / 120)
        / math.cos(lat_rad)
    )

    lat = math.degrees(lat_rad)
    lon = math.degrees(lon_rad)

    return lat, lon


def page_dimensions_m(
    paper_size: str, orientation: str, scale: int, paper_sizes_mm: dict[str, tuple[float, float]]
) -> tuple[float, float]:
    """Calculate paper dimensions in meters for a given scale and orientation."""
    width_mm, height_mm = paper_sizes_mm[paper_size]

    if orientation == "portrait":
        width_mm, height_mm = height_mm, width_mm

    return (
        width_mm / 1000.0 * scale,
        height_mm / 1000.0 * scale,
    )
