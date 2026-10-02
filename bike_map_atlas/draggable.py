"""Draggable rectangle handler for preview."""

from __future__ import annotations

from typing import Callable

from .projection import latlon_to_utm


class DraggableRectangle:
    """A matplotlib rectangle that can be dragged and double-clicked."""

    def __init__(
        self,
        rect,
        section_idx: int,
        on_double_click: Callable[[int], None],
        on_drag_end: Callable[[int, float, float], None],
    ) -> None:
        """Initialize draggable rectangle.

        Args:
            rect: Matplotlib Rectangle patch.
            section_idx: Index of the section.
            on_double_click: Callback for double-click.
            on_drag_end: Callback for drag end (idx, x, y).
        """
        self.rect = rect
        self.section_idx = section_idx
        self.on_double_click = on_double_click
        self.on_drag_end = on_drag_end
        self.press = None
        self.dragging = False
        self._click_timer = None

        self.rect.figure.canvas.mpl_connect("button_press_event", self.on_press)
        self.rect.figure.canvas.mpl_connect("button_release_event", self.on_release)
        self.rect.figure.canvas.mpl_connect("motion_notify_event", self.on_motion)

    def on_press(self, event) -> None:
        """Handle mouse press."""
        if event.inaxes != self.rect.axes:
            return

        contains, _ = self.rect.contains(event)
        if not contains:
            return

        self.press = (event.xdata, event.ydata)
        self.dragging = False

        # Double-click detection
        if self._click_timer is None:
            self._click_timer = self.rect.figure.canvas.new_timer(interval=300)
            self._click_timer.single_shot = True
            self._click_timer.add_callback(self._clear_timer)
            self._click_timer.start()
        else:
            self._click_timer.stop()
            self._click_timer = None
            self.on_double_click(self.section_idx)

    def _clear_timer(self) -> None:
        self._click_timer = None

    def on_motion(self, event) -> None:
        """Handle mouse motion."""
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

    def on_release(self, event) -> None:
        """Handle mouse release."""
        if self.press is not None and self.dragging:
            x0, y0 = self.rect.xy
            w = self.rect.get_width()
            h = self.rect.get_height()
            center_lon = x0 + w / 2
            center_lat = y0 + h / 2

            x, y, _ = latlon_to_utm(center_lat, center_lon)
            self.on_drag_end(self.section_idx, x, y)

        self.press = None
        self.dragging = False
