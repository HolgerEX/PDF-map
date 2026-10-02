#!/usr/bin/env python3
"""
Bike Map Atlas Generator - Final Working Version
- No external CLI tools (PaperMap/Mapnik removed)
- Interactive Matplotlib overview (double-click for scale, drag to move)
- PDF generation with Matplotlib + contextily (high-res OSM tiles)
- Per‑page custom scale and center
- Split PDF by km or file size
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
import threading
import math
import os
import tempfile
import shutil
from dataclasses import dataclass
from typing import List, Tuple, Optional
from pathlib import Path

import gpxpy
import gpxpy.gpx
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import contextily as ctx
import numpy as np
from reportlab.lib.pagesizes import A3, A4, A5, A6, letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import PyPDF2

import time
import contextily as ctx


# ============================================================================
# PAPER SIZES (width, height in mm)
# ============================================================================
PAPER_SIZES_MM = {
    'A3': (420, 297),
    'A4': (297, 210),
    'A5': (210, 148),
    'A6': (148, 105),
    'Letter': (279, 216)
}
TILE_PROVIDERS = {
    "OpenStreetMap (default)": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "CyclOSM (bicycle)": "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
    "OpenCycleMap": "https://{s}.tile.opencyclemap.org/cycle/{z}/{x}/{y}.png"
}
MAP_SCALES = {
    '1:5,000 (City)': 5000,
    '1:10,000 (Town)': 10000,
    '1:25,000 (Regional)': 25000,
    '1:50,000 (Country)': 50000,
    '1:100,000 (Long distance)': 100000,
    '1:500,000 (Very Long distance)': 500000
}

# ============================================================================
# UTM CONVERSION (accurate enough for splitting)
# ============================================================================
class UTMConverter:
    @staticmethod
    def latlon_to_utm(lat: float, lon: float) -> Tuple[float, float, int]:
        zone = int((lon + 180) / 6) + 1
        a = 6378137.0
        k0 = 0.9996
        ecc = 0.00669438
        ecc2 = ecc / (1 - ecc)

        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        lon0_rad = math.radians((zone - 1) * 6 - 180 + 3)

        N = a / math.sqrt(1 - ecc * math.sin(lat_rad)**2)
        T = math.tan(lat_rad)**2
        C = ecc2 * math.cos(lat_rad)**2
        A = math.cos(lat_rad) * (lon_rad - lon0_rad)

        M = a * ((1 - ecc/4 - 3*ecc**2/64 - 5*ecc**3/256) * lat_rad -
                 (3*ecc/8 + 3*ecc**2/32 + 45*ecc**3/1024) * math.sin(2*lat_rad) +
                 (15*ecc**2/256 + 45*ecc**3/1024) * math.sin(4*lat_rad) -
                 (35*ecc**3/3072) * math.sin(6*lat_rad))

        easting = k0 * N * (A + (1 - T + C) * A**3 / 6 +
                           (5 - 18*T + T**2 + 72*C - 58*ecc2) * A**5 / 120) + 500000
        northing = k0 * (M + N * math.tan(lat_rad) *
                        (A**2 / 2 + (5 - T + 9*C + 4*C**2) * A**4 / 24 +
                         (61 - 58*T + T**2 + 600*C - 330*ecc2) * A**6 / 720))
        return easting, northing, zone

# ============================================================================
# DATA CLASSES
# ============================================================================
@dataclass
class RoutePoint:
    lat: float
    lon: float
    easting: float = 0.0
    northing: float = 0.0
    distance_m: float = 0.0

@dataclass
class MapSection:
    index: int
    points: List[RoutePoint]
    bounds_utm: Tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)
    start_m: float
    end_m: float
    center_utm: Tuple[float, float]
    orientation: str
    paper_size: str
    global_scale: int
    custom_scale: Optional[int] = None
    custom_center_utm: Optional[Tuple[float, float]] = None
    custom_zoom: Optional[int] = None

    @property
    def effective_zoom(self) -> int:
        """Return the tile zoom level to use for this section."""
        if self.custom_zoom is not None:
            return self.custom_zoom
        # Default zoom from scale (as defined in MapRenderer._get_zoom_for_scale)
        scale = self.effective_scale
        if scale <= 5000:
            return 18
        elif scale <= 10000:
            return 17
        elif scale <= 25000:
            return 16
        elif scale <= 50000:
            return 15
        elif scale <= 100000:
            return 14
        else:
            return 12

    @property
    def effective_scale(self) -> int:
        return self.custom_scale if self.custom_scale is not None else self.global_scale

    @property
    def effective_center_utm(self) -> Tuple[float, float]:
        return self.custom_center_utm if self.custom_center_utm is not None else self.center_utm

    def set_custom_center(self, easting: float, northing: float):
        self.custom_center_utm = (easting, northing)

# ============================================================================
# GPX PARSER
# ============================================================================
class GPXParser:
    @staticmethod
    def parse(file_path: str) -> Tuple[List[RoutePoint], float]:
        with open(file_path, 'r') as f:
            gpx = gpxpy.parse(f)

        points = []
        total_m = 0.0

        for track in gpx.tracks:
            for segment in track.segments:
                seg_points = []
                for pt in segment.points:
                    easting, northing, _ = UTMConverter.latlon_to_utm(pt.latitude, pt.longitude)
                    seg_points.append((easting, northing, pt.latitude, pt.longitude))

                if len(seg_points) > 1:
                    for i, (e, n, lat, lon) in enumerate(seg_points):
                        if i > 0:
                            pe, pn, _, _ = seg_points[i-1]
                            total_m += math.hypot(e - pe, n - pn)
                        points.append(RoutePoint(lat=lat, lon=lon, easting=e, northing=n, distance_m=total_m))

        if not points:
            raise ValueError("No track points found")
        return points, total_m / 1000.0

# ============================================================================
# ROUTE SPLITTER (with overlap and minimum page length)
# ============================================================================
import math
from typing import List, Tuple

class RouteSplitter:
    def __init__(self, points: List["RoutePoint"], global_scale: int, paper_size: str, overlap_m: float = 500):
        self.points = points
        self.global_scale = global_scale
        self.paper_size = paper_size
        self.overlap_m = overlap_m
        self.total_m = points[-1].distance_m if points else 0

        w_mm, h_mm = PAPER_SIZES_MM[paper_size]
        self.page_width_m = (w_mm / 1000.0) * global_scale
        self.page_height_m = (h_mm / 1000.0) * global_scale

        self.edge_margin = 0.05

        self.avail_w = self.page_width_m * (1 - 2 * self.edge_margin)
        self.avail_h = self.page_height_m * (1 - 2 * self.edge_margin)

    # -------------------------
    # MAIN SPLIT
    # -------------------------
    def split(self) -> List["MapSection"]:
        if not self.points:
            return []

        sections = []
        i = 0
        total_pts = len(self.points)

        while i < total_pts:
            best = None

            # Only 2 orientations: 0° and 90°
            for mode in ["normal", "rot90"]:
                result = self._fit_orientation(i, mode)
                if not result:
                    continue
                if result and (not best or result["score"] > best["score"]):
                    best = result

            if not best:
                best = {"end_idx": i + 1, "mode": "normal"}

            sections.append(
                self._create_section(
                    i,
                    best["end_idx"],
                    len(sections),
                    best["mode"]
                )
            )

            # overlap handling
            end_m = self.points[best["end_idx"]].distance_m
            overlap_start_m = end_m #- self.overlap_m
            next_i = self._find_index_at_distance(overlap_start_m)

            if next_i <= i:
                next_i = i + 1

            i = next_i

        return sections

    # -------------------------
    # FIT WITH ORIENTATION
    # -------------------------
    def _fit_orientation(self, start_idx: int, mode: str):
        origin = self.points[start_idx]
        ox, oy = origin.easting, origin.northing

        min_x = max_x = 0.0
        min_y = max_y = 0.0

        j = start_idx
        best_result = None

        while j + 1 < len(self.points):
            p = self.points[j + 1]

            dx = p.easting - ox
            dy = p.northing - oy

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

            if width > self.avail_w or height > self.avail_h:
                break

            min_x, max_x = new_min_x, new_max_x
            min_y, max_y = new_min_y, new_max_y
            j += 1

            bbox_area = max(width, 1e-6) * max(height, 1e-6)
            page_area = self.avail_w * self.avail_h
            density = bbox_area / page_area

            coverage = j - start_idx

            score = coverage * (0.3 + 0.7 * density)

            best_result = {
                "end_idx": j,
                "mode": mode,
                "score": score
            }

        # ✅ CRITICAL FIX: ensure we ALWAYS return something
        if best_result is None:
            return {
                "end_idx": start_idx,
                "mode": mode,
                "score": 0
            }

        return best_result
    # -------------------------
    # CREATE SECTION
    # -------------------------
    def _create_section(self, start_idx: int, end_idx: int, idx: int, mode: str) -> "MapSection":
        pts = self.points[start_idx:end_idx + 1]

        # UTM bounds (axis‑aligned, used for map extraction)
        min_x = min(p.easting for p in pts)
        max_x = max(p.easting for p in pts)
        min_y = min(p.northing for p in pts)
        max_y = max(p.northing for p in pts)
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0

        # Paper natural orientation
        paper_is_landscape = self.page_width_m >= self.page_height_m

        # Decide page orientation based on mode
        if mode == "normal":
            orientation = "landscape" if paper_is_landscape else "portrait"
        else:  # rot90
            orientation = "portrait" if paper_is_landscape else "landscape"

        # If your renderer still gets it wrong, uncomment the next line to flip
        orientation = "landscape" if orientation == "portrait" else "portrait"

        bounds_utm = (
            cx - self.page_width_m / 2,
            cy - self.page_height_m / 2,
            cx + self.page_width_m / 2,
            cy + self.page_height_m / 2
        )

        return MapSection(
            index=idx,
            points=pts,
            bounds_utm=bounds_utm,
            start_m=pts[0].distance_m,
            end_m=pts[-1].distance_m,
            center_utm=(cx, cy),
            orientation=orientation,
            paper_size=self.paper_size,
            global_scale=self.global_scale
        )

        return MapSection(
            index=idx,
            points=pts,
            bounds_utm=bounds_utm,
            start_m=pts[0].distance_m,
            end_m=pts[-1].distance_m,
            center_utm=(cx, cy),
            orientation=orientation,
            paper_size=self.paper_size,
            global_scale=self.global_scale
        )
    # -------------------------
    # DISTANCE LOOKUP
    # -------------------------
    def _find_index_at_distance(self, dist_m: float) -> int:
        lo, hi = 0, len(self.points) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.points[mid].distance_m < dist_m:
                lo = mid + 1
            else:
                hi = mid - 1
        return max(0, lo)



# ============================================================================
# PDF RENDERER (Matplotlib + contextily)
# ============================================================================
class MapRenderer:
    def __init__(self, global_scale: int, dpi: int = 300, tile_source: str = None):
        self.global_scale = global_scale
        self.dpi = dpi
        self.tile_source = tile_source or TILE_PROVIDERS["OpenStreetMap (default)"]

    def _get_zoom_for_scale(self, scale: int) -> int:
        """Convert map scale denominator to a suitable OSM zoom level."""
        # Approximate mapping: scale 1:5000 -> zoom 18, 1:100000 -> zoom 12
        if scale <= 5000:
            return 18
        elif scale <= 10000:
            return 17
        elif scale <= 25000:
            return 16
        elif scale <= 50000:
            return 15
        elif scale <= 100000:
            return 14
        else:
            return 12
            
            
    def render_part_overview(self, sections: List[MapSection], total_km: float, output_path: str):
        """Generate overview PDF for a subset of sections, properly scaled and undistorted."""
        if not sections:
            return

        # Collect all route points
        all_lons = [p.lon for sec in sections for p in sec.points]
        all_lats = [p.lat for sec in sections for p in sec.points]
        min_lon, max_lon = min(all_lons), max(all_lons)
        min_lat, max_lat = min(all_lats), max(all_lats)

        # Add 10% padding
        lon_pad = (max_lon - min_lon) * 0.1
        lat_pad = (max_lat - min_lat) * 0.1
        min_lon -= lon_pad
        max_lon += lon_pad
        min_lat -= lat_pad
        max_lat += lat_pad

        # Create figure (A3 landscape)
        fig, ax = plt.subplots(figsize=(16.5, 11.7), dpi=self.dpi)
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)
        ax.set_aspect('equal', adjustable='box')  # prevents distortion

        # Add basemap (auto zoom)
        try:
            ctx.add_basemap(ax, crs='EPSG:4326', source=self.tile_source, zoom='auto')
        except:
            ax.set_facecolor('#e0e0e0')

        # Draw route line
        ax.plot(all_lons, all_lats, 'r-', linewidth=2, label='Route')

        # Draw section boxes
        for i, sec in enumerate(sections):
            xmin, ymin, xmax, ymax = self._get_paper_bounds_utm(sec)
            ref = sec.points[0]
            lon_min, lat_min = self._utm_to_latlon(xmin, ymin, ref.lat, ref.lon)
            lon_max, lat_max = self._utm_to_latlon(xmax, ymax, ref.lat, ref.lon)
            rect = Rectangle((lon_min, lat_min), lon_max - lon_min, lat_max - lat_min,
                             fill=False, edgecolor='blue', linewidth=1.5, linestyle='--')
            ax.add_patch(rect)
            ax.text((lon_min + lon_max)/2, (lat_min + lat_max)/2, str(i+1),
                    ha='center', va='center', fontsize=8,
                    bbox=dict(boxstyle='circle', facecolor='white', alpha=0.7))

        ax.set_title(f'Part Overview - {total_km:.1f} km')
        ax.set_xlabel('Longitude')
        ax.set_ylabel('Latitude')
        ax.legend()
        fig.tight_layout()

        # Save as PDF
        from matplotlib.backends.backend_pdf import PdfPages
        with PdfPages(output_path) as pdf:
            pdf.savefig(fig)
        plt.close(fig)  
    
    def render_overview(self, sections: List[MapSection], total_km: float, output_path: str):
        """Generate overview PDF with route and page boxes (A3 landscape, 600 DPI)."""
        all_lons = [p.lon for sec in sections for p in sec.points]
        all_lats = [p.lat for sec in sections for p in sec.points]
        # A3 landscape at 600 DPI: 16.5 x 11.7 inches -> 9900 x 7020 pixels
        fig, ax = plt.subplots(figsize=(16.5, 11.7), dpi=self.dpi)
        ax.plot(all_lons, all_lats, 'r-', linewidth=1, label='Route')
        ax.set_aspect('equal', adjustable='box')  # prevents distortion

        # Use a fixed zoom level for overview (e.g., 12) – you can adjust
        zoom = 12
        try:
            ctx.add_basemap(ax, crs='EPSG:4326', source=self.tile_source, zoom='auto')
        except:
            ax.set_facecolor('#e0e0e0')
        for sec in sections:
            xmin, ymin, xmax, ymax = self._get_paper_bounds_utm(sec)
            ref = sec.points[0]
            lon_min, lat_min = self._utm_to_latlon(xmin, ymin, ref.lat, ref.lon)
            lon_max, lat_max = self._utm_to_latlon(xmax, ymax, ref.lat, ref.lon)
            rect = Rectangle((lon_min, lat_min), lon_max-lon_min, lat_max-lat_min,
                             fill=False, edgecolor='blue', linewidth=1.5, linestyle='--')
            ax.add_patch(rect)
            ax.text((lon_min+lon_max)/2, (lat_min+lat_max)/2, str(sec.index+1),
                    ha='center', va='center', fontsize=8,
                    bbox=dict(boxstyle='circle', facecolor='white', alpha=0.7))
        ##ax.set_title(f'Route Overview - {total_km:.1f} km')
        #ax.set_xlabel('Longitude')
        #ax.set_ylabel('Latitude')
        ax.legend()
        fig.tight_layout()
        ax.set_axis_off()
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        from matplotlib.backends.backend_pdf import PdfPages
        with PdfPages(output_path) as pdf:
            pdf.savefig(fig)
        plt.close(fig)

    import time
    import contextily as ctx

    def render_section(self, section: MapSection, output_path: str):
        """Render a single section as a full‑bleed PDF (no margins, no axes)."""
        w_mm, h_mm = PAPER_SIZES_MM[section.paper_size]
        if section.orientation == "landscape":
            w_mm, h_mm = h_mm, w_mm
        w_inch = w_mm / 25.4
        h_inch = h_mm / 25.4

        fig, ax = plt.subplots(figsize=(w_inch, h_inch), dpi=self.dpi)
        # Remove all axes, ticks, labels, titles
        ax.set_axis_off()
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

        # Calculate the map bounds in lon/lat
        xmin, ymin, xmax, ymax = self._get_paper_bounds_utm(section)
        ref = section.points[0]
        lon_min, lat_min = self._utm_to_latlon(xmin, ymin, ref.lat, ref.lon)
        lon_max, lat_max = self._utm_to_latlon(xmax, ymax, ref.lat, ref.lon)
        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)

        # --- Basemap retry logic ---
        zoom = self._get_zoom_for_scale(section.effective_scale)
        standard_osm_source = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        success = False

        # Helper to attempt adding basemap
        def attempt_basemap(source, retries=3):
            for attempt in range(1, retries + 1):
                try:
                    ctx.add_basemap(ax, crs='EPSG:4326', source=source, zoom=zoom)
                    return True
                except Exception as e:
                    print(f"Attempt {attempt} with source {source} failed: {e}")
                    if attempt < retries:
                        time.sleep(1)  # wait before retry
            return False

        # First, try with the configured tile source (online provider)
        if attempt_basemap(self.tile_source, retries=3):
            success = True
        else:
            # Fallback: try with standard OSM tile server (3 attempts)
            print("Falling back to standard OSM tile server...")
            if attempt_basemap(standard_osm_source, retries=3):
                success = True

        # If all attempts failed, set a grey background
        if not success:
            print("All tile server attempts failed. Using grey background.")
            ax.set_facecolor('#e0e0e0')

        # Draw route line (thinner, semi‑transparent)
        lons = [p.lon for p in section.points]
        lats = [p.lat for p in section.points]
        ax.plot(lons, lats, 'r-', linewidth=0.5, alpha=0.7, zorder=5)

        # Start and end markers (commented out as in original)
        #ax.scatter(lons[0], lats[0], c='green', s=40, zorder=6,
        #           edgecolors='black', linewidth=0.5)
        #ax.scatter(lons[-1], lats[-1], c='red', s=40, zorder=6,
        #           edgecolors='black', linewidth=0.5)

        # Save the figure without any extra margins
        plt.savefig(output_path, dpi=self.dpi, bbox_inches=None, pad_inches=0)
        plt.close(fig)
        
        
    def _get_paper_bounds_utm(self, section: MapSection):
        w_mm, h_mm = PAPER_SIZES_MM[section.paper_size]
        if section.orientation == "landscape":
            w_mm, h_mm = h_mm, w_mm
        page_width_m = (w_mm / 1000.0) * section.effective_scale
        page_height_m = (h_mm / 1000.0) * section.effective_scale
        cx, cy = section.effective_center_utm
        return (cx - page_width_m/2, cy - page_height_m/2,
                cx + page_width_m/2, cy + page_height_m/2)

    def _utm_to_latlon(self, easting: float, northing: float, ref_lat: float, ref_lon: float):
        lat_deg_per_m = 1 / 111319.9
        lon_deg_per_m = 1 / (111319.9 * math.cos(math.radians(ref_lat)))
        ref_e, ref_n, _ = UTMConverter.latlon_to_utm(ref_lat, ref_lon)
        d_e = easting - ref_e
        d_n = northing - ref_n
        lon = ref_lon + d_e * lon_deg_per_m
        lat = ref_lat + d_n * lat_deg_per_m
        return lon, lat

# ============================================================================
# INTERACTIVE PREVIEW (double-click for scale, drag to move)
# ============================================================================

class SectionSettingsDialog:
    def __init__(self, parent, section_idx, current_scale, current_zoom, callback):
        self.parent = parent
        self.section_idx = section_idx
        self.callback = callback
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"Section {section_idx+1} Settings")
        self.dialog.geometry("300x220")
        self.dialog.transient(parent)
        self.dialog.resizable(False, False)

        # Scale input
        tk.Label(self.dialog, text="Map Scale (1:xxx):").pack(pady=5)
        self.scale_var = tk.StringVar(value=str(current_scale))
        tk.Entry(self.dialog, textvariable=self.scale_var, width=15).pack()

        # Zoom level
        tk.Label(self.dialog, text="Tile Zoom Level (0-20, leave empty for auto):").pack(pady=5)
        self.zoom_var = tk.StringVar(value=str(current_zoom) if current_zoom else "")
        tk.Entry(self.dialog, textvariable=self.zoom_var, width=10).pack()

        # Buttons
        btn_frame = tk.Frame(self.dialog)
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text="Apply", command=self._apply).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=self.dialog.destroy).pack(side=tk.LEFT)

        # Make sure the window is visible before grabbing focus
        self.dialog.update_idletasks()
        self.dialog.after(10, self._set_grab)

    def _set_grab(self):
        """Set grab after the window is viewable."""
        self.dialog.grab_set()
        self.dialog.focus_set()

    def _apply(self):
        try:
            new_scale = int(self.scale_var.get())
            if new_scale <= 0:
                raise ValueError
        except:
            messagebox.showerror("Invalid scale", "Scale must be a positive integer.")
            return
        zoom_str = self.zoom_var.get().strip()
        new_zoom = None
        if zoom_str:
            try:
                new_zoom = int(zoom_str)
                if not (0 <= new_zoom <= 20):
                    raise ValueError
            except:
                messagebox.showerror("Invalid zoom", "Zoom must be an integer between 0 and 20.")
                return
        self.callback(self.section_idx, new_scale, new_zoom)
        self.dialog.destroy()

class DraggableRectangle:
    """
    A rectangle that can be dragged with the mouse.
    Also supports double‑click callback.
    """
    def __init__(self, rect, section_idx, on_double_click, on_drag_end):
        self.rect = rect
        self.section_idx = section_idx
        self.on_double_click = on_double_click
        self.on_drag_end = on_drag_end
        self.press = None
        self.dragging = False
        self.rect.figure.canvas.mpl_connect('button_press_event', self.on_press)
        self.rect.figure.canvas.mpl_connect('button_release_event', self.on_release)
        self.rect.figure.canvas.mpl_connect('motion_notify_event', self.on_motion)
        # For double‑click detection
        self._click_timer = None

    def on_press(self, event):
        if event.inaxes != self.rect.axes:
            return
        contains, _ = self.rect.contains(event)
        if not contains:
            return
        self.press = (event.xdata, event.ydata)
        self.dragging = False
        # Double‑click detection
        if self._click_timer is None:
            self._click_timer = self.rect.figure.canvas.new_timer(interval=300)
            self._click_timer.single_shot = True
            self._click_timer.add_callback(self._clear_timer)
            self._click_timer.start()
        else:
            self._click_timer.stop()
            self._click_timer = None
            self.on_double_click(self.section_idx)

    def _clear_timer(self):
        self._click_timer = None

    def on_motion(self, event):
        if self.press is None:
            return
        if event.inaxes != self.rect.axes:
            return
        dx = event.xdata - self.press[0]
        dy = event.ydata - self.press[1]
        if abs(dx) > 0.005 or abs(dy) > 0.005:
            self.dragging = True
            x0, y0 = self.rect.xy
            self.rect.set_xy((x0 + dx, y0 + dy))
            self.rect.figure.canvas.draw_idle()
            self.press = (event.xdata, event.ydata)

    def on_release(self, event):
        if self.press is not None and self.dragging:
            # Get new rectangle center in lat/lon
            x0, y0 = self.rect.xy
            w = self.rect.get_width()
            h = self.rect.get_height()
            center_lon = x0 + w/2
            center_lat = y0 + h/2
            # Convert to UTM
            easting, northing, _ = UTMConverter.latlon_to_utm(center_lat, center_lon)
            self.on_drag_end(self.section_idx, easting, northing)
        self.press = None
        self.dragging = False




class InteractivePreview:
    def __init__(self, parent_frame, on_click, on_move, tile_source):
        self.parent = parent_frame
        self.on_click = on_click          # double‑click → set custom scale
        self.on_edit = on_click
        self.on_move = on_move            # drag end → move center
        self.tile_source = tile_source

        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.ax = None
        self.sections = []
        self.total_km = 0.0
        self.draggables = []

        self.parent.bind("<Configure>", self._on_resize)
        self.parent.after_idle(self._initial_resize)

    def _initial_resize(self):
        w = self.parent.winfo_width()
        h = self.parent.winfo_height()
        if w > 10 and h > 10:
            self.fig.set_size_inches(w / self.fig.dpi, h / self.fig.dpi)
            self.canvas.draw_idle()

    def _on_resize(self, event):
        if event.widget == self.parent:
            w, h = event.width, event.height
            if w > 10 and h > 10:
                self.fig.set_size_inches(w / self.fig.dpi, h / self.fig.dpi)
                self.canvas.draw_idle()

    def update(self, sections, total_km):
        self.sections = sections
        self.total_km = total_km
        self._draw()

    def _draw(self):
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.ax.set_frame_on(False)
        self.fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

        # Collect all route points
        all_lons = [p.lon for sec in self.sections for p in sec.points]
        all_lats = [p.lat for sec in self.sections for p in sec.points]
        if not all_lons:
            return

        # Compute bounds with 10% padding
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

        # Add basemap with auto zoom
        try:
            ctx.add_basemap(self.ax, crs='EPSG:4326', source=self.tile_source, zoom='auto')
        except:
            self.ax.set_facecolor('#e0e0e0')

        # Draw route line
        self.ax.plot(all_lons, all_lats, 'r-', linewidth=2, label='Route')

        # --- NEW: Mark split points (first point of each section after the first) ---
        for i, sec in enumerate(self.sections):
            if i > 0:
                first_pt = sec.points[0]
                self.ax.plot(first_pt.lon, first_pt.lat, 'rx', markersize=8, markeredgewidth=2, zorder=6)

        # Draw draggable rectangles and labels
        self.draggables = []
        for i, sec in enumerate(self.sections):
            # Compute rectangle in lat/lon using effective center and scale
            cx_utm, cy_utm = sec.effective_center_utm
            ref = sec.points[0]
            center_lon, center_lat = self._utm_to_latlon(cx_utm, cy_utm, ref.lat, ref.lon)

            w_mm, h_mm = PAPER_SIZES_MM[sec.paper_size]
            if sec.orientation == "landscape":
                w_mm, h_mm = h_mm, w_mm
            half_w_m = (w_mm / 1000.0) * sec.effective_scale / 2
            half_h_m = (h_mm / 1000.0) * sec.effective_scale / 2

            lat_deg_per_m = 1 / 111319.9
            lon_deg_per_m = 1 / (111319.9 * math.cos(math.radians(ref.lat)))
            lon_min = center_lon - half_w_m * lon_deg_per_m
            lon_max = center_lon + half_w_m * lon_deg_per_m
            lat_min = center_lat - half_h_m * lat_deg_per_m
            lat_max = center_lat + half_h_m * lat_deg_per_m

            rect = Rectangle((lon_min, lat_min), lon_max - lon_min, lat_max - lat_min,
                             fill=False, edgecolor='blue', linewidth=2, alpha=0.7)
            self.ax.add_patch(rect)
            self.draggables.append(DraggableRectangle(rect, i, self.on_edit, self.on_move))

            # Multi-line label: page number, scale, zoom info
            label_text = f"{i+1}\n1:{sec.effective_scale:,}"
            if sec.custom_zoom:
                label_text += f"\nz{sec.custom_zoom}"
            else:
                label_text += "\nz(auto)"
            self.ax.text((lon_min + lon_max)/2, (lat_min + lat_max)/2, label_text,
                         ha='center', va='center', fontsize=9,
                         bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

        self.ax.set_aspect('equal', adjustable='box')
        self.canvas.draw()
        
    
    def _utm_to_latlon(self, easting, northing, ref_lat, ref_lon):
        lat_deg_per_m = 1 / 111319.9
        lon_deg_per_m = 1 / (111319.9 * math.cos(math.radians(ref_lat)))
        ref_e, ref_n, _ = UTMConverter.latlon_to_utm(ref_lat, ref_lon)
        d_e = easting - ref_e
        d_n = northing - ref_n
        lon = ref_lon + d_e * lon_deg_per_m
        lat = ref_lat + d_n * lat_deg_per_m
        return lon, lat
# ============================================================================
# MAIN GUI APPLICATION
# ============================================================================
class BikeMapAtlasApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Bike Map Atlas Generator")
        self.root.geometry("1200x800")

        self.points = []
        self.total_km = 0
        self.sections: List[MapSection] = []
        self.global_scale = 100000
        self.paper_size = "A4"
        self.overlap_m = 500
        self.dpi = 300
        self.output_path = None
        self.split_by = tk.StringVar(value="none")
        
        self._setup_gui()
        self.tile_source = TILE_PROVIDERS[self.tile_var.get()]

        self.preview = InteractivePreview(self.preview_frame,
                                  self._edit_section_properties,
                                  self._on_section_move,
                                  self.tile_source)
                                  
                                  
        
    def _setup_gui(self):
        left = ttk.Frame(self.root, width=300)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        # File
        f_frame = ttk.LabelFrame(left, text="GPX File", padding=5)
        f_frame.pack(fill='x', pady=5)
        self.gpx_label = ttk.Label(f_frame, text="No file loaded", wraplength=250)
        self.gpx_label.pack()
        self.load_btn = ttk.Button(f_frame, text="Load GPX", command=self._load_gpx)
        self.load_btn.pack(pady=2)

        # Settings
        s_frame = ttk.LabelFrame(left, text="Global Settings", padding=5)
        s_frame.pack(fill='x', pady=5)
    
        ttk.Label(s_frame, text="Paper Size:").grid(row=0, column=0, sticky='w')
        self.paper_var = tk.StringVar(value="A4")
        cb_paper = ttk.Combobox(s_frame, textvariable=self.paper_var, values=list(PAPER_SIZES_MM.keys()), state='readonly')
        cb_paper.grid(row=0, column=1, sticky='w')
        cb_paper.bind('<<ComboboxSelected>>', lambda e: self._update_preview())

        ttk.Label(s_frame, text="Global Scale:").grid(row=1, column=0, sticky='w', pady=2)
        self.scale_var = tk.StringVar(value="1:25,000 (Regional)")
        cb_scale = ttk.Combobox(s_frame, textvariable=self.scale_var, values=list(MAP_SCALES.keys()), state='readonly')
        cb_scale.grid(row=1, column=1, sticky='w')
        cb_scale.bind('<<ComboboxSelected>>', lambda e: self._update_preview())
        
        
        ttk.Label(s_frame, text="Tile Server:").grid(row=4, column=0, sticky='w', pady=2)
        self.tile_var = tk.StringVar(value="OpenStreetMap (default)")
        cb_tile = ttk.Combobox(s_frame, textvariable=self.tile_var,
                               values=list(TILE_PROVIDERS.keys()), state='readonly')
        cb_tile.grid(row=4, column=1, sticky='w')
        cb_tile.bind('<<ComboboxSelected>>', lambda e: self._on_tile_changed())

        ttk.Label(s_frame, text="Overlap (m):").grid(row=2, column=0, sticky='w', pady=2)
        self.overlap_var = tk.StringVar(value="500")
        sp_overlap = tk.Spinbox(s_frame, from_=0, to=2000, increment=100, textvariable=self.overlap_var, width=8)
        sp_overlap.grid(row=2, column=1, sticky='w')
        sp_overlap.bind('<KeyRelease>', lambda e: self._update_preview())

        ttk.Label(s_frame, text="DPI (print):").grid(row=3, column=0, sticky='w', pady=2)
        self.dpi_var = tk.StringVar(value="300")
        sp_dpi = tk.Spinbox(s_frame, from_=150, to=1200, increment=150, textvariable=self.dpi_var, width=8)
        sp_dpi.grid(row=3, column=1, sticky='w')

        # Split options
        split_frame = ttk.LabelFrame(left, text="PDF Split Options", padding=5)
        split_frame.pack(fill='x', pady=5)
        ttk.Label(split_frame, text="Split by:").grid(row=0, column=0, sticky='w')
        self.split_by = tk.StringVar(value="none")
        rb_none = ttk.Radiobutton(split_frame, text="No split", variable=self.split_by, value="none")
        rb_none.grid(row=0, column=1, sticky='w')
        rb_km = ttk.Radiobutton(split_frame, text="Kilometers per file", variable=self.split_by, value="km")
        rb_km.grid(row=1, column=0, sticky='w')
        self.split_km_entry = ttk.Entry(split_frame, width=8, state='disabled')
        self.split_km_entry.grid(row=1, column=1, sticky='w')
        rb_size = ttk.Radiobutton(split_frame, text="File size (MB) per file", variable=self.split_by, value="filesize")
        rb_size.grid(row=2, column=0, sticky='w')
        self.split_size_entry = ttk.Entry(split_frame, width=8, state='disabled')
        self.split_size_entry.grid(row=2, column=1, sticky='w')
        rb_km.bind('<Button-1>', lambda e: self.split_km_entry.config(state='normal'))
        rb_size.bind('<Button-1>', lambda e: self.split_size_entry.config(state='normal'))
        rb_none.bind('<Button-1>', lambda e: (self.split_km_entry.config(state='disabled'), self.split_size_entry.config(state='disabled')))

        # Output
        o_frame = ttk.LabelFrame(left, text="Output", padding=5)
        o_frame.pack(fill='x', pady=5)
        self.output_label = ttk.Label(o_frame, text="No output selected", wraplength=250)
        self.output_label.pack()
        btn_frame = ttk.Frame(o_frame)
        btn_frame.pack(fill='x', pady=2)
        self.select_btn = ttk.Button(btn_frame, text="Select PDF", command=self._select_output)
        self.select_btn.pack(side=tk.LEFT, padx=2)
        self.generate_btn = ttk.Button(btn_frame, text="Generate PDF", command=self._generate_pdf)
        self.generate_btn.pack(side=tk.LEFT, padx=2)

        self.status = ttk.Label(left, text="Ready", relief=tk.SUNKEN)
        self.status.pack(fill='x', pady=5)
        self.progress = ttk.Progressbar(left, mode='indeterminate')
        self.progress.pack(fill='x', pady=5)

        # Right panel: preview
        right = ttk.Frame(self.root)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.info = ttk.Label(right, text="Load a GPX file to see interactive map")
        self.info.pack()
        self.preview_frame = ttk.Frame(right)
        self.preview_frame.pack(fill=tk.BOTH, expand=True)

    def _on_section_click(self, idx):
        self._edit_section_properties(self, idx)

    def _on_section_move(self, idx, easting, northing):
        self._set_custom_center(idx, easting, northing)
        
    def _on_tile_changed(self):
        self.tile_source = TILE_PROVIDERS[self.tile_var.get()]
        self._update_preview()
        
        
    def _edit_section_properties(self, section_idx):
        sec = self.sections[section_idx]
        current_scale = sec.custom_scale if sec.custom_scale is not None else sec.global_scale
        current_zoom = sec.custom_zoom if hasattr(sec, 'custom_zoom') else None
        SectionSettingsDialog(self.root, section_idx, current_scale, current_zoom,
                              self._apply_section_settings)

    def _apply_section_settings(self, section_idx, new_scale, new_zoom):
        sec = self.sections[section_idx]
        sec.custom_scale = new_scale
        sec.custom_zoom = new_zoom
        self.preview.update(self.sections, self.total_km)
        self.status.config(text=f"Section {section_idx+1} updated: scale 1:{new_scale}, zoom {new_zoom if new_zoom else 'auto'}")
    
    
    def _set_custom_scale(self, section_idx):
        current = self.sections[section_idx].custom_scale
        default = self.global_scale
        result = simpledialog.askstring("Custom Scale",
                                         f"Enter custom scale denominator for section {section_idx+1}\n(leave empty for global {default}):",
                                         initialvalue=str(current) if current else "")
        if result is not None:
            if result.strip() == "":
                self.sections[section_idx].custom_scale = None
            else:
                try:
                    new_scale = int(result)
                    if new_scale > 0:
                        self.sections[section_idx].custom_scale = new_scale
                except:
                    messagebox.showerror("Invalid scale", "Scale must be a positive integer.")
        self.preview.update(self.sections, self.total_km)

    def _set_custom_center(self, section_idx, easting, northing):
        self.sections[section_idx].set_custom_center(easting, northing)
        self.preview.update(self.sections, self.total_km)
        self.status.config(text=f"Section {section_idx+1} moved")

    def _load_gpx(self):
        path = filedialog.askopenfilename(filetypes=[("GPX", "*.gpx")])
        if not path:
            return
        self.gpx_label.config(text=os.path.basename(path))
        default_output = Path(path).stem + "_maps.pdf"
        self.output_path = str(Path(path).parent / default_output)
        self.output_label.config(text=os.path.basename(self.output_path))

        self.status.config(text="Loading GPX...")
        self.progress.start()
        self._set_buttons_state('disabled')

        def thread_load():
            try:
                points, total_km = GPXParser.parse(path)
                self.root.after(0, self._on_gpx_loaded, points, total_km)
            except Exception as e:
                self.root.after(0, self._on_load_error, str(e))

        threading.Thread(target=thread_load, daemon=True).start()

    def _on_gpx_loaded(self, points, total_km):
        self.points = points
        self.total_km = total_km
        self.progress.stop()
        self.status.config(text=f"Loaded: {self.total_km:.1f} km, {len(self.points)} points")
        self._set_buttons_state('normal')
        self._update_preview()

    def _on_load_error(self, error_msg):
        self.progress.stop()
        self.status.config(text="Load failed")
        self._set_buttons_state('normal')
        messagebox.showerror("Error", error_msg)

    def _update_preview(self):
        if not self.points:
            return
        self.status.config(text="Computing sections...")
        self.progress.start()
        self._set_buttons_state('disabled')

        def thread_preview():
            try:
                self.global_scale = MAP_SCALES[self.scale_var.get()]
                self.paper_size = self.paper_var.get()
                self.overlap_m = float(self.overlap_var.get())
                splitter = RouteSplitter(self.points, self.global_scale, self.paper_size, self.overlap_m)
                sections = splitter.split()
                # Preserve custom settings
                if hasattr(self, 'sections') and self.sections:
                    for old in self.sections:
                        for new in sections:
                            if new.index == old.index:
                                new.custom_scale = old.custom_scale
                                new.custom_center_utm = old.custom_center_utm
                self.root.after(0, self._display_preview, sections)
            except Exception as e:
                self.root.after(0, self._on_preview_error, str(e))

        threading.Thread(target=thread_preview, daemon=True).start()

    def _display_preview(self, sections):
        self.sections = sections
        self.preview.update(self.sections, self.total_km)
        self.info.config(text=f"{len(self.sections)} pages (double-click to set scale, drag to move)")
        self.status.config(text=f"Ready - {len(self.sections)} pages")
        self.progress.stop()
        self._set_buttons_state('normal')

    def _on_preview_error(self, error_msg):
        self.progress.stop()
        self.status.config(text=f"Preview error: {error_msg}")
        self._set_buttons_state('normal')
        messagebox.showerror("Preview Error", error_msg)

    def _select_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if path:
            self.output_path = path
            self.output_label.config(text=os.path.basename(path))

    def _generate_pdf(self):
        if not self.points:
            messagebox.showwarning("No data", "Load a GPX file first")
            return
        if not self.output_path:
            messagebox.showwarning("No output", "Select an output PDF file")
            return

        self.status.config(text="Generating PDF...")
        self.progress.start()
        self._set_buttons_state('disabled')

        def thread_generate():
            try:
                self.global_scale = MAP_SCALES[self.scale_var.get()]
                self.paper_size = self.paper_var.get()
                self.overlap_m = float(self.overlap_var.get())
                self.dpi = int(self.dpi_var.get())

                splitter = RouteSplitter(self.points, self.global_scale, self.paper_size, self.overlap_m)
                new_sections = splitter.split()
                # Transfer custom settings
                for new in new_sections:
                    for old in self.sections:
                        if old.index == new.index:
                            new.custom_scale = old.custom_scale
                            new.custom_center_utm = old.custom_center_utm
                            break
                self.sections = new_sections

                renderer = MapRenderer(global_scale=self.global_scale, dpi=self.dpi,
                       tile_source=self.tile_source)
                self.map_renderer = renderer

                with tempfile.TemporaryDirectory(prefix="bike_atlas_") as tmpdir:
                    tmp = Path(tmpdir)
                    # Overview page
                    self.root.after(0, self._update_status, "Generating overview map...")
                    overview = tmp / "overview.pdf"
                    renderer.render_overview(self.sections, self.total_km, str(overview))
                    pdfs = [overview]

                    for i, sec in enumerate(self.sections):
                        self.root.after(0, self._update_status, f"Rendering section {i+1}/{len(self.sections)}...")
                        sec_pdf = tmp / f"section_{i:04d}.pdf"
                        renderer.render_section(sec, str(sec_pdf))
                        pdfs.append(sec_pdf)

                    # Split or merge
                    split_mode = self.split_by.get()
                    if split_mode == "km":
                        km_val = float(self.split_km_entry.get() or 0)
                        if km_val > 0:
                            self._split_by_km(pdfs, km_val)
                        else:
                            self._merge_pdfs(pdfs, self.output_path)
                    elif split_mode == "filesize":
                        mb_val = float(self.split_size_entry.get() or 0)
                        if mb_val > 0:
                            self._split_by_filesize(pdfs, mb_val)
                        else:
                            self._merge_pdfs(pdfs, self.output_path)
                    else:
                        self._merge_pdfs(pdfs, self.output_path)

                self.root.after(0, self._generation_done, True, f"PDF generated: {len(self.sections)} pages")
            except Exception as e:
                self.root.after(0, self._generation_done, False, str(e))

        threading.Thread(target=thread_generate, daemon=True).start()

    def _merge_pdfs(self, pdf_list, out_path):
        merger = PyPDF2.PdfMerger()
        for p in pdf_list:
            merger.append(str(p))
        merger.write(out_path)
        merger.close()

    def _split_by_km(self, pdf_list: list, km_per_file: float):
        """Split by kilometers, each part gets its own overview (first part also gets full overview)."""
        output_base = Path(self.output_path).stem
        output_dir = Path(self.output_path).parent
        full_overview = pdf_list[0]          # global overview PDF path
        section_pdfs = pdf_list[1:]          # list of section PDFs (in order)

        # Group sections into parts by accumulated km
        parts = []          # each element: list of MapSection objects
        current_part_sections = []
        current_km = 0.0
        for i, sec in enumerate(self.sections):
            current_km += (sec.end_m - sec.start_m) / 1000.0
            current_part_sections.append(sec)
            if current_km >= km_per_file:
                parts.append(current_part_sections)
                current_part_sections = []
                current_km = 0.0
        if current_part_sections:
            parts.append(current_part_sections)

        # Generate a PDF file for each part
        part_start_idx = 0  # index in section_pdfs
        for part_idx, part_sections in enumerate(parts):
            part_num = part_idx + 1
            out_file = output_dir / f"{output_base}_part{part_num}.pdf"
            part_pdfs = []

            # First part includes the full overview
            if part_num == 1:
                part_pdfs.append(full_overview)

            # Part‑specific overview
            part_overview = output_dir / f"temp_part{part_num}_overview.pdf"
            part_total_km = sum((sec.end_m - sec.start_m)/1000.0 for sec in part_sections)
            self.map_renderer.render_part_overview(part_sections, part_total_km, str(part_overview))
            part_pdfs.append(part_overview)

            # Add the section PDFs belonging to this part
            num_sections = len(part_sections)
            part_pdfs.extend(section_pdfs[part_start_idx:part_start_idx + num_sections])
            part_start_idx += num_sections

            # Merge and save
            self._merge_pdfs(part_pdfs, str(out_file))
            os.unlink(part_overview)

        messagebox.showinfo("Split", f"Split into {len(parts)} files by {km_per_file} km")

    def _split_by_filesize(self, pdf_list: list, max_mb: float):
        """Split by file size, each part gets its own overview (first part also gets full overview)."""
        output_base = Path(self.output_path).stem
        output_dir = Path(self.output_path).parent
        full_overview = pdf_list[0]
        section_pdfs = pdf_list[1:]

        # Group sections by accumulated file size (use PDF sizes)
        parts = []
        current_part_sections = []
        current_size = 0.0
        part_start_idx = 0
        for i, (sec, pdf_path) in enumerate(zip(self.sections, section_pdfs)):
            size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
            if current_size + size_mb > max_mb and current_part_sections:
                parts.append(current_part_sections)
                current_part_sections = []
                current_size = 0.0
            current_part_sections.append(sec)
            current_size += size_mb
        if current_part_sections:
            parts.append(current_part_sections)

        # Generate files for each part
        part_start_idx = 0
        for part_idx, part_sections in enumerate(parts):
            part_num = part_idx + 1
            out_file = output_dir / f"{output_base}_part{part_num}.pdf"
            part_pdfs = []
            if part_num == 1:
                part_pdfs.append(full_overview)
            part_overview = output_dir / f"temp_part{part_num}_overview.pdf"
            part_total_km = sum((sec.end_m - sec.start_m)/1000.0 for sec in part_sections)
            self.map_renderer.render_part_overview(part_sections, part_total_km, str(part_overview))
            part_pdfs.append(part_overview)
            # Add the section PDFs
            num_sections = len(part_sections)
            part_pdfs.extend(section_pdfs[part_start_idx:part_start_idx + num_sections])
            part_start_idx += num_sections
            self._merge_pdfs(part_pdfs, str(out_file))
            os.unlink(part_overview)

        messagebox.showinfo("Split", f"Split into {len(parts)} files by size")

    def _update_status(self, msg):
        self.status.config(text=msg)

    def _generation_done(self, success, msg):
        self.progress.stop()
        self._set_buttons_state('normal')
        if success:
            messagebox.showinfo("Success", msg)
            self.status.config(text="PDF generated")
        else:
            messagebox.showerror("Error", msg)
            self.status.config(text="Generation failed")

    def _set_buttons_state(self, state):
        self.load_btn.config(state=state)
        self.select_btn.config(state=state)
        self.generate_btn.config(state=state)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = BikeMapAtlasApp()
    app.run()
