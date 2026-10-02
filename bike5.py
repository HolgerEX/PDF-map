#!/usr/bin/env python3
"""
Bike Map Atlas Generator - Final (No tkintermapview)
- PaperMap for all maps (overview + sections)
- Interactive preview with Matplotlib (click/drag sections)
- PDF overview page with route + page bounds
- Correct path overlay on section pages
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
import threading
import math
from dataclasses import dataclass
from typing import List, Tuple, Optional
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import contextily as ctx
import gpxpy
import gpxpy.gpx
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import PyPDF2
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPDF

#import mapnik
import simplekml
import tempfile
import os

import subprocess
import gpxpy.gpx
from typing import List, Tuple
# ============================================================================
# PAPER SIZES
# ============================================================================

PAPER_SIZES_MM = {
    'A3': (420, 297),
    'A4': (297, 210),
    'A5': (210, 148),
    'A6': (148, 105),
    'Letter': (279, 216)
}

MAP_SCALES = {
    '1:5,000 (City)': 5000,
    '1:10,000 (Town)': 10000,
    '1:25,000 (Regional)': 25000,
    '1:50,000 (Country)': 50000,
    '1:100,000 (Long distance)': 100000
}

TILE_PROVIDERS = {
    "OpenStreetMap": "openstreetmap",
    "CyclOSM (bike)": "cyclosm",
    "Humanitarian": "hot",
    "Stamen Toner": "stamen_toner",
    "Stamen Terrain": "stamen_terrain"
}

# ============================================================================
# UTM CONVERSION
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
    bounds_utm: Tuple[float, float, float, float]   # (xmin, ymin, xmax, ymax)
    start_m: float
    end_m: float
    center_utm: Tuple[float, float]
    orientation: str
    paper_size: str
    global_scale: int
    custom_scale: Optional[int] = None
    custom_center_utm: Optional[Tuple[float, float]] = None

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

class RouteSplitter:
    def __init__(self, points: List[RoutePoint], global_scale: int, paper_size: str, overlap_m: float = 500):
        self.points = points
        self.global_scale = global_scale
        self.paper_size = paper_size
        self.overlap_m = overlap_m
        self.total_m = points[-1].distance_m
        w_mm, h_mm = PAPER_SIZES_MM[paper_size]
        self.page_width_m = (w_mm / 1000.0) * global_scale
        self.page_height_m = (h_mm / 1000.0) * global_scale
        self.min_page_length_m = self.page_width_m * 0.1

    def split(self) -> List[MapSection]:
        if self.total_m <= self.page_width_m:
            return [self._create_section(0, len(self.points)-1, 0)]

        sections = []
        start_m = 0.0
        max_iter = 1000
        iter_count = 0

        while start_m < self.total_m - self.min_page_length_m and iter_count < max_iter:
            iter_count += 1
            end_m = min(start_m + self.page_width_m, self.total_m)
            start_idx = self._find_index_at_distance(start_m)
            end_idx = self._find_index_at_distance(end_m)

            if start_idx >= len(self.points) or end_idx <= start_idx:
                break

            sections.append(self._create_section(start_idx, end_idx, len(sections)))

            next_start = end_m - self.overlap_m
            if next_start <= start_m + self.min_page_length_m:
                next_start = start_m + self.page_width_m * 0.5
            start_m = next_start
            if start_m >= self.total_m - self.min_page_length_m:
                break

        # ensure last section
        if sections and sections[-1].end_m < self.total_m - self.min_page_length_m:
            last_start = max(sections[-1].start_m, self.total_m - self.page_width_m)
            start_idx = self._find_index_at_distance(last_start)
            end_idx = len(self.points) - 1
            if end_idx > start_idx:
                final = self._create_section(start_idx, end_idx, len(sections))
                sections.append(final)

        return sections

    def _find_index_at_distance(self, dist_m: float) -> int:
        lo, hi = 0, len(self.points)-1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.points[mid].distance_m < dist_m:
                lo = mid + 1
            else:
                hi = mid - 1
        return max(0, lo)

    def _get_orientation(self, pts: List[RoutePoint]) -> str:
        min_x = min(p.easting for p in pts)
        max_x = max(p.easting for p in pts)
        min_y = min(p.northing for p in pts)
        max_y = max(p.northing for p in pts)
        return "portrait" if (max_y - min_y) > (max_x - min_x) else "landscape"

    def _get_page_size_meters(self, orientation: str) -> Tuple[float, float]:
        w_mm, h_mm = PAPER_SIZES_MM[self.paper_size]
        if orientation == "landscape":
            w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
        return (w_mm / 1000.0) * self.global_scale, (h_mm / 1000.0) * self.global_scale

    def _create_section(self, start_idx: int, end_idx: int, idx: int) -> MapSection:
        pts = self.points[start_idx:end_idx+1]
        min_x = min(p.easting for p in pts)
        max_x = max(p.easting for p in pts)
        min_y = min(p.northing for p in pts)
        max_y = max(p.northing for p in pts)
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0
        orientation = self._get_orientation(pts)
        pw, ph = self._get_page_size_meters(orientation)
        bounds_utm = (cx - pw/2, cy - ph/2, cx + pw/2, cy + ph/2)
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

# ============================================================================
# PAPERMAP RENDERER (with SVG overlay)
# ============================================================================


import subprocess
import tempfile
import os
import math
import gpxpy
import gpxpy.gpx
from typing import List

class MapnikCLIRenderer:
    def __init__(self, global_scale: int, dpi: int = 300, style_file: str = "mapnik_style.xml"):
        self.global_scale = global_scale
        self.dpi = dpi
        self.style_file = style_file

    # ----------------------------------------------------------------------
    # Section rendering (one page)
    # ----------------------------------------------------------------------
    def render_section(self, section: 'MapSection', output_path: str):
        """Render a single section using hm-render-mapnik (bbox mode)."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.gpx', delete=False) as f:
            self._write_gpx([section], f.name)
            gpx_path = f.name
        try:
            # Compute page bounds in lat/lon from UTM
            xmin, ymin, xmax, ymax = self._get_paper_bounds_utm(section)
            ref = section.points[0]
            lon_min, lat_min = self._utm_to_latlon(xmin, ymin, ref.lat, ref.lon)
            lon_max, lat_max = self._utm_to_latlon(xmax, ymax, ref.lat, ref.lon)

            w_mm, h_mm = PAPER_SIZES_MM[section.paper_size]
            if section.orientation == "landscape":
                w_mm, h_mm = h_mm, w_mm
            page_width_cm = w_mm / 10.0
            page_height_cm = h_mm / 10.0

            cmd = [
                "hm-render-mapnik",
                "--pagewidth", str(page_width_cm),
                "--pageheight", str(page_height_cm),
                "--dpi", str(self.dpi),
                "--mapstyle", self.style_file,
                "-b", output_path.replace('.pdf', ''),  # output base name (no extension)
                gpx_path,
                "bbox",
                str(lon_min), str(lat_min), str(lon_max), str(lat_max)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            # hm-render-mapnik appends '.pdf' to the base name
            expected = output_path.replace('.pdf', '') + '.pdf'
            if expected != output_path and os.path.exists(expected):
                os.rename(expected, output_path)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"hm-render-mapnik section failed: {e.stderr.decode()}")
        finally:
            os.unlink(gpx_path)

    # ----------------------------------------------------------------------
    # Overview rendering (whole route)
    # ----------------------------------------------------------------------
    def render_overview(self, sections: List['MapSection'], total_km: float, output_path: str):
        """Render an overview map showing the whole route."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.gpx', delete=False) as f:
            self._write_gpx(sections, f.name)
            gpx_path = f.name
        try:
            # Compute overall lat/lon bounds from all points
            all_lons = [p.lon for sec in sections for p in sec.points]
            all_lats = [p.lat for sec in sections for p in sec.points]
            lon_min, lon_max = min(all_lons), max(all_lons)
            lat_min, lat_max = min(all_lats), max(all_lats)
            # Add 10% padding
            lon_pad = (lon_max - lon_min) * 0.1
            lat_pad = (lat_max - lat_min) * 0.1
            lon_min -= lon_pad
            lon_max += lon_pad
            lat_min -= lat_pad
            lat_max += lat_pad

            # Overview on A3 landscape
            page_width_cm = 42.0
            page_height_cm = 29.7

            cmd = [
                "hm-render-mapnik",
                "--pagewidth", str(page_width_cm),
                "--pageheight", str(page_height_cm),
                "--dpi", str(self.dpi),
                "--mapstyle", self.style_file,
                "-b", output_path.replace('.pdf', ''),
                gpx_path,
                "bbox",
                str(lon_min), str(lat_min), str(lon_max), str(lat_max)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            expected = output_path.replace('.pdf', '') + '.pdf'
            if expected != output_path and os.path.exists(expected):
                os.rename(expected, output_path)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"hm-render-mapnik overview failed: {e.stderr.decode()}")
        finally:
            os.unlink(gpx_path)

    # ----------------------------------------------------------------------
    # Helper: compute page bounds in UTM meters
    # ----------------------------------------------------------------------
    def _get_paper_bounds_utm(self, section: 'MapSection'):
        w_mm, h_mm = PAPER_SIZES_MM[section.paper_size]
        if section.orientation == "landscape":
            w_mm, h_mm = h_mm, w_mm
        page_width_m = (w_mm / 1000.0) * section.effective_scale
        page_height_m = (h_mm / 1000.0) * section.effective_scale
        cx, cy = section.effective_center_utm
        return (cx - page_width_m/2, cy - page_height_m/2,
                cx + page_width_m/2, cy + page_height_m/2)

    # ----------------------------------------------------------------------
    # Helper: write GPX from sections
    # ----------------------------------------------------------------------
    def _write_gpx(self, sections: List['MapSection'], filepath: str):
        gpx = gpxpy.gpx.GPX()
        track = gpxpy.gpx.GPXTrack()
        gpx.tracks.append(track)
        segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(segment)
        for sec in sections:
            for p in sec.points:
                segment.points.append(gpxpy.gpx.GPXTrackPoint(p.lat, p.lon))
        with open(filepath, 'w') as f:
            f.write(gpx.to_xml())

    # ----------------------------------------------------------------------
    # UTM to lat/lon (approximate but sufficient)
    # ----------------------------------------------------------------------
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
# INTERACTIVE PREVIEW (Matplotlib)
# ============================================================================
from matplotlib.widgets import RectangleSelector

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
    def __init__(self, parent_frame, on_click, on_move):
        self.parent = parent_frame
        self.on_click = on_click
        self.on_move = on_move
        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.ax = None
        self.sections = []
        self.total_km = 0.0
        self.draggables = []

    def update(self, sections: List[MapSection], total_km: float):
        self.sections = sections
        self.total_km = total_km
        self._draw()

    def _draw(self):
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)

        # Route line
        all_lons = [p.lon for sec in self.sections for p in sec.points]
        all_lats = [p.lat for sec in self.sections for p in sec.points]
        self.ax.plot(all_lons, all_lats, 'r-', linewidth=2, label='Route')

        # Basemap
        try:
            ctx.add_basemap(self.ax, crs='EPSG:4326', source=ctx.providers.OpenStreetMap.Mapnik, zoom=10)
        except:
            self.ax.set_facecolor('#e0e0e0')

        self.draggables = []
        for i, sec in enumerate(self.sections):
            # Use effective center (custom if set, else original)
            cx_utm, cy_utm = sec.effective_center_utm
            ref = sec.points[0]
            center_lon, center_lat = self._utm_to_latlon(cx_utm, cy_utm, ref.lat, ref.lon)

            # Page dimensions in meters at effective scale
            w_mm, h_mm = PAPER_SIZES_MM[sec.paper_size]
            if sec.orientation == "landscape":
                w_mm, h_mm = h_mm, w_mm
            half_w_m = (w_mm / 1000.0) * sec.effective_scale / 2
            half_h_m = (h_mm / 1000.0) * sec.effective_scale / 2

            # Convert to degrees
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
                edgecolor='blue',
                linewidth=2,
                alpha=0.7,
                picker=False
            )
            self.ax.add_patch(rect)

            draggable = DraggableRectangle(
                rect, i,
                on_double_click=self.on_click,
                on_drag_end=self.on_move
            )
            self.draggables.append(draggable)

            label = f"{i+1}"
            if sec.custom_scale:
                label += f"\n1:{sec.custom_scale//1000}k"
            self.ax.text(
                (lon_min + lon_max)/2,
                (lat_min + lat_max)/2,
                label,
                ha='center', va='center',
                fontsize=9,
                bbox=dict(boxstyle='circle', facecolor='white', alpha=0.8)
            )

        self.ax.set_title(f'Route Overview - {self.total_km:.1f} km (double‑click box to set scale, drag to move)')
        self.ax.set_xlabel('Longitude')
        self.ax.set_ylabel('Latitude')
        self.ax.legend()
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
        self.root.title("Bike Map Atlas Generator - PaperMap only")
        self.root.geometry("1200x800")

        self.points = []
        self.total_km = 0
        self.sections: List[MapSection] = []
        self.global_scale = 25000
        self.paper_size = "A4"
        self.overlap_m = 500
        self.dpi = 300
        self.tile_provider = "openstreetmap"
        self.output_path = None
        self.split_by = tk.StringVar(value="none")

        self._setup_gui()
        self.preview = InteractivePreview(self.preview_frame, self._on_click_section, self._on_move_section)

    def _setup_gui(self):
        left = ttk.Frame(self.root, width=300)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        f_frame = ttk.LabelFrame(left, text="GPX File", padding=5)
        f_frame.pack(fill='x', pady=5)
        self.gpx_label = ttk.Label(f_frame, text="No file loaded", wraplength=250)
        self.gpx_label.pack()
        self.load_btn = ttk.Button(f_frame, text="Load GPX", command=self._load_gpx)
        self.load_btn.pack(pady=2)

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

        ttk.Label(s_frame, text="Overlap (m):").grid(row=2, column=0, sticky='w', pady=2)
        self.overlap_var = tk.StringVar(value="500")
        sp_overlap = tk.Spinbox(s_frame, from_=0, to=2000, increment=100, textvariable=self.overlap_var, width=8)
        sp_overlap.grid(row=2, column=1, sticky='w')
        sp_overlap.bind('<KeyRelease>', lambda e: self._update_preview())

        ttk.Label(s_frame, text="DPI (print):").grid(row=3, column=0, sticky='w', pady=2)
        self.dpi_var = tk.StringVar(value="300")
        sp_dpi = tk.Spinbox(s_frame, from_=150, to=1200, increment=150, textvariable=self.dpi_var, width=8)
        sp_dpi.grid(row=3, column=1, sticky='w')

        ttk.Label(s_frame, text="Tile Server:").grid(row=4, column=0, sticky='w', pady=2)
        self.tile_var = tk.StringVar(value="OpenStreetMap")
        cb_tile = ttk.Combobox(s_frame, textvariable=self.tile_var, values=list(TILE_PROVIDERS.keys()), state='readonly')
        cb_tile.grid(row=4, column=1, sticky='w')
        cb_tile.bind('<<ComboboxSelected>>', lambda e: self._update_preview())

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

        right = ttk.Frame(self.root)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.info = ttk.Label(right, text="Load a GPX file to see interactive map")
        self.info.pack()
        self.preview_frame = ttk.Frame(right)
        self.preview_frame.pack(fill=tk.BOTH, expand=True)

    def _on_click_section(self, idx):
        self._set_custom_scale(idx)

    def _on_move_section(self, idx, easting, northing):
        self._set_custom_center(idx, easting, northing)

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
                    else:
                        raise ValueError
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
        self.gpx_path = path
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
                self.tile_provider = TILE_PROVIDERS[self.tile_var.get()]
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
        self.info.config(text=f"{len(self.sections)} pages (click box to set scale, drag to move)")
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
                self.tile_provider = TILE_PROVIDERS[self.tile_var.get()]

                splitter = RouteSplitter(self.points, self.global_scale, self.paper_size, self.overlap_m)
                new_sections = splitter.split()
                for new in new_sections:
                    for old in self.sections:
                        if old.index == new.index:
                            new.custom_scale = old.custom_scale
                            new.custom_center_utm = old.custom_center_utm
                            break
                self.sections = new_sections

                #renderer = PaperMapRenderer(self.global_scale, self.dpi, self.tile_provider)

                #renderer = MapnikRenderer(global_scale=self.global_scale, dpi=self.dpi)
                renderer = MapnikCLIRenderer(global_scale=self.global_scale, dpi=self.dpi)
                
                with tempfile.TemporaryDirectory(prefix="bike_atlas_") as tmpdir:
                    tmp = Path(tmpdir)
                    self.root.after(0, self._update_status, "Generating overview map...")
                    overview = tmp / "overview.pdf"
                    renderer.render_overview(self.sections, self.total_km, str(overview))
                    pdfs = [overview]

                    for i, sec in enumerate(self.sections):
                        self.root.after(0, self._update_status, f"Rendering section {i+1}/{len(self.sections)}...")
                        sec_pdf = tmp / f"section_{i:04d}.pdf"
                        renderer.render_section(sec, str(sec_pdf))
                        pdfs.append(sec_pdf)

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

    def _split_by_km(self, pdf_list, km_per_file):
        base = Path(self.output_path).stem
        out_dir = Path(self.output_path).parent
        cur_pdfs = []
        cur_km = 0
        file_num = 1
        for i, pdf in enumerate(pdf_list):
            if i == 0:  # overview always first
                cur_pdfs.append(pdf)
                continue
            sec = self.sections[i-1]
            cur_km += (sec.end_m - sec.start_m) / 1000.0
            cur_pdfs.append(pdf)
            if cur_km >= km_per_file:
                out_file = out_dir / f"{base}_part{file_num}.pdf"
                self._merge_pdfs(cur_pdfs, str(out_file))
                file_num += 1
                cur_pdfs = []
                cur_km = 0
        if cur_pdfs:
            out_file = out_dir / f"{base}_part{file_num}.pdf"
            self._merge_pdfs(cur_pdfs, str(out_file))
        messagebox.showinfo("Split", f"Split into {file_num} files by {km_per_file} km")

    def _split_by_filesize(self, pdf_list, max_mb):
        base = Path(self.output_path).stem
        out_dir = Path(self.output_path).parent
        cur_pdfs = []
        cur_size = 0
        file_num = 1
        for pdf in pdf_list:
            sz = os.path.getsize(pdf) / (1024*1024)
            if cur_size + sz > max_mb and cur_pdfs:
                out_file = out_dir / f"{base}_part{file_num}.pdf"
                self._merge_pdfs(cur_pdfs, str(out_file))
                file_num += 1
                cur_pdfs = []
                cur_size = 0
            cur_pdfs.append(pdf)
            cur_size += sz
        if cur_pdfs:
            out_file = out_dir / f"{base}_part{file_num}.pdf"
            self._merge_pdfs(cur_pdfs, str(out_file))
        messagebox.showinfo("Split", f"Split into {file_num} files by size")

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
