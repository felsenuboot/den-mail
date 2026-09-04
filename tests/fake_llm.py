"""A canned OpenAI-compatible server for screenshots and smoke runs: every chat completion
answers with the same short summary, and /models lists one model.

    python -m tests.fake_llm 18082
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL = "fake-summariser"
ANSWER = ("The sender confirms the order and says the parcel is on its way with DHL, expected on 6 September "
          "around 18:00.\nNothing to do: the tracking number is in the message.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": MODEL}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._send({"choices": [{"message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
                    "model": MODEL})


def main(port: int) -> None:
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 18082)
