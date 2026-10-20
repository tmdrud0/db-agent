"""Model chat HTTP contract checks with a mocked provider; these are not scores."""

import json
import os
import unittest
from unittest import mock

import httpx

from agent.model import (
    API_URL,
    MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    MODEL_ID,
    ModelClient,
    ModelError,
)


def provider_reply(content: str = "SELECT 1;", finish_reason: str = "stop") -> dict:
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


class ChatRequestTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler, **kwargs) -> ModelClient:
        return ModelClient(transport=httpx.MockTransport(handler), **kwargs)

    async def test_request_matches_the_provider_contract(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=provider_reply())

        messages = [
            {"role": "system", "content": "You are a test."},
            {"role": "user", "content": "How many customers?"},
        ]
        reply = await self.client(handler, api_key="fake-key").chat(messages)

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), API_URL)
        self.assertEqual(request.headers["authorization"], "Bearer fake-key")
        body = json.loads(request.content)
        self.assertEqual(body["model"], MODEL_ID)
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(body["messages"], messages)

        self.assertEqual(reply.content, "SELECT 1;")
        self.assertEqual(reply.response_model, MODEL_ID)
        self.assertEqual(reply.usage, provider_reply()["usage"])
        self.assertGreaterEqual(reply.elapsed_seconds, 0)
        self.assertFalse(reply.truncated)

    async def test_missing_api_key_fails_before_any_request(self):
        def handler(request):
            raise AssertionError("No HTTP request may be sent without a key")

        with mock.patch.dict(os.environ, {"CMD_API_KEY": ""}):
            with self.assertRaises(ModelError) as caught:
                await self.client(handler).chat([{"role": "user", "content": "q"}])
        self.assertIn("CMD_API_KEY", str(caught.exception))

    async def test_http_errors_and_timeouts_use_the_retry_budget(self):
        requests = []

        def failing(request):
            requests.append(request)
            return httpx.Response(500, json={"error": {"message": "upstream failure"}})

        with self.assertRaises(ModelError) as caught:
            await self.client(failing, api_key="fake-key").chat(
                [{"role": "user", "content": "q"}]
            )
        self.assertEqual(len(requests), MAX_RETRIES + 1)
        self.assertIn("500", str(caught.exception))
        self.assertIn(f"after {MAX_RETRIES + 1} attempts", str(caught.exception))

        timeouts = []

        def timing_out(request):
            timeouts.append(request)
            raise httpx.ReadTimeout("timed out")

        with self.assertRaises(ModelError) as caught:
            await self.client(timing_out, api_key="fake-key").chat(
                [{"role": "user", "content": "q"}]
            )
        self.assertEqual(len(timeouts), MAX_RETRIES + 1)
        self.assertIn("timed out", str(caught.exception))
        self.assertIn(f"after {MAX_RETRIES + 1} attempts", str(caught.exception))

    async def test_a_transient_failure_can_succeed_within_the_retry_budget(self):
        attempts = []

        def handler(request):
            attempts.append(request)
            if len(attempts) < MAX_RETRIES + 1:
                return httpx.Response(503, json={"error": {"message": "busy"}})
            return httpx.Response(200, json=provider_reply("SELECT 2;"))

        reply = await self.client(handler, api_key="fake-key").chat(
            [{"role": "user", "content": "q"}]
        )
        self.assertEqual(len(attempts), MAX_RETRIES + 1)
        self.assertEqual(reply.content, "SELECT 2;")

    async def test_client_errors_are_not_retried(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(401, json={"error": {"message": "unauthorized"}})

        with self.assertRaises(ModelError) as caught:
            await self.client(handler, api_key="fake-key").chat(
                [{"role": "user", "content": "q"}]
            )
        self.assertEqual(len(requests), 1)
        self.assertIn("401", str(caught.exception))

    async def test_malformed_payloads_use_the_retry_budget(self):
        cases = {
            "invalid json": lambda: httpx.Response(200, content=b"<html>not json</html>"),
            "missing choices": lambda: httpx.Response(200, json={"model": MODEL_ID}),
            "empty choices": lambda: httpx.Response(200, json={"model": MODEL_ID, "choices": []}),
            "choices not a list": lambda: httpx.Response(
                200, json={"model": MODEL_ID, "choices": {}}
            ),
            "choice not an object": lambda: httpx.Response(
                200, json={"model": MODEL_ID, "choices": [7]}
            ),
        }

        for name, make_response in cases.items():
            with self.subTest(name=name):
                requests = []

                def handler(request, make_response=make_response):
                    requests.append(request)
                    return make_response()

                with self.assertRaises(ModelError) as caught:
                    await self.client(handler, api_key="fake-key").chat(
                        [{"role": "user", "content": "q"}]
                    )
                self.assertEqual(len(requests), MAX_RETRIES + 1)
                self.assertIn(f"after {MAX_RETRIES + 1} attempts", str(caught.exception))

    async def test_empty_and_truncated_content_are_reported_not_fabricated(self):
        def handler_for(response):
            def handler(request):
                return response

            return handler

        empty = await self.client(
            handler_for(httpx.Response(200, json=provider_reply("   "))), api_key="fake-key"
        ).chat([{"role": "user", "content": "q"}])
        self.assertEqual(empty.content, "   ")
        self.assertFalse(empty.truncated)

        truncated = await self.client(
            handler_for(httpx.Response(200, json=provider_reply("SELECT", "length"))),
            api_key="fake-key",
        ).chat([{"role": "user", "content": "q"}])
        self.assertEqual(truncated.content, "SELECT")
        self.assertTrue(truncated.truncated)

    async def test_error_messages_never_contain_the_api_key(self):
        requests = []

        def handler(request):
            requests.append(request)
            raise httpx.ConnectError("connection refused")

        with self.assertRaises(ModelError) as caught:
            await self.client(handler, api_key="fake-secret-key").chat(
                [{"role": "user", "content": "q"}]
            )
        self.assertEqual(len(requests), MAX_RETRIES + 1)
        self.assertNotIn("fake-secret-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
