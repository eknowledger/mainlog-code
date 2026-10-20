"""A local stand-in for the Messages endpoint that answers slowly, on purpose. Standard library only.

It speaks just enough of the API for the real SDK to parse it: a streamed answer as server-sent events, one token
every `gap` seconds, or a JSON answer whose bytes trickle out 20 at a time. Nothing leaves this machine, and no key
is needed, so CI runs it.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class Slow:
    """Start with `with Slow(gap=1.5, tokens=12) as base_url:`; `stall_after` makes one gap `stall` seconds long."""

    def __init__(self, gap=1.5, tokens=12, stall_after=None, stall=0.0, silent=0.0):
        self.gap, self.tokens, self.stall_after, self.stall = gap, tokens, stall_after, stall
        self.silent = silent                # seconds before any byte of the response, headers included
        self.attempts = 0                   # requests received, so a caller can count the SDK's retries

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.attempts += 1
                try:
                    if outer.silent:
                        time.sleep(outer.silent)
                    if body.get("stream"):
                        outer._stream(self, body)
                    else:
                        outer._json(self, body)
                except (BrokenPipeError, ConnectionResetError):
                    pass                    # the client gave up, which is what some runs are testing

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_port}"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()

    def _wait(self, i):
        time.sleep(self.stall if self.stall_after is not None and i == self.stall_after else self.gap)

    def _stream(self, h, body):
        h.send_response(200)
        h.send_header("content-type", "text/event-stream")
        h.end_headers()
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}
        h.wfile.write(_sse("message_start", {"type": "message_start", "message": msg}))
        h.wfile.write(_sse("content_block_start", {"type": "content_block_start", "index": 0,
                                                   "content_block": {"type": "text", "text": ""}}))
        h.wfile.flush()
        for i in range(self.tokens):
            self._wait(i)
            h.wfile.write(_sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                       "delta": {"type": "text_delta", "text": f"t{i} "}}))
            h.wfile.flush()
        h.wfile.write(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        h.wfile.write(_sse("message_delta", {"type": "message_delta",
                                             "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                             "usage": {"output_tokens": self.tokens}}))
        h.wfile.write(_sse("message_stop", {"type": "message_stop"}))
        h.wfile.flush()

    def _json(self, h, body):
        data = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                           "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                           "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
        h.send_response(200)
        h.send_header("content-type", "application/json")
        h.send_header("content-length", str(len(data)))
        h.end_headers()
        h.wfile.flush()
        for i, start in enumerate(range(0, len(data), 20)):
            self._wait(i)
            h.wfile.write(data[start:start + 20])
            h.wfile.flush()
