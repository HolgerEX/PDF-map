from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class RoutePoint:
    lat: float
    lon: float
    x: float
    y: float
    distance_m: float = 0.0
    zone: Optional[int] = None


@dataclass
class MapSection:
    index: int
    points: list[RoutePoint]
    start_m: float
    end_m: float
    center_xy: tuple[float, float]
    orientation: str
    paper_size: str
    global_scale: int
    custom_scale: Optional[int] = None
    custom_center_xy: Optional[tuple[float, float]] = None
    custom_zoom: Optional[int] = None

    @property
    def effective_scale(self) -> int:
        return self.custom_scale if self.custom_scale is not None else self.global_scale

    @property
    def effective_center_xy(self) -> tuple[float, float]:
        return self.custom_center_xy if self.custom_center_xy is not None else self.center_xy

    @property
    def effective_zoom(self) -> int:
        if self.custom_zoom is not None:
            return self.custom_zoom

        scale = self.effective_scale
        if scale <= 5_000:
            return 18
        if scale <= 10_000:
            return 17
        if scale <= 25_000:
            return 16
        if scale <= 50_000:
            return 15
        if scale <= 100_000:
            return 14
        return 12

    @property
    def center_xy(self) -> tuple[float, float]:
        return self._center_xy

    @center_xy.setter
    def center_xy(self, value: tuple[float, float]) -> None:
        self._center_xy = value

    def set_custom_center(self, x: float, y: float) -> None:
        self.custom_center_xy = (x, y)
