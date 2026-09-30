"""One lost voice packet, delivered over UDP and over TCP: which frames still play on time.

    python one_lost_packet.py

A model, not a network test, so every run gives the same numbers and every assumption is written down:

- A frame leaves every 20 ms and takes 30 ms to arrive (a 60 ms round trip). No jitter.
- The receiver plays frame n at 20n + 30 + 40 ms: arrival time plus a 40 ms jitter buffer.
- Frame 5 is lost once.
- UDP: the receiver sees frame 6 without 5, and the decoder conceals the missing 20 ms.
- TCP, fast retransmit: frames 6, 7 and 8 each draw a duplicate ACK; the third, back at the sender 30 ms after frame 8
  arrives, triggers the resend (RFC 5681). TCP hands nothing after frame 4 to the application until 5 arrives.
- TCP, timeout: with nothing after the loss to draw duplicate ACKs (the end of a sentence, a pause), the resend waits
  for the retransmission timer. 200 ms is Linux's minimum; RFC 6298 recommends 1 s.
- Any frame the application gets after its playout time is too late to play and is concealed instead.
"""

from __future__ import annotations

from dataclasses import dataclass

FRAME_MS = 20
ONE_WAY_MS = 30
BUFFER_MS = 40
LOST = 5
FRAMES = range(1, 21)
LINUX_MIN_RTO_MS = 200


def sent(n: int) -> int:
    return n * FRAME_MS


def deadline(n: int) -> int:
    return sent(n) + ONE_WAY_MS + BUFFER_MS


@dataclass(frozen=True)
class Outcome:
    name: str
    delivered: dict[int, int | None]  # frame -> time the application gets it, None if never

    def late(self) -> list[int]:
        return [n for n, t in self.delivered.items() if t is None or t > deadline(n)]


def udp() -> Outcome:
    return Outcome("UDP", {n: None if n == LOST else sent(n) + ONE_WAY_MS for n in FRAMES})


def tcp(resend_at: int, name: str) -> Outcome:
    """In-order delivery: nothing after the lost frame reaches the application before the resent copy does."""
    repaired = resend_at + ONE_WAY_MS
    return Outcome(name, {n: sent(n) + ONE_WAY_MS if n < LOST else max(sent(n) + ONE_WAY_MS, repaired) for n in FRAMES})


def main() -> None:
    third_dup_ack = sent(LOST + 3) + 2 * ONE_WAY_MS  # frame 8 arrives, its duplicate ACK travels back
    cases = [
        udp(),
        tcp(third_dup_ack, "TCP, fast retransmit"),
        tcp(sent(LOST) + LINUX_MIN_RTO_MS, "TCP, 200 ms timeout"),
    ]
    print(f"A frame every {FRAME_MS} ms, {ONE_WAY_MS} ms each way, a {BUFFER_MS} ms jitter buffer; frame {LOST} is lost once.\n")
    print("frame  sent  play by   " + "  ".join(f"{c.name:>22}" for c in cases))
    for n in range(3, 13):
        cells = []
        for c in cases:
            t = c.delivered[n]
            cells.append(f"{'lost' if t is None else f'{t} ms':>12} {'late' if n in c.late() else 'on time':>9}")
        print(f"{n:>5}  {sent(n):>4}  {deadline(n):>7}   " + "  ".join(cells))
    print()
    for c in cases:
        late = c.late()
        print(f"{c.name:>22}: {len(late)} frame(s) not played on time = {len(late) * FRAME_MS} ms of concealed audio")


if __name__ == "__main__":
    main()
