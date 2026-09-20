"""Weather glyphs drawn with PicoGraphics primitives.

Drawing them beats shipping bitmaps: they scale to any box, cost no
flash, and stay crisp on a 1-bit e-ink panel where dithered artwork
turns to mush. Every icon fits a square box and assumes the caller has
already selected a pen.
"""

BLACK = 0
WHITE = 15


def _sun(d, x, y, s, rays=True):
    cx, cy, r = x + s // 2, y + s // 2, s // 4
    d.circle(cx, cy, r)
    if not rays:
        return
    step = max(2, s // 8)
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        d.line(cx + dx * (r + step // 2), cy + dy * (r + step // 2),
               cx + dx * (r + step + 2), cy + dy * (r + step + 2))


def _moon(d, x, y, s):
    cx, cy, r = x + s // 2, y + s // 2, s // 3
    d.circle(cx, cy, r)
    d.set_pen(WHITE)
    d.circle(cx + r // 2, cy - r // 3, r)
    d.set_pen(BLACK)


def _cloud(d, x, y, s, w=None):
    """A cloud sitting in the upper two-thirds of the box."""
    w = w or s
    base = y + int(s * 0.60)
    left = x + int(w * 0.16)
    right = x + int(w * 0.84)
    r1 = max(3, int(s * 0.17))
    r2 = max(4, int(s * 0.22))
    d.circle(left + r1, base - r1, r1)
    d.circle(x + w // 2, base - r2 - 1, r2)
    d.circle(right - r1, base - r1, r1)
    d.rectangle(left, base - r1, right - left, r1)


def _drops(d, x, y, s, count=3, length=None, heavy=False):
    top = y + int(s * 0.62)
    length = length or max(4, int(s * 0.22))
    span = int(s * 0.62)
    start = x + int(s * 0.19)
    for i in range(count):
        px = start + (span * i) // max(count - 1, 1)
        offset = 0 if not heavy else (i % 2) * 2
        d.line(px, top + offset, px - length // 2, top + length + offset)


def _flakes(d, x, y, s, count=3):
    top = y + int(s * 0.70)
    span = int(s * 0.62)
    start = x + int(s * 0.19)
    arm = max(2, s // 10)
    for i in range(count):
        px = start + (span * i) // max(count - 1, 1)
        d.line(px - arm, top, px + arm, top)
        d.line(px, top - arm, px, top + arm)
        d.line(px - arm + 1, top - arm + 1, px + arm - 1, top + arm - 1)
        d.line(px - arm + 1, top + arm - 1, px + arm - 1, top - arm + 1)


def _bolt(d, x, y, s):
    cx = x + s // 2
    top = y + int(s * 0.58)
    bottom = y + int(s * 0.95)
    mid = (top + bottom) // 2
    d.triangle(cx + 3, top, cx - 4, mid + 2, cx + 1, mid - 1)
    d.triangle(cx + 4, mid - 2, cx - 3, bottom, cx, mid + 1)


def draw(d, key, x, y, s=30):
    """Render icon `key` in an s x s box with its top-left at (x, y)."""
    d.set_pen(BLACK)
    if key == "sun":
        _sun(d, x, y, s)
    elif key == "moon":
        _moon(d, x, y, s)
    elif key == "partly":
        _sun(d, x - int(s * 0.08), y - int(s * 0.10), int(s * 0.72))
        d.set_pen(WHITE)
        _cloud(d, x + 1, y + 1, s)          # knock a halo out behind the cloud
        d.set_pen(BLACK)
        _cloud(d, x, y, s)
    elif key == "cloud":
        _cloud(d, x, y, s)
    elif key == "fog":
        _cloud(d, x, y, int(s * 0.85))
        for i in range(3):
            row = y + int(s * 0.66) + i * max(3, s // 9)
            inset = int(s * 0.12) + (i % 2) * int(s * 0.10)
            d.line(x + inset, row, x + s - inset, row)
    elif key == "drizzle":
        _cloud(d, x, y, s)
        _drops(d, x, y, s, 3, length=max(3, s // 8))
    elif key == "rain":
        _cloud(d, x, y, s)
        _drops(d, x, y, s, 3)
    elif key == "heavy_rain":
        _cloud(d, x, y, s)
        _drops(d, x, y, s, 5, heavy=True)
    elif key == "snow":
        _cloud(d, x, y, s)
        _flakes(d, x, y, s, 3)
    elif key == "sleet":
        _cloud(d, x, y, s)
        _drops(d, x, y, s, 2)
        _flakes(d, x, y, s, 2)
    elif key == "storm":
        _cloud(d, x, y, s)
        _bolt(d, x, y, s)
    else:
        _cloud(d, x, y, s)
