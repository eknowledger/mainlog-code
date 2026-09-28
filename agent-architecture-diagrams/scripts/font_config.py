"""Font loading shared by preflight, measurement, and the native SVG auditor.

This module contains no geometry. The SVG requests Arial; overrides must point
to the corresponding Arial faces installed for the SVG renderer as well.
"""
import os
from pathlib import Path


def load_font(size, bold=False):
    from PIL import ImageFont

    variable = "DIAGRAM_FONT_BOLD" if bold else "DIAGRAM_FONT_REGULAR"
    override = os.environ.get(variable)
    filename = "Arial Bold.ttf" if bold else "Arial.ttf"
    candidates = [override] if override else [
        f"/System/Library/Fonts/Supplemental/{filename}",
        f"/Library/Fonts/{filename}",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf" if bold else
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/arialbd.ttf" if bold else
        "/usr/share/fonts/truetype/msttcorefonts/arial.ttf",
    ]
    errors = []
    for path in candidates:
        try:
            if not Path(path).is_file():
                raise OSError("file not found")
            return ImageFont.truetype(path, round(size * 4))
        except (OSError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
    raise RuntimeError(
        f"Cannot load Arial {'bold' if bold else 'regular'}. Set {variable} to "
        f"the corresponding Arial font file installed for the SVG renderer. "
        + "; ".join(errors)
    )
