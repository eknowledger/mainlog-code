"""Draw one_lost_packet.py's result as a strip: one row per transport, one cell per 20 ms frame.

    python draw_timeline.py            # writes one_lost_packet.svg

Standard library only. The numbers come from one_lost_packet.py itself, so the figure cannot disagree with the table.
A frame that plays on time is a solid blue cell; one that does not is orange with a diagonal hatch, so the two states
differ in texture as well as colour. Each row ends with its count in words.
"""

from __future__ import annotations

from pathlib import Path

from one_lost_packet import FRAME_MS, FRAMES, LINUX_MIN_RTO_MS, LOST, ONE_WAY_MS, sent, tcp, udp

ON_TIME = "#2a78d6"
MISSED = "#d9480f"
INK, MUTED, RULE = "#1a1d2b", "#5f6472", "#d9dce3"

CELL, GAP = 30, 3
LEFT, TOP, ROW_H = 190, 74, 46
RIGHT_LABEL = 190


def main() -> None:
    cases = [
        udp(),
        tcp(sent(LOST + 3) + 2 * ONE_WAY_MS, "TCP, fast retransmit"),
        tcp(sent(LOST) + LINUX_MIN_RTO_MS, "TCP, 200 ms timeout"),
    ]
    frames = list(FRAMES)
    width = LEFT + len(frames) * (CELL + GAP) + RIGHT_LABEL
    height = TOP + len(cases) * ROW_H + 58
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'font-family="Arial, Helvetica, sans-serif" role="img" aria-labelledby="t d">',
        '<title id="t">One lost voice frame, over UDP and over TCP</title>',
        f'<desc id="d">Frames 1 to {len(frames)}, 20 ms each, frame {LOST} lost once. UDP misses 1 frame, TCP with fast '
        "retransmit misses 4, TCP waiting for a 200 ms timeout misses 8.</desc>",
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="6" height="6" fill="{MISSED}"/><line x1="0" y1="0" x2="0" y2="6" stroke="#ffffff" '
        'stroke-width="2" stroke-opacity="0.55"/></pattern></defs>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="16" y="28" font-size="17" font-weight="700" fill="{INK}">One lost frame, three outcomes</text>',
        f'<text x="16" y="50" font-size="12.5" fill="{MUTED}">A frame every {FRAME_MS} ms, {ONE_WAY_MS} ms each way, '
        f"a 40 ms jitter buffer. Frame {LOST} is lost once on the network.</text>",
    ]
    for i, n in enumerate(frames):
        x = LEFT + i * (CELL + GAP) + CELL / 2
        weight = ' font-weight="700"' if n == LOST else ""
        out.append(f'<text x="{x}" y="{TOP - 8}" font-size="11" text-anchor="middle" fill="{MUTED}"{weight}>{n}</text>')
    for r, case in enumerate(cases):
        y = TOP + r * ROW_H
        late = set(case.late())
        out.append(f'<text x="16" y="{y + CELL / 2 + 4}" font-size="13" font-weight="700" fill="{INK}">{case.name}</text>')
        for i, n in enumerate(frames):
            x = LEFT + i * (CELL + GAP)
            fill = "url(#hatch)" if n in late else ON_TIME
            state = "not played on time" if n in late else "played on time"
            out.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="4" fill="{fill}">'
                       f"<title>{case.name}, frame {n}: {state}</title></rect>")
        count = len(late)
        out.append(f'<text x="{LEFT + len(frames) * (CELL + GAP) + 10}" y="{y + CELL / 2 + 4}" font-size="13" '
                   f'fill="{INK}">{count} frame{"s" if count > 1 else ""}, {count * FRAME_MS} ms concealed</text>')
    ly = TOP + len(cases) * ROW_H + 18
    out += [
        f'<rect x="{LEFT}" y="{ly}" width="14" height="14" rx="3" fill="{ON_TIME}"/>',
        f'<text x="{LEFT + 20}" y="{ly + 11}" font-size="12" fill="{MUTED}">played on time</text>',
        f'<rect x="{LEFT + 130}" y="{ly}" width="14" height="14" rx="3" fill="url(#hatch)"/>',
        f'<text x="{LEFT + 150}" y="{ly + 11}" font-size="12" fill="{MUTED}">missed its playout time, so concealed</text>',
        "</svg>",
    ]
    path = Path(__file__).with_name("one_lost_packet.svg")
    path.write_text("\n".join(o for o in out if o) + "\n", encoding="utf-8")
    print(f"Wrote {path.name}")


if __name__ == "__main__":
    main()
