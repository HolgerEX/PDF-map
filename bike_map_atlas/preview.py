"""Interactive Matplotlib preview with draggable rectangles."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import contextily as ctx
import tkinter as tk

from .projection import latlon_to_utm

if TYPE_CHECKING:
    from .models import MapSection


class InteractivePreview:
    """Matplotlib-based interactive preview with draggable section boxes."""

    def __init__(
        self,
        parent_frame: tk.Widget,
        on_double_click: Callable[[int], None],
        on_drag_end: Callable[[int, float, float], None],
        tile_source: str,
    ) -> None:
        """Initialize preview.

        Args:
            parent_frame: Parent Tkinter frame.
            on_double_click: Callback for double-click on section.
            on_drag_end: Callback for drag end (section_idx, x, y).
            tile_source: Tile provider URL.
        """
        self.parent = parent_frame
        self.on_double_click = on_double_click
        self.on_drag_end = on_drag_end
        self.tile_source = tile_source

        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.ax = None
        self.sections: list[MapSection] = []
        self.total_km = 0.0
        self.draggables: list = []

        self.parent.bind("<Configure>", self._on_resize)
        self.parent.after_idle(self._initial_resize)

    def _initial_resize(self) -> None:
        w = self.parent.winfo_width()
        h = self.parent.winfo_height()
        if w > 10 and h > 10:
            self.fig.set_size_inches(w / self.fig.dpi, h / self.fig.dpi)
            self.canvas.draw_idle()

    def _on_resize(self, event) -> None:
        if event.widget == self.parent:
            w, h = event.width, event.height
            if w > 10 and h > 10:
                self.fig.set_size_inches(w / self.fig.dpi, h / self.fig.dpi)
                self.canvas.draw_idle()

    def update(self, sections: list[MapSection], total_km: float) -> None:
        """Update preview with new sections."""
        self.sections = sections
        self.total_km = total_km
        self._draw()

    def _draw(self) -> None:
        """Redraw the preview map."""
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.ax.set_frame_on(False)
        self.fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

        if not self.sections:
            return

        all_lons = [p.lon for sec in self.sections for p in sec.points]
        all_lats = [p.lat for sec in self.sections for p in sec.points]

        min_lon, max_lon = min(all_lons), max(all_lons)
        min_lat, max_lat = min(all_lats), max(all_lats)

        lon_pad = (max_lon - min_lon) * 0.1
        lat_pad = (max_lat - min_lat) * 0.1
        min_lon -= lon_pad
        max_lon += lon_pad
        min_lat -= lat_pad
        max_lat += lat_pad

        self.ax.set_xlim(min_lon, max_lon)
        self.ax.set_ylim(min_lat, max_lat)

        try:
            ctx.add_basemap(self.ax, crs="EPSG:4326", source=self.tile_source, zoom="auto")
        except Exception as e:
            print(f"Preview basemap failed: {e}")
            self.ax.set_facecolor("#e0e0e0")

        self.ax.plot(all_lons, all_lats, "r-", linewidth=2, label="Route")

        # Mark section boundaries
        for i, sec in enumerate(self.sections):
            if i > 0:
                first_pt = sec.points[0]
                self.ax.plot(first_pt.lon, first_pt.lat, "rx", markersize=8, markeredgewidth=2, zorder=6)

        # Draw draggable rectangles
        self.draggables = []
        for i, sec in enumerate(self.sections):
            self._draw_section_box(i, sec)

        self.ax.set_aspect("equal", adjustable="box")
        self.canvas.draw()

    def _draw_section_box(self, idx: int, sec: MapSection) -> None:
        """Draw a draggable rectangle for a section."""
        from .draggable import DraggableRectangle

        cx, cy = sec.effective_center_xy
        ref = sec.points[0]
        center_lon, center_lat = self._xy_to_latlon(cx, cy, ref.lat, ref.lon)

        w_mm, h_mm = self._get_paper_mm(sec)
        half_w_m = (w_mm / 1000.0) * sec.effective_scale / 2
        half_h_m = (h_mm / 1000.0) * sec.effective_scale / 2

        lat_deg_per_m = 1 / 111319.9
        lon_deg_per_m = 1 / (111319.9 * math.cos(math.radians(ref.lat)))

        lon_min = center_lon - half_w_m * lon_deg_per_m
        lon_max = center_lon + half_w_m * lon_deg_per_m
        lat_min = center_lat - half_h_m * lat_deg_per_m
        lat_max = center_lat + half_h_m * lat_deg_per_m

        rect = Rectangle(
            (lon_min, lat_min),
            lon_max - lon_min,
            lat_max - lat_min,
            fill=False,
            edgecolor="blue",
            linewidth=2,
            alpha=0.7,
        )
        self.ax.add_patch(rect)
        self.draggables.append(
            DraggableRectangle(rect, idx, self.on_double_click, self.on_drag_end)
        )

        # Label with page number, scale, and zoom
        label_text = f"{idx+1}\n1:{sec.effective_scale:,}"
        if sec.custom_zoom is not None:  # Use is not None check
            label_text += f"\nz{sec.custom_zoom}"
        else:
            label_text += "\nz(auto)"

        self.ax.text(
            (lon_min + lon_max) / 2,
            (lat_min + lat_max) / 2,
            label_text,
            ha="center",
            va="center",
            fontsize=9,
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )

    def _get_paper_mm(self, sec: MapSection) -> tuple[float, float]:
        """Get paper size in mm for a section's orientation."""
        from .constants import PAPER_SIZES_MM

        w_mm, h_mm = PAPER_SIZES_MM[sec.paper_size]
        if sec.orientation == "portrait":
            w_mm, h_mm = h_mm, w_mm
        return w_mm, h_mm

    def _xy_to_latlon(self, x: float, y: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
        """Convert projected coordinates to lat/lon."""
        lat_deg_per_m = 1 / 111319.9
        lon_deg_per_m = 1 / (111319.9 * math.cos(math.radians(ref_lat)))

        ref_x, ref_y, _ = latlon_to_utm(ref_lat, ref_lon)
        d_x = x - ref_x
        d_y = y - ref_y

        lon = ref_lon + d_x * lon_deg_per_m
        lat = ref_lat + d_y * lat_deg_per_m

        return lon, lat
