"""A deterministic OpenAI-compatible mock judge (standard library only).

Used by the test-suite and by ``scripts/check.bash``; also handy for trying ``mmeb score`` without an
API key::

    python tests/mock_judge.py --port 8765 &
    mmeb score examples/predictions_sample.jsonl --judge-base-url http://127.0.0.1:8765/v1 --judge-model mock

Rule: a candidate answer is judged ``correct`` when it contains the marker ``[MOCK-CORRECT]`` or equals
the reference answer after whitespace normalisation. Everything else is ``incorrect``. The server also
answers ``GET /v1/models`` so it can stand in for a vLLM reader in tests.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

MARKER = "[MOCK-CORRECT]"


def judge_rule(fields):
    candidate = str(fields.get("candidate_answer", ""))
    reference = str(fields.get("reference_answer", ""))
    if MARKER in candidate:
        return True, "Mock judge: marker present."
    if " ".join(candidate.split()) == " ".join(reference.split()):
        return True, "Mock judge: exact match with the reference."
    return False, "Mock judge: no marker and no exact match."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.send_json(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received.append(body)
        try:
            fields = json.loads(body["messages"][-1]["content"])
            correct, reason = judge_rule(fields)
            content = json.dumps({"correct": correct, "reason": reason})
        except (KeyError, IndexError, TypeError, ValueError):
            content = "mock reader answer"
        self.send_json(200, {"id": "mock", "object": "chat.completion", "model": body.get("model", "mock"),
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})


def start(port=0, host="127.0.0.1"):
    """Start the server in a daemon thread; returns (server, base_url_with_v1)."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{server.server_address[1]}/v1"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    server, base = start(args.port, args.host)
    print(f"Mock judge listening on {base} (model name: mock; correct iff the answer contains {MARKER})", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
