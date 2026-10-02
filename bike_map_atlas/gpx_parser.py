"""GPX file parser."""

from __future__ import annotations

import math
from pathlib import Path

import gpxpy

from .models import RoutePoint
from .projection import latlon_to_utm


class GPXParser:
    """Parses GPX files and extracts route points with projected coordinates."""

    @staticmethod
    def parse(file_path: str | Path) -> tuple[list[RoutePoint], float]:
        """
        Parse a GPX file and return a list of RoutePoints and total distance in km.

        Args:
            file_path: Path to the GPX file.

        Returns:
            Tuple of (points, total_distance_km).

        Raises:
            ValueError: If the GPX file has fewer than 2 points.
        """
        path = Path(file_path)

        with path.open("r", encoding="utf-8") as f:
            gpx = gpxpy.parse(f)

        points: list[RoutePoint] = []
        total_m = 0.0

        for track in gpx.tracks:
            for segment in track.segments:
                seg_points = []
                for pt in segment.points:
                    x, y, zone = latlon_to_utm(pt.latitude, pt.longitude)
                    seg_points.append((x, y, pt.latitude, pt.longitude, zone))

                # Skip single-point segments (documented policy: ignore discontinuous segments)
                if len(seg_points) < 2:
                    continue

                for i, (x, y, lat, lon, zone) in enumerate(seg_points):
                    if i > 0:
                        px, py, _, _, _ = seg_points[i - 1]
                        total_m += math.hypot(x - px, y - py)

                    points.append(
                        RoutePoint(lat=lat, lon=lon, x=x, y=y, distance_m=total_m, zone=zone)
                    )

        if len(points) < 2:
            raise ValueError("The GPX file must contain at least two track points.")

        return points, total_m / 1000.0
