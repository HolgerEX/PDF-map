"""Map rendering to PDF with matplotlib and contextily."""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
import contextily as ctx

from .constants import PAPER_SIZES_MM
from .models import MapSection
from .projection import page_dimensions_m, utm_to_latlon


class MapRenderer:
    """Renders map sections and overviews to PDF."""

    def __init__(self, global_scale: int, dpi: int = 300, tile_source: str | None = None) -> None:
        """Initialize renderer.

        Args:
            global_scale: Default map scale.
            dpi: Output DPI for PDF.
            tile_source: Tile provider URL.
        """
        self.global_scale = global_scale
        self.dpi = dpi
        self.tile_source = tile_source or "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        self._basemap_cache = {}

    def _get_zoom_for_scale(self, scale: int) -> int:
        """Convert map scale to OSM zoom level."""
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

    def render_overview(self, sections: list[MapSection], total_km: float, output_path: str | Path) -> None:
        """Render full route overview with page boxes."""
        output_path = Path(output_path)

        all_lons = [p.lon for sec in sections for p in sec.points]
        all_lats = [p.lat for sec in sections for p in sec.points]

        fig, ax = plt.subplots(figsize=(16.5, 11.7), dpi=self.dpi)
        ax.plot(all_lons, all_lats, "r-", linewidth=1, label="Route")
        ax.set_aspect("equal", adjustable="box")

        try:
            ctx.add_basemap(ax, crs="EPSG:4326", source=self.tile_source, zoom="auto")
        except Exception as e:
            print(f"Failed to load basemap: {e}")
            ax.set_facecolor("#e0e0e0")

        for sec in sections:
            cx, cy = sec.center_xy
            ref = sec.points[0]
            lon_min, lat_min = self._xy_to_latlon(cx - 10000, cy - 10000, ref.lat, ref.lon)
            lon_max, lat_max = self._xy_to_latlon(cx + 10000, cy + 10000, ref.lat, ref.lon)

            rect = Rectangle(
                (lon_min, lat_min),
                lon_max - lon_min,
                lat_max - lat_min,
                fill=False,
                edgecolor="blue",
                linewidth=1.5,
                linestyle="--",
            )
            ax.add_patch(rect)
            ax.text(
                (lon_min + lon_max) / 2,
                (lat_min + lat_max) / 2,
                str(sec.index + 1),
                ha="center",
                va="center",
                fontsize=8,
                bbox=dict(boxstyle="circle", facecolor="white", alpha=0.7),
            )

        ax.legend()
        ax.set_axis_off()
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

        with PdfPages(output_path) as pdf:
            pdf.savefig(fig)
        plt.close(fig)

    def render_part_overview(
        self, sections: list[MapSection], total_km: float, output_path: str | Path
    ) -> None:
        """Render overview for a subset of sections."""
        output_path = Path(output_path)

        all_lons = [p.lon for sec in sections for p in sec.points]
        all_lats = [p.lat for sec in sections for p in sec.points]
        min_lon, max_lon = min(all_lons), max(all_lons)
        min_lat, max_lat = min(all_lats), max(all_lats)

        lon_pad = (max_lon - min_lon) * 0.1
        lat_pad = (max_lat - min_lat) * 0.1
        min_lon -= lon_pad
        max_lon += lon_pad
        min_lat -= lat_pad
        max_lat += lat_pad

        fig, ax = plt.subplots(figsize=(16.5, 11.7), dpi=self.dpi)
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)
        ax.set_aspect("equal", adjustable="box")

        try:
            ctx.add_basemap(ax, crs="EPSG:4326", source=self.tile_source, zoom="auto")
        except Exception as e:
            print(f"Failed to load basemap: {e}")
            ax.set_facecolor("#e0e0e0")

        ax.plot(all_lons, all_lats, "r-", linewidth=2, label="Route")

        for i, sec in enumerate(sections):
            cx, cy = sec.center_xy
            ref = sec.points[0]
            lon_min, lat_min = self._xy_to_latlon(cx - 10000, cy - 10000, ref.lat, ref.lon)
            lon_max, lat_max = self._xy_to_latlon(cx + 10000, cy + 10000, ref.lat, ref.lon)

            rect = Rectangle(
                (lon_min, lat_min),
                lon_max - lon_min,
                lat_max - lat_min,
                fill=False,
                edgecolor="blue",
                linewidth=1.5,
                linestyle="--",
            )
            ax.add_patch(rect)
            ax.text(
                (lon_min + lon_max) / 2,
                (lat_min + lat_max) / 2,
                str(i + 1),
                ha="center",
                va="center",
                fontsize=8,
                bbox=dict(boxstyle="circle", facecolor="white", alpha=0.7),
            )

        ax.set_title(f"Part Overview - {total_km:.1f} km")
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.legend()
        fig.tight_layout()

        with PdfPages(output_path) as pdf:
            pdf.savefig(fig)
        plt.close(fig)

    def render_section(self, section: MapSection, output_path: str | Path) -> None:
        """Render a single map section to PDF."""
        output_path = Path(output_path)

        w_mm, h_mm = PAPER_SIZES_MM[section.paper_size]
        if section.orientation == "portrait":
            w_mm, h_mm = h_mm, w_mm

        w_inch = w_mm / 25.4
        h_inch = h_mm / 25.4

        fig, ax = plt.subplots(figsize=(w_inch, h_inch), dpi=self.dpi)
        ax.set_axis_off()
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

        # Use effective_zoom (respects custom zoom)
        zoom = section.effective_zoom

        # Calculate bounds
        cx, cy = section.effective_center_xy
        ref = section.points[0]
        page_w_m, page_h_m = page_dimensions_m(
            section.paper_size, section.orientation, section.effective_scale, PAPER_SIZES_MM
        )

        lon_min, lat_min = self._xy_to_latlon(cx - page_w_m / 2, cy - page_h_m / 2, ref.lat, ref.lon)
        lon_max, lat_max = self._xy_to_latlon(cx + page_w_m / 2, cy + page_h_m / 2, ref.lat, ref.lon)

        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)

        # Add basemap with retry logic
        success = self._attempt_basemap(ax, zoom)
        if not success:
            ax.set_facecolor("#e0e0e0")

        # Draw route
        lons = [p.lon for p in section.points]
        lats = [p.lat for p in section.points]
        ax.plot(lons, lats, "r-", linewidth=0.5, alpha=0.7, zorder=5)

        plt.savefig(output_path, dpi=self.dpi, bbox_inches=None, pad_inches=0)
        plt.close(fig)

    def _attempt_basemap(self, ax, zoom: int, retries: int = 3) -> bool:
        """Attempt to add basemap with retry logic."""
        for attempt in range(1, retries + 1):
            try:
                ctx.add_basemap(ax, crs="EPSG:4326", source=self.tile_source, zoom=zoom)
                return True
            except Exception as e:
                print(f"Basemap attempt {attempt} failed: {e}")
                if attempt < retries:
                    time.sleep(1)
        return False

    def _xy_to_latlon(self, x: float, y: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
        """Convert projected coordinates to lat/lon (local approximation)."""
        import math

        lat_deg_per_m = 1 / 111319.9
        lon_deg_per_m = 1 / (111319.9 * math.cos(math.radians(ref_lat)))

        from .projection import latlon_to_utm

        ref_x, ref_y, _ = latlon_to_utm(ref_lat, ref_lon)
        d_x = x - ref_x
        d_y = y - ref_y

        lon = ref_lon + d_x * lon_deg_per_m
        lat = ref_lat + d_y * lat_deg_per_m

        return lon, lat
