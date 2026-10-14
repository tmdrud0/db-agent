"""Model HTTP contract checks with a mocked provider; these are not scores."""

import json
import os
import unittest
from unittest import mock

import httpx

from agent.model import (
    API_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    ModelClient,
    ModelError,
    extract_sql,
)


def provider_reply(content: str = "SELECT 1;", finish_reason: str = "stop") -> dict:
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


class ExtractSqlTests(unittest.TestCase):
    def test_plain_sql_is_kept_and_outer_whitespace_is_trimmed(self):
        self.assertEqual(extract_sql("  SELECT 1;\n"), "SELECT 1;")

    def test_single_code_fence_wrapping_the_whole_response_is_removed(self):
        self.assertEqual(extract_sql("```sql\nSELECT 1;\n```"), "SELECT 1;")
        self.assertEqual(extract_sql("```\nSELECT 1;\n```"), "SELECT 1;")

    def test_only_a_fence_wrapping_the_whole_response_is_removed(self):
        inline = "SELECT '```' AS marker"
        self.assertEqual(extract_sql(inline), inline)
        trailing = "```\nSELECT 1;\n```\nSELECT 2;"
        self.assertEqual(extract_sql(trailing), trailing)

    def test_empty_content_and_empty_fence_are_errors(self):
        for content in ("", "   ", "```\n```", "```sql\n```"):
            with self.subTest(content=content), self.assertRaises(ModelError):
                extract_sql(content)


class ModelRequestTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler, **kwargs) -> ModelClient:
        return ModelClient(transport=httpx.MockTransport(handler), **kwargs)

    async def test_request_matches_the_provider_contract(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=provider_reply())

        prompt = "Database: demo\n\nUser Query:\nHow many customers?"
        reply = await self.client(handler, api_key="fake-key").generate_sql(prompt)

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), API_URL)
        self.assertEqual(request.headers["authorization"], "Bearer fake-key")
        body = json.loads(request.content)
        self.assertEqual(body["model"], MODEL_ID)
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        system = body["messages"][0]["content"].lower()
        self.assertIn("postgresql", system)
        self.assertIn("only", system)
        self.assertIn("no markdown", system)
        self.assertEqual(body["messages"][1]["content"], prompt)

        self.assertEqual(reply.sql, "SELECT 1;")
        self.assertEqual(reply.response_model, MODEL_ID)
        self.assertEqual(reply.usage, provider_reply()["usage"])
        self.assertGreaterEqual(reply.elapsed_seconds, 0)

    async def test_missing_api_key_fails_before_any_request(self):
        def handler(request):
            raise AssertionError("No HTTP request may be sent without a key")

        with mock.patch.dict(os.environ, {"CMD_API_KEY": ""}):
            with self.assertRaises(ModelError) as caught:
                await self.client(handler).generate_sql("How many customers?")
        self.assertIn("CMD_API_KEY", str(caught.exception))

    async def test_model_http_errors_and_timeouts_are_explicit(self):
        def failing(request):
            return httpx.Response(500, json={"error": {"message": "upstream failure"}})

        with self.assertRaises(ModelError) as caught:
            await self.client(failing, api_key="fake-key").generate_sql("q")
        self.assertIn("500", str(caught.exception))

        def timing_out(request):
            raise httpx.ReadTimeout("timed out")

        with self.assertRaises(ModelError) as caught:
            await self.client(timing_out, api_key="fake-key").generate_sql("q")
        self.assertIn("timed out", str(caught.exception))

    async def test_invalid_or_incomplete_payloads_are_generation_failures(self):
        cases = {
            "invalid json": httpx.Response(200, content=b"<html>not json</html>"),
            "missing choices": httpx.Response(200, json={"model": MODEL_ID}),
            "choices not a list": httpx.Response(200, json={"model": MODEL_ID, "choices": {}}),
            "empty content": httpx.Response(200, json=provider_reply("   ")),
            "truncated": httpx.Response(200, json=provider_reply("SELECT", "length")),
        }

        def handler_for(response):
            def handler(request):
                return response

            return handler

        for name, response in cases.items():
            with self.subTest(name=name), self.assertRaises(ModelError):
                await self.client(handler_for(response), api_key="fake-key").generate_sql("q")

    async def test_error_messages_never_contain_the_api_key(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        with self.assertRaises(ModelError) as caught:
            await self.client(handler, api_key="fake-secret-key").generate_sql("q")
        self.assertNotIn("fake-secret-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
