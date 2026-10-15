"""Provider integration tests against an in-process HTTP server."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for running without pip install
from mmenterprise_bench.providers import Client, ProviderError


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, value):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

    def do_GET(self):
        self.send_json(200, {"data": [{"id": "local-reader"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received.append(body)
        if self.server.responses:
            status, response = self.server.responses.pop(0)
            self.send_json(status, response)
        elif ":generateContent" in self.path:
            self.send_json(200, {"candidates": [{"content": {"parts": [{"text": "private reasoning", "thought": True}, {"text": "42"}]}, "finishReason": "STOP"}], "modelVersion": "gemini-test"})
        else:
            content = '{"correct": true, "reason": "Equivalent answer."}' if body.get("response_format") else "42"
            self.send_json(200, {"model": body["model"], "choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"completion_tokens": 1}})


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.received = []
        self.server.responses = []
        self.spec = {"provider": "local", "base_url": self.base + "/v1", "model_id": "local-reader"}
        self.client = Client(attempts=2, backoff=0)
        self.body = {"model": "local-reader", "messages": []}

    def test_local_model_preflight_and_answer(self):
        self.client.check_local_model(self.spec)
        response = self.client.answer(self.spec, self.body)
        self.assertEqual(response["answer"], "42")
        with self.assertRaisesRegex(ValueError, "not serving"):
            self.client.check_local_model({**self.spec, "model_id": "wrong-model"})

    def test_rate_limit_retries_without_zero_score(self):
        self.server.responses = [(429, {"error": {"code": "rate_limit_exceeded"}})]
        with patch("mmenterprise_bench.providers.time.sleep"):
            self.assertEqual(self.client.answer(self.spec, self.body)["answer"], "42")
        self.assertEqual(len(self.server.received), 2)

    def test_capacity_and_billing_are_distinct(self):
        self.server.responses = [(400, {"error": {"code": "context_length_exceeded"}})]
        with self.assertRaises(ProviderError) as captured:
            self.client.answer(self.spec, self.body)
        self.assertTrue(captured.exception.capacity)
        self.server.responses = [(429, {"error": {"code": "insufficient_quota"}})]
        with self.assertRaises(ProviderError) as captured:
            self.client.answer(self.spec, self.body)
        self.assertFalse(captured.exception.capacity)
        self.assertFalse(captured.exception.retryable)

    def test_malformed_answer_response_is_retried(self):
        self.server.responses = [(200, {"choices": []})]
        self.assertEqual(self.client.answer(self.spec, self.body)["answer"], "42")
        self.assertEqual(len(self.server.received), 2)

    def test_judgment_schema_over_http(self):
        body = {**self.body, "response_format": {"type": "json_object"}}
        result = self.client.judge(self.spec, body)
        self.assertIs(result["correct"], True)

    def test_gemini_thoughts_excluded(self):
        spec = {"provider": "gemini", "model_id": "gemini-test", "base_url": self.base,
                "api_key_env": "TEST_GEMINI_API_KEY"}
        with patch.dict(os.environ, {"TEST_GEMINI_API_KEY": "test-only-key"}):
            response = self.client.answer(spec, {"contents": []})
        self.assertEqual(response["answer"], "42")


if __name__ == "__main__":
    unittest.main()
