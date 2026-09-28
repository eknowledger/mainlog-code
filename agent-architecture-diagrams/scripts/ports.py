"""Derive facing, aligned ports from current rectangles, never saved pixels."""

PORT_INSET = 12


def packed_positions(length, fixed, mandatory):
    """Respect aligned anchors and port order; omit optional sites that don't fit.

    Alternative sides are optional sites for the same connection, not extra
    wires that must all reserve space. Required sites are never dropped.
    """
    def fit(indices):
        count = len(indices)
        gap = min(24, (length - 2 * PORT_INSET) / max(1, count - 1))
        start = (length - gap * (count - 1)) / 2
        anchors = {i: fixed[k] for i, k in enumerate(indices) if fixed[k] is not None}
        positions = {}
        previous = -float("inf")
        for i, key in enumerate(indices):
            low = PORT_INSET + i * gap
            high = length - PORT_INSET - (count - 1 - i) * gap
            for j, coordinate in anchors.items():
                if j < i:
                    low = max(low, coordinate + (i - j) * gap)
                elif j > i:
                    high = min(high, coordinate - (j - i) * gap)
                else:
                    low, high = max(low, coordinate), min(high, coordinate)
            position = max(low, previous + gap, min(high, start + i * gap))
            if position > high + 1e-6:
                return None
            positions[key] = position
            previous = position
        return positions

    chosen = [i for i in range(len(fixed)) if mandatory[i] or fixed[i] is not None]
    if fit(chosen) is None:
        raise ValueError("Required aligned ports cannot fit in order")
    for i in range(len(fixed)):
        if i not in chosen and fit(sorted(chosen + [i])) is not None:
            chosen.append(i)
            chosen.sort()
    return fit(chosen)


def straight_plan(a, b, axis, fraction=.5):
    if not 0 <= fraction <= 1:
        raise ValueError("Straight-port fraction must be between zero and one")
    if axis == "horizontal":
        if a["x"] + a["w"] <= b["x"]:
            sides, fixed = ("E", "W"), (a["x"] + a["w"], b["x"])
        elif b["x"] + b["w"] <= a["x"]:
            sides, fixed = ("W", "E"), (a["x"], b["x"] + b["w"])
        else:
            return None
        lo = max(a["y"], b["y"]) + PORT_INSET
        hi = min(a["y"] + a["h"], b["y"] + b["h"]) - PORT_INSET
        if hi < lo:
            return None
        coord = lo + fraction * (hi - lo)
        points = [(x, coord) for x in fixed]
    elif axis == "vertical":
        if a["y"] + a["h"] <= b["y"]:
            sides, fixed = ("S", "N"), (a["y"] + a["h"], b["y"])
        elif b["y"] + b["h"] <= a["y"]:
            sides, fixed = ("N", "S"), (a["y"], b["y"] + b["h"])
        else:
            return None
        lo = max(a["x"], b["x"]) + PORT_INSET
        hi = min(a["x"] + a["w"], b["x"] + b["w"]) - PORT_INSET
        if hi < lo:
            return None
        coord = lo + fraction * (hi - lo)
        points = [(coord, y) for y in fixed]
    else:
        raise ValueError("Straight-port axis must be horizontal or vertical")
    return sides, points
