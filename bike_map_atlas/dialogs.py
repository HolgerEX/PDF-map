"""Custom dialog windows for section settings."""

from __future__ import annotations

from typing import Callable
import tkinter as tk
from tkinter import messagebox


class SectionSettingsDialog:
    """Dialog for editing section scale and zoom level."""

    def __init__(
        self,
        parent: tk.Widget,
        section_idx: int,
        current_scale: int,
        current_zoom: int | None,
        callback: Callable[[int, int, int | None], None],
    ) -> None:
        """Initialize dialog.

        Args:
            parent: Parent window.
            section_idx: Section index.
            current_scale: Current scale value.
            current_zoom: Current zoom value (or None).
            callback: Callback function (idx, scale, zoom).
        """
        self.parent = parent
        self.section_idx = section_idx
        self.callback = callback

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"Section {section_idx + 1} Settings")
        self.dialog.geometry("300x220")
        self.dialog.transient(parent)
        self.dialog.resizable(False, False)

        # Scale input
        tk.Label(self.dialog, text="Map Scale (1:xxx):").pack(pady=5)
        self.scale_var = tk.StringVar(value=str(current_scale))
        tk.Entry(self.dialog, textvariable=self.scale_var, width=15).pack()

        # Zoom level
        tk.Label(self.dialog, text="Tile Zoom Level (0-20, leave empty for auto):").pack(pady=5)
        self.zoom_var = tk.StringVar(value=str(current_zoom) if current_zoom is not None else "")
        tk.Entry(self.dialog, textvariable=self.zoom_var, width=10).pack()

        # Buttons
        btn_frame = tk.Frame(self.dialog)
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text="Apply", command=self._apply).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=self.dialog.destroy).pack(side=tk.LEFT)

        self.dialog.update_idletasks()
        self.dialog.after(10, self._set_grab)

    def _set_grab(self) -> None:
        self.dialog.grab_set()
        self.dialog.focus_set()

    def _apply(self) -> None:
        try:
            new_scale = int(self.scale_var.get())
            if new_scale <= 0:
                raise ValueError
        except (ValueError, TypeError):
            messagebox.showerror("Invalid scale", "Scale must be a positive integer.")
            return

        zoom_str = self.zoom_var.get().strip()
        new_zoom: int | None = None
        if zoom_str:
            try:
                new_zoom = int(zoom_str)
                if not (0 <= new_zoom <= 20):
                    raise ValueError
            except (ValueError, TypeError):
                messagebox.showerror("Invalid zoom", "Zoom must be an integer between 0 and 20.")
                return

        self.callback(self.section_idx, new_scale, new_zoom)
        self.dialog.destroy()
