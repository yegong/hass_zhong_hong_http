"""Tests for the standalone Zhonghong gateway query tool."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.query_gateway import (
    Endpoint,
    GatewayAuthenticationError,
    GatewayResponseError,
    PageResult,
    QueryResult,
    ZhonghongQueryClient,
    build_debug_output,
    parse_endpoint,
    parse_http_response,
)


class ParseEndpointTests(unittest.TestCase):
    """Test endpoint validation."""

    def test_plain_host_uses_default_port(self) -> None:
        self.assertEqual(
            parse_endpoint("192.0.2.10"),
            Endpoint("192.0.2.10", 80, "192.0.2.10"),
        )

    def test_http_url_with_port(self) -> None:
        self.assertEqual(
            parse_endpoint("http://gateway.local:8080/"),
            Endpoint("gateway.local", 8080, "gateway.local:8080"),
        )

    def test_rejects_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain a path"):
            parse_endpoint("http://gateway.local/cgi-bin/api.html")

    def test_rejects_https(self) -> None:
        with self.assertRaisesRegex(ValueError, "only plain HTTP"):
            parse_endpoint("https://gateway.local")

    def test_rejects_invalid_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid port"):
            parse_endpoint("gateway.local:0")


class ParseHttpResponseTests(unittest.TestCase):
    """Test HTTP and body-only response parsing."""

    def test_accepts_body_only_response(self) -> None:
        body = b'{"err":"0","unit":[]}'
        self.assertEqual(parse_http_response(body), (body, "body-only"))

    def test_accepts_http_response(self) -> None:
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n\r\n"
            b'{"err":"0","unit":[]}'
        )
        body, transport = parse_http_response(raw)
        self.assertEqual(body, b'{"err":"0","unit":[]}')
        self.assertEqual(transport, "HTTP/1.1")

    def test_rejects_authentication_failure(self) -> None:
        with self.assertRaises(GatewayAuthenticationError):
            parse_http_response(b"HTTP/1.1 401 Unauthorized\r\n\r\n")

    def test_decodes_chunked_response(self) -> None:
        chunks = (b'{"err"', b':"0","unit":[]}')
        chunked_body = b"".join(
            f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n" for chunk in chunks
        )
        raw = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + chunked_body
            + b"0\r\n\r\n"
        )
        body, _ = parse_http_response(raw)
        self.assertEqual(json.loads(body), {"err": "0", "unit": []})


class QueryClientTests(unittest.IsolatedAsyncioTestCase):
    """Test pagination with controlled asynchronous responses."""

    async def test_fetches_all_pages_and_sends_credentials(self) -> None:
        requests: list[bytes] = []

        async def fake_exchange(
            _endpoint: Endpoint,
            request: bytes,
            _max_response_bytes: int,
        ) -> bytes:
            requests.append(request)
            self.assertIn(b"Connection: close\r\n", request)
            request_target = request.split(b" ", 2)[1].decode("ascii")
            page = int(parse_qs(urlsplit(request_target).query)["p"][0])

            if page == 0:
                return json.dumps(
                    {
                        "err": "0",
                        "unit": [
                            {
                                "idx": 4,
                                "oa": 1,
                                "ia": 2,
                                "on": 1,
                                "mode": 1,
                                "tempSet": 24,
                                "tempIn": 25,
                                "fan": 0,
                            }
                        ],
                    }
                ).encode()

            body = b'{"err":0,"unit":[]}'
            return (
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )

        client = ZhonghongQueryClient(
            Endpoint("gateway.local", 80, "gateway.local"),
            "admin",
            "secret",
        )

        with patch("tools.query_gateway._exchange", side_effect=fake_exchange):
            result = await client.fetch_all(4)

        self.assertEqual(result.page_count, 2)
        self.assertEqual(len(result.units), 1)
        self.assertEqual(result.units[0]["idx"], 4)
        self.assertEqual(result.transports, ("body-only", "HTTP/1.1"))
        self.assertEqual([page.page for page in result.pages], [0, 1])
        self.assertGreater(result.pages[0].response_bytes, 0)
        authorization = base64.b64encode(b"admin:secret")
        self.assertIn(b"Authorization: Basic " + authorization, requests[0])

    async def test_rejects_repeated_pages(self) -> None:
        async def repeated_page(_page: int) -> PageResult:
            return PageResult(
                page=_page,
                units=({"idx": 1},),
                transport="body-only",
                response_bytes=30,
                elapsed_ms=1.0,
                error_value="0",
            )

        client = ZhonghongQueryClient(
            Endpoint("gateway.local", 80, "gateway.local"),
            "admin",
            "",
        )

        with (
            patch.object(client, "fetch_page", side_effect=repeated_page),
            self.assertRaisesRegex(GatewayResponseError, "repeated page"),
        ):
            await client.fetch_all(4)


class DebugOutputTests(unittest.TestCase):
    """Test credential-free pagination diagnostics."""

    def test_reports_partial_page_overlap(self) -> None:
        pages = (
            PageResult(
                0,
                ({"oa": 1, "ia": 2, "idx": 0, "nm": "A"},),
                "body-only",
                80,
                1.2,
                "0",
            ),
            PageResult(
                1,
                ({"oa": 1, "ia": 2, "idx": 0, "nm": "A"},),
                "body-only",
                80,
                1.3,
                "0",
            ),
            PageResult(2, (), "body-only", 21, 1.1, 0),
        )
        result = QueryResult(
            units=pages[0].units + pages[1].units,
            page_count=3,
            transports=tuple(page.transport for page in pages),
            pages=pages,
        )

        debug_output = build_debug_output(
            Endpoint("gateway.local", 80, "gateway.local"), result
        )

        self.assertEqual(debug_output["endpoint"], "gateway.local")
        self.assertEqual(debug_output["pages"][2]["unit_count"], 0)
        self.assertEqual(
            debug_output["duplicate_unit_keys"],
            [
                {
                    "oa": 1,
                    "ia": 2,
                    "occurrences": [
                        {"page": 0, "position": 0},
                        {"page": 1, "position": 0},
                    ],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
