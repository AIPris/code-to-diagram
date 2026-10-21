"""draw.io desktop CLI -> verified PNG.

A PNG is usable only if it has the signature and its IEND trailer: draw.io has been seen to write PNGs that stop before
IEND (always with -e/--embed-diagram, sometimes otherwise), and reading one makes the image API reject the file and
ends the whole session. So every export is checked, retried, and never embedded. The project being diagrammed often
has its own scripts/render_png.sh; this one is the skill's and is run by absolute path.
"""
import os
import shutil
import struct
import subprocess
import sys
import time

CANDIDATES = [
    r"C:\Program Files\draw.io\draw.io.exe", r"C:\Program Files (x86)\draw.io\draw.io.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\draw.io\draw.io.exe"),
    "/Applications/draw.io.app/Contents/MacOS/draw.io", os.path.expanduser("~/Applications/draw.io.app/Contents/MacOS/draw.io"),
    "/usr/bin/drawio", "/usr/local/bin/drawio", "/snap/bin/drawio", "/opt/drawio/drawio",
]
TOO_BIG = 7500      # the image API rejects sides over 8000 px


def find_drawio():
    for c in CANDIDATES:
        if c and os.path.isfile(c):
            return c
    return shutil.which("drawio") or shutil.which("draw.io")


def png_dims(path):
    """(width, height) if the file is a complete PNG, else None."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82":
        return None
    return struct.unpack(">II", data[16:24])


def choose_scale(width, height):
    """Scale that keeps small labels legible without an unreadably large image: 2x up to ~1500 px, then 1x, then less."""
    longest = max(width, height)
    return 2 if longest <= 1500 else 1 if longest <= 3200 else 0.75


def render(drawio, png, scale=2):
    """Export drawio to png. Returns (dims or None, message)."""
    exe = find_drawio()
    if not exe:
        return None, "draw.io desktop not found (install from https://github.com/jgraph/drawio-desktop/releases); the .drawio file is still complete"
    prefix, flags = [], []
    if sys.platform.startswith("linux"):
        if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
            prefix = ["xvfb-run", "-a"]
        if hasattr(os, "getuid") and os.getuid() == 0:
            flags = ["--no-sandbox"]
    err = ""
    for attempt in range(3):
        if os.path.exists(png):
            os.remove(png)
        try:
            r = subprocess.run(prefix + [exe, "-x", "-f", "png", "-b", "20", "-s", str(scale), "-o", png, drawio] + flags,
                               capture_output=True, text=True, timeout=120)
            err = (r.stderr or "").strip()
        except subprocess.TimeoutExpired:
            err = "draw.io timed out"
        dims = png_dims(png)
        if dims:
            if scale > 1 and max(dims) > TOO_BIG:
                scale = 1
                continue
            return dims, ""
        time.sleep(1)
    if os.path.exists(png):
        os.remove(png)
    return None, f"draw.io export failed after 3 attempts ({err[:200] or 'no PNG written'}); causes: malformed XML, file open in draw.io. No PNG exists: do not Read one"
