"""Data models for bike map atlas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoutePoint:
    """A single point along the route."""

    lat: float
    lon: float
    distance_m: float = 0.0


@dataclass
class MapSection:
    """A section of the route to be rendered on a single map page."""

    index: int
    start_m: float
    end_m: float
    orientation: str
    center_utm: tuple[float, float]
    center_xy: tuple[float, float] = (0.0, 0.0)
    custom_center_utm: tuple[float, float] | None = None
    scale: int | None = None

    @property
    def effective_scale(self) -> int:
        """Return the scale, or 25000 if not explicitly set."""
        return self.scale if self.scale is not None else 25000

    @property
    def effective_center_xy(self) -> tuple[float, float]:
        """Return custom center if set, otherwise the default center."""
        return self.center_xy

    @property
    def effective_zoom(self) -> int:
        """Return zoom level derived from scale."""
        return max(8, min(16, 24 - int(__import__('math').log2(self.effective_scale / 250))))
