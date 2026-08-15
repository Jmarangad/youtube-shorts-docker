"""Uploads Hindi-dubbed Shorts to YouTube."""

from .core import build_metadata, find_dubbed, run_upload

__version__ = "1.0.0"

__all__ = ["build_metadata", "find_dubbed", "run_upload"]
