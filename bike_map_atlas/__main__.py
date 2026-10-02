"""Main entry point for the application."""

from __future__ import annotations

from .gui import BikeMapAtlasApp


if __name__ == "__main__":
    app = BikeMapAtlasApp()
    app.run()
