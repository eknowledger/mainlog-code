"""Measure the same named font that the standalone SVG requests."""
from functools import lru_cache
from font_config import load_font

TITLE_SIZE, BODY_SIZE, LABEL_SIZE = 14, 12, 12
FONT_FAMILY = "Arial"


@lru_cache(maxsize=64)
def font(size, bold=False):
    return load_font(size, bold)


def width(text, size=BODY_SIZE, bold=False):
    return font(size, bold).getlength(text) / 4


def text_box(text, x, y, size=BODY_SIZE, bold=False, anchor="middle"):
    f = font(size, bold)
    b = f.getbbox(text, anchor={"middle": "ms", "start": "ls", "end": "rs"}[anchor])
    return (x + b[0] / 4, y + b[1] / 4, x + b[2] / 4, y + b[3] / 4)
