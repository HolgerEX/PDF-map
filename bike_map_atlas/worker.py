"""Worker thread functions (safe from Tkinter main thread)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from .gpx_parser import GPXParser
from .route_splitter import RouteSplitter

if TYPE_CHECKING:
    from .models import MapSection, RoutePoint


def load_gpx_worker(
    file_path: str, on_complete: Callable, on_error: Callable
) -> None:
    """Load GPX file in worker thread (main thread safe).

    Args:
        file_path: Path to GPX file.
        on_complete: Callback (points, total_km) - will be called on main thread via root.after().
        on_error: Callback (error_msg) - will be called on main thread via root.after().
    """
    try:
        points, total_km = GPXParser.parse(file_path)
        on_complete(points, total_km)
    except Exception as e:
        on_error(str(e))


def split_route_worker(
    points: list[RoutePoint],
    global_scale: int,
    paper_size: str,
    overlap_m: float,
    on_complete: Callable,
    on_error: Callable,
) -> None:
    """Split route in worker thread (main thread safe).

    Args:
        points: List of RoutePoints.
        global_scale: Map scale.
        paper_size: Paper size name.
        overlap_m: Overlap in meters.
        on_complete: Callback (sections) - will be called on main thread via root.after().
        on_error: Callback (error_msg) - will be called on main thread via root.after().
    """
    try:
        splitter = RouteSplitter(points, global_scale, paper_size, overlap_m)
        sections = splitter.split()
        on_complete(sections)
    except Exception as e:
        on_error(str(e))
