"""Route splitting algorithm."""

from __future__ import annotations

from .constants import PAPER_SIZES_MM
from .models import MapSection, RoutePoint
from .projection import page_dimensions_m


class RouteSplitter:
    """Splits a route into map sections with automatic orientation selection."""

    def __init__(
        self,
        points: list[RoutePoint],
        global_scale: int,
        paper_size: str,
        overlap_m: float = 500.0,
    ) -> None:
        """
        Initialize the splitter.

        Args:
            points: List of RoutePoints.
            global_scale: Default map scale (e.g., 25000 for 1:25,000).
            paper_size: Paper size name (e.g., "A4").
            overlap_m: Overlap between consecutive sections in meters.
        """
        self.points = points
        self.global_scale = global_scale
        self.paper_size = paper_size
        self.overlap_m = max(0.0, overlap_m)
        self.edge_margin = 0.05

    def split(self) -> list[MapSection]:
        """
        Split the route into sections.

        Returns:
            List of MapSection objects.
        """
        if not self.points:
            return []

        sections: list[MapSection] = []
        start_idx = 0

        while start_idx < len(self.points):
            # Try both orientations and pick the best
            candidates = [
                self._fit_orientation(start_idx, "normal"),
                self._fit_orientation(start_idx, "rot90"),
            ]

            best = max(candidates, key=lambda item: item["score"])
            end_idx = best["end_idx"]

            # Create section
            sections.append(
                self._create_section(
                    start_idx=start_idx,
                    end_idx=end_idx,
                    index=len(sections),
                    mode=best["mode"],
                )
            )

            # Stop if we've reached the end
            if end_idx >= len(self.points) - 1:
                break

            # Calculate overlap start (restore overlap feature)
            end_m = self.points[end_idx].distance_m
            overlap_start_m = max(0.0, end_m - self.overlap_m)
            next_idx = self._find_index_at_distance(overlap_start_m)

            # Prevent infinite loop
            if next_idx <= start_idx:
                next_idx = start_idx + 1

            start_idx = next_idx

        return sections

    def _get_available_dimensions(self, mode: str) -> tuple[float, float]:
        """Get available page dimensions (width, height) in meters."""
        w_mm, h_mm = PAPER_SIZES_MM[self.paper_size]

        orientation = "landscape" if mode == "normal" else "portrait"
        page_w_m, page_h_m = page_dimensions_m(
            self.paper_size, orientation, self.global_scale, PAPER_SIZES_MM
        )

        # Apply margin
        return (
            page_w_m * (1.0 - 2.0 * self.edge_margin),
            page_h_m * (1.0 - 2.0 * self.edge_margin),
        )

    def _fit_orientation(self, start_idx: int, mode: str) -> dict:
        """
        Fit as many points as possible in the given orientation.

        Returns:
            Dict with "end_idx", "mode", "score".
        """
        avail_w, avail_h = self._get_available_dimensions(mode)

        origin = self.points[start_idx]
        min_x = max_x = 0.0
        min_y = max_y = 0.0
        best_result = None

        for end_idx in range(start_idx + 1, len(self.points)):
            point = self.points[end_idx]

            dx = point.x - origin.x
            dy = point.y - origin.y

            # Apply rotation for rot90 mode
            if mode == "normal":
                rx, ry = dx, dy
            else:
                rx, ry = dy, -dx

            new_min_x = min(min_x, rx)
            new_max_x = max(max_x, rx)
            new_min_y = min(min_y, ry)
            new_max_y = max(max_y, ry)

            width = new_max_x - new_min_x
            height = new_max_y - new_min_y

            # Stop if page is full
            if width > avail_w or height > avail_h:
                break

            min_x, max_x = new_min_x, new_max_x
            min_y, max_y = new_min_y, new_max_y

            # Compute score
            bbox_area = max(width, 1e-6) * max(height, 1e-6)
            page_area = avail_w * avail_h
            density = bbox_area / page_area
            coverage = end_idx - start_idx
            score = coverage * (0.3 + 0.7 * density)

            best_result = {"end_idx": end_idx, "mode": mode, "score": score}

        # Ensure we always return something
        if best_result is None:
            return {"end_idx": start_idx, "mode": mode, "score": 0}

        return best_result

    def _create_section(self, start_idx: int, end_idx: int, index: int, mode: str) -> MapSection:
        """Create a MapSection from a range of points."""
        pts = self.points[start_idx : end_idx + 1]

        # Calculate UTM bounds
        min_x = min(p.x for p in pts)
        max_x = max(p.x for p in pts)
        min_y = min(p.y for p in pts)
        max_y = max(p.y for p in pts)
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0

        # Determine orientation
        w_mm, h_mm = PAPER_SIZES_MM[self.paper_size]
        page_w_m = (w_mm / 1000.0) * self.global_scale
        page_h_m = (h_mm / 1000.0) * self.global_scale
        paper_is_landscape = page_w_m >= page_h_m

        if mode == "normal":
            orientation = "landscape" if paper_is_landscape else "portrait"
        else:  # rot90
            orientation = "portrait" if paper_is_landscape else "landscape"

        # Do NOT flip orientation unconditionally (bug fix)

        return MapSection(
            index=index,
            points=pts,
            start_m=pts[0].distance_m,
            end_m=pts[-1].distance_m,
            center_xy=(cx, cy),
            orientation=orientation,
            paper_size=self.paper_size,
            global_scale=self.global_scale,
        )

    def _find_index_at_distance(self, dist_m: float) -> int:
        """Binary search to find the point index at a given distance."""
        lo, hi = 0, len(self.points) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.points[mid].distance_m < dist_m:
                lo = mid + 1
            else:
                hi = mid - 1
        return max(0, lo)
