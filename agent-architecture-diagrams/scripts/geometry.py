"""Small exact geometry primitives used by placement and label selection."""
import math


def bounds(box):
    return (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])


def expand(r, pad):
    return r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad


def rect_overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def segment_rect(a, b, r):
    """Liang–Barsky clipping, including diagonal and zero-length segments."""
    lo, hi = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - r[0]), (dx, r[2] - a[0]),
                 (-dy, a[1] - r[1]), (dy, r[3] - a[1])):
        if abs(p) < 1e-9:
            if q < 0:
                return False
        elif p < 0:
            lo = max(lo, q / p)
        else:
            hi = min(hi, q / p)
        if lo > hi:
            return False
    return True


def segment_distance(a, b, c, d):
    def point_seg(p, q, r):
        vx, vy = r[0] - q[0], r[1] - q[1]
        length = vx * vx + vy * vy
        t = max(0, min(1, ((p[0] - q[0]) * vx + (p[1] - q[1]) * vy) / length)) if length else 0
        return math.hypot(p[0] - q[0] - t * vx, p[1] - q[1] - t * vy)
    if intersection(a, b, c, d) is not None:
        return 0.0
    return min(point_seg(a, c, d), point_seg(b, c, d), point_seg(c, a, b), point_seg(d, a, b))


def intersection(a, b, c, d):
    rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-8:
        return None
    t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / den
    u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / den
    if -1e-7 <= t <= 1 + 1e-7 and -1e-7 <= u <= 1 + 1e-7:
        return (a[0] + t * rx, a[1] + t * ry)
    return None


def parallel_overlap(a, b, c, d):
    if abs(a[1] - b[1]) < .01 and abs(c[1] - d[1]) < .01:
        return abs(a[1] - c[1]), min(max(a[0], b[0]), max(c[0], d[0])) - max(min(a[0], b[0]), min(c[0], d[0]))
    if abs(a[0] - b[0]) < .01 and abs(c[0] - d[0]) < .01:
        return abs(a[0] - c[0]), min(max(a[1], b[1]), max(c[1], d[1])) - max(min(a[1], b[1]), min(c[1], d[1]))
    return math.inf, 0


def arrow(points, length=9, half_width=3.8):
    a, b = points[-2:]
    dist = math.dist(a, b)
    dx, dy = (b[0] - a[0]) / dist, (b[1] - a[1]) / dist
    return [b, (b[0] - dx * length - dy * half_width, b[1] - dy * length + dx * half_width),
            (b[0] - dx * length + dy * half_width, b[1] - dy * length - dx * half_width)]
