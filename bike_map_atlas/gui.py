"""Tkinter GUI for the bike map atlas application."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .map_renderer import MapRenderer
from .models import MapSection
from .worker import load_gpx_worker, split_route_worker


class BikeMapAtlasApp:
    """Simple Tkinter-based application shell for GPX processing and PDF export."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Bike Map Atlas")
        self.root.geometry("1024x700")
        self.root.minsize(800, 550)

        self.file_path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready")
        self.scale_var = tk.StringVar(value="25000")
        self.paper_var = tk.StringVar(value="A4")
        self.overlap_var = tk.StringVar(value="500")

        self.points = []
        self.sections: list[MapSection] = []
        self.total_km = 0.0

        self._build_ui()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(12, 12, 12, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Button(top, text="Open GPX", command=self.open_gpx).grid(row=0, column=0, padx=(0, 8), sticky="w")
        ttk.Entry(top, textvariable=self.file_path_var, state="readonly").grid(row=0, column=1, sticky="ew")

        settings = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        settings.grid(row=1, column=0, sticky="ew")
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        ttk.Label(settings, text="Scale").grid(row=0, column=0, padx=(0, 6), sticky="w")
        ttk.Entry(settings, textvariable=self.scale_var, width=12).grid(row=0, column=1, sticky="w")

        ttk.Label(settings, text="Paper").grid(row=0, column=2, padx=(16, 6), sticky="w")
        ttk.Combobox(settings, textvariable=self.paper_var, values=["A4", "A3", "Letter"], width=8, state="readonly").grid(row=0, column=3, sticky="w")

        ttk.Label(settings, text="Overlap (m)").grid(row=1, column=0, padx=(0, 6), pady=(8, 0), sticky="w")
        ttk.Entry(settings, textvariable=self.overlap_var, width=12).grid(row=1, column=1, pady=(8, 0), sticky="w")

        action_frame = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        action_frame.grid(row=2, column=0, sticky="ew")

        ttk.Button(action_frame, text="Split Route", command=self.split_route).pack(side="left", padx=(0, 8))
        ttk.Button(action_frame, text="Render Overview", command=self.render_overview).pack(side="left", padx=(0, 8))
        ttk.Button(action_frame, text="Render Selected Page", command=self.render_selected_section).pack(side="left")

        status = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        status.grid(row=3, column=0, sticky="ew")
        ttk.Label(status, textvariable=self.status_var, foreground="#1a5fb4").pack(anchor="w")

        self.tree = ttk.Treeview(self.root, columns=("index", "start", "end", "orientation", "scale"), show="headings")
        self.tree.heading("index", text="#")
        self.tree.heading("start", text="Start (km)")
        self.tree.heading("end", text="End (km)")
        self.tree.heading("orientation", text="Orientation")
        self.tree.heading("scale", text="Scale")
        self.tree.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.root.rowconfigure(4, weight=1)

    def run(self) -> None:
        self.root.mainloop()

    def open_gpx(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("GPX files", "*.gpx"), ("All files", "*.*")])
        if not path:
            return

        self.file_path_var.set(path)
        self.status_var.set(f"Loading {Path(path).name}...")

        thread = threading.Thread(target=self._load_gpx_worker, args=(path,), daemon=True)
        thread.start()

    def _load_gpx_worker(self, path: str) -> None:
        def on_complete(points, total_km):
            self.points = points
            self.total_km = total_km
            self.root.after(0, self._on_gpx_loaded)

        def on_error(error_msg):
            self.root.after(0, lambda: self._set_error(error_msg))

        load_gpx_worker(path, on_complete, on_error)

    def _on_gpx_loaded(self) -> None:
        self.status_var.set(f"Loaded {len(self.points)} points ({self.total_km:.1f} km)")
        self.split_route()

    def split_route(self) -> None:
        if not self.points:
            messagebox.showwarning("No route loaded", "Open a GPX file first.")
            return

        try:
            scale = int(self.scale_var.get())
            overlap = float(self.overlap_var.get())
            paper = self.paper_var.get()
        except ValueError:
            messagebox.showerror("Invalid settings", "Scale and overlap must be numeric values.")
            return

        self.status_var.set("Splitting route...")

        thread = threading.Thread(
            target=self._split_route_worker,
            args=(scale, paper, overlap),
            daemon=True,
        )
        thread.start()

    def _split_route_worker(self, scale: int, paper: str, overlap: float) -> None:
        def on_complete(sections):
            self.sections = sections
            self.root.after(0, self._on_route_split)

        def on_error(error_msg):
            self.root.after(0, lambda: self._set_error(error_msg))

        split_route_worker(self.points, scale, paper, overlap, on_complete, on_error)

    def _on_route_split(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for section in self.sections:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    section.index + 1,
                    round(section.start_m / 1000.0, 2),
                    round(section.end_m / 1000.0, 2),
                    section.orientation,
                    section.effective_scale,
                ),
            )
        self.status_var.set(f"Split into {len(self.sections)} section(s)")

    def render_overview(self) -> None:
        if not self.sections:
            messagebox.showwarning("No sections", "Split the route before rendering the overview.")
            return

        output = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile="overview.pdf",
        )
        if not output:
            return

        try:
            renderer = MapRenderer(int(self.scale_var.get()))
            renderer.render_overview(self.sections, self.total_km, output)
            self.status_var.set(f"Overview saved to {output}")
        except Exception as exc:  # pragma: no cover
            self._set_error(str(exc))

    def render_selected_section(self) -> None:
        if not self.sections:
            messagebox.showwarning("No sections", "Split the route before rendering a section.")
            return

        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No page selected", "Select a section in the table first.")
            return

        index = int(self.tree.item(selection[0], "values")[0]) - 1
        if index < 0 or index >= len(self.sections):
            return

        output = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=f"section_{index + 1}.pdf",
        )
        if not output:
            return

        try:
            renderer = MapRenderer(int(self.scale_var.get()))
            renderer.render_section(self.sections[index], output)
            self.status_var.set(f"Page {index + 1} saved to {output}")
        except Exception as exc:  # pragma: no cover
            self._set_error(str(exc))

    def _set_error(self, message: str) -> None:
        self.status_var.set(f"Error: {message}")
        messagebox.showerror("Error", message)


if __name__ == "__main__":
    app = BikeMapAtlasApp()
    app.run()


__all__ = ["BikeMapAtlasApp"]
