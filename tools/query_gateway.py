#!/usr/bin/env python3
"""Query indoor-unit data from a Zhonghong VRF Admin Panel."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urlsplit

DEFAULT_USER = "admin"
DEFAULT_PASSWORD = ""
DEFAULT_PORT = 80
DEFAULT_REQUEST_TIMEOUT = 10.0
DEFAULT_TOTAL_TIMEOUT = 60.0
DEFAULT_MAX_PAGES = 128
MAX_RESPONSE_BYTES = 1024 * 1024
READ_SIZE = 64 * 1024


class GatewayError(Exception):
    """Base exception for gateway query failures."""


class GatewayConnectionError(GatewayError):
    """Raised when the gateway cannot be reached."""


class GatewayAuthenticationError(GatewayError):
    """Raised when the gateway rejects the supplied credentials."""


class GatewayResponseError(GatewayError):
    """Raised when the gateway returns an invalid or unsuccessful response."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Validated HTTP endpoint."""

    host: str
    port: int
    host_header: str


@dataclass(frozen=True, slots=True)
class PageResult:
    """Parsed result from one status page."""

    page: int
    units: tuple[dict[str, Any], ...]
    transport: str
    response_bytes: int
    elapsed_ms: float
    error_value: Any


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Aggregated result from all status pages."""

    units: tuple[dict[str, Any], ...]
    page_count: int
    transports: tuple[str, ...]
    pages: tuple[PageResult, ...]


def parse_endpoint(value: str) -> Endpoint:
    """Parse a host, optional port, or HTTP URL into a safe endpoint."""
    raw_value = value.strip()
    if not raw_value:
        raise ValueError("host must not be empty")
    if any(character.isspace() for character in raw_value):
        raise ValueError("host contains invalid whitespace")

    parsed = urlsplit(raw_value if "://" in raw_value else f"http://{raw_value}")
    if parsed.scheme.lower() != "http":
        raise ValueError("only plain HTTP endpoints are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("pass credentials with --user and --password")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("host must not contain a path, query, or fragment")
    if parsed.hostname is None:
        raise ValueError("host is invalid")

    try:
        parsed_port = parsed.port
    except ValueError as err:
        raise ValueError("host contains an invalid port") from err
    port = DEFAULT_PORT if parsed_port is None else parsed_port
    if not 1 <= port <= 65535:
        raise ValueError("host contains an invalid port")

    display_host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    host_header = display_host if port == DEFAULT_PORT else f"{display_host}:{port}"
    return Endpoint(parsed.hostname, port, host_header)


def _decode_chunked_body(body: bytes) -> bytes:
    """Decode a basic HTTP chunked body."""
    decoded = bytearray()
    position = 0

    while True:
        line_end = body.find(b"\r\n", position)
        line_separator_length = 2
        if line_end == -1:
            line_end = body.find(b"\n", position)
            line_separator_length = 1
        if line_end == -1:
            raise GatewayResponseError("incomplete chunk-size line")

        size_text = body[position:line_end].split(b";", 1)[0].strip()
        try:
            chunk_size = int(size_text, 16)
        except ValueError as err:
            raise GatewayResponseError("invalid chunk-size line") from err

        position = line_end + line_separator_length
        if chunk_size == 0:
            return bytes(decoded)
        chunk_end = position + chunk_size
        if chunk_end > len(body):
            raise GatewayResponseError("incomplete chunked response body")

        decoded.extend(body[position:chunk_end])
        position = chunk_end
        if body[position : position + 2] == b"\r\n":
            position += 2
        elif body[position : position + 1] == b"\n":
            position += 1
        else:
            raise GatewayResponseError("chunk is missing its line terminator")


def parse_http_response(raw_response: bytes) -> tuple[bytes, str]:
    """Extract a body from HTTP/1.x or body-only HTTP/0.9-style output."""
    if not raw_response:
        raise GatewayResponseError("gateway returned an empty response")

    if not raw_response.startswith(b"HTTP/"):
        return raw_response, "body-only"

    header_end = raw_response.find(b"\r\n\r\n")
    separator_length = 4
    if header_end == -1:
        header_end = raw_response.find(b"\n\n")
        separator_length = 2
    if header_end == -1:
        raise GatewayResponseError("HTTP response headers are incomplete")

    header_bytes = raw_response[:header_end]
    body = raw_response[header_end + separator_length :]
    header_lines = header_bytes.decode("iso-8859-1").replace("\r\n", "\n").split("\n")
    status_parts = header_lines[0].split(None, 2)
    if len(status_parts) < 2:
        raise GatewayResponseError("HTTP status line is invalid")
    try:
        status = int(status_parts[1])
    except ValueError as err:
        raise GatewayResponseError("HTTP status code is invalid") from err

    if status in (401, 403):
        raise GatewayAuthenticationError(
            f"gateway rejected credentials with HTTP status {status}"
        )
    if not 200 <= status < 300:
        raise GatewayResponseError(f"gateway returned HTTP status {status}")

    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise GatewayResponseError("HTTP response contains an invalid header")
        headers[name.strip().lower()] = value.strip()

    content_encoding = headers.get("content-encoding", "identity").lower()
    if content_encoding not in ("", "identity"):
        raise GatewayResponseError(
            f"unsupported HTTP content encoding: {content_encoding}"
        )
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = _decode_chunked_body(body)

    return body, status_parts[0]


async def _exchange(
    endpoint: Endpoint,
    request: bytes,
    max_response_bytes: int,
) -> bytes:
    """Send one request and read the response until the gateway closes it."""
    try:
        reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
    except (OSError, asyncio.TimeoutError) as err:
        raise GatewayConnectionError(f"could not connect to gateway: {err}") from err

    try:
        writer.write(request)
        await writer.drain()

        response = bytearray()
        while True:
            chunk = await reader.read(READ_SIZE)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > max_response_bytes:
                raise GatewayResponseError(
                    f"gateway response exceeds {max_response_bytes} bytes"
                )
        return bytes(response)
    except OSError as err:
        raise GatewayConnectionError(f"gateway connection failed: {err}") from err
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


class ZhonghongQueryClient:
    """Minimal asynchronous client for the Zhonghong status endpoint."""

    def __init__(
        self,
        endpoint: Endpoint,
        user: str,
        password: str,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        """Initialize the query client."""
        self._endpoint = endpoint
        self._request_timeout = request_timeout
        self._max_response_bytes = max_response_bytes
        credentials = f"{user}:{password}".encode()
        self._authorization = base64.b64encode(credentials).decode("ascii")

    async def fetch_page(self, page: int) -> PageResult:
        """Fetch and validate one status page."""
        query = urlencode({"f": 17, "p": page})
        request = (
            f"GET /cgi-bin/api.html?{query} HTTP/1.1\r\n"
            f"Host: {self._endpoint.host_header}\r\n"
            f"Authorization: Basic {self._authorization}\r\n"
            "Accept: application/json\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")

        started_at = monotonic()
        try:
            raw_response = await asyncio.wait_for(
                _exchange(
                    self._endpoint,
                    request,
                    self._max_response_bytes,
                ),
                timeout=self._request_timeout,
            )
        except TimeoutError as err:
            raise GatewayConnectionError(
                f"gateway request timed out after {self._request_timeout:g} seconds"
            ) from err
        elapsed_ms = (monotonic() - started_at) * 1000

        body, transport = parse_http_response(raw_response)
        try:
            payload = json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise GatewayResponseError(
                "gateway response is not valid UTF-8 JSON"
            ) from err

        if not isinstance(payload, dict):
            raise GatewayResponseError("gateway JSON response must be an object")
        error_value = payload.get("err")
        is_success = error_value == "0" or (
            type(error_value) is int and error_value == 0
        )
        if not is_success:
            raise GatewayResponseError(
                f"gateway reported an unsuccessful result: err={error_value!r}"
            )

        units = payload.get("unit")
        if not isinstance(units, list):
            raise GatewayResponseError("gateway JSON field 'unit' must be an array")
        if not all(isinstance(unit, dict) for unit in units):
            raise GatewayResponseError(
                "every item in gateway JSON field 'unit' must be an object"
            )

        return PageResult(
            page=page,
            units=tuple(units),
            transport=transport,
            response_bytes=len(raw_response),
            elapsed_ms=elapsed_ms,
            error_value=error_value,
        )

    async def fetch_all(self, max_pages: int) -> QueryResult:
        """Fetch all pages until the gateway returns an empty unit array."""
        all_units: list[dict[str, Any]] = []
        transports: list[str] = []
        pages: list[PageResult] = []
        seen_pages: set[str] = set()

        for page in range(max_pages):
            result = await self.fetch_page(page)
            transports.append(result.transport)
            pages.append(result)
            if not result.units:
                return QueryResult(
                    tuple(all_units),
                    page + 1,
                    tuple(transports),
                    tuple(pages),
                )

            fingerprint = json.dumps(
                result.units,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint in seen_pages:
                raise GatewayResponseError(
                    f"gateway repeated page data at page {page}; pagination aborted"
                )
            seen_pages.add(fingerprint)
            all_units.extend(result.units)

        raise GatewayResponseError(
            f"gateway did not return an empty page within {max_pages} pages"
        )


def build_debug_output(endpoint: Endpoint, result: QueryResult) -> dict[str, Any]:
    """Build credential-free pagination diagnostics for verbose output."""
    occurrences: dict[tuple[Any, Any], list[dict[str, int]]] = {}
    pages: list[dict[str, Any]] = []

    for page in result.pages:
        unit_summaries: list[dict[str, Any]] = []
        for position, unit in enumerate(page.units):
            summary = {
                "position": position,
                "oa": unit.get("oa"),
                "ia": unit.get("ia"),
                "idx": unit.get("idx"),
                "nm": unit.get("nm"),
            }
            unit_summaries.append(summary)

            oa = unit.get("oa")
            ia = unit.get("ia")
            if isinstance(oa, (str, int)) and isinstance(ia, (str, int)):
                occurrences.setdefault((oa, ia), []).append(
                    {"page": page.page, "position": position}
                )

        pages.append(
            {
                "page": page.page,
                "request_target": f"/cgi-bin/api.html?f=17&p={page.page}",
                "transport": page.transport,
                "response_bytes": page.response_bytes,
                "elapsed_ms": round(page.elapsed_ms, 1),
                "err": page.error_value,
                "err_type": type(page.error_value).__name__,
                "unit_count": len(page.units),
                "units": unit_summaries,
            }
        )

    duplicates = [
        {
            "oa": oa,
            "ia": ia,
            "occurrences": locations,
        }
        for (oa, ia), locations in occurrences.items()
        if len(locations) > 1
    ]
    return {
        "endpoint": endpoint.host_header,
        "pages": pages,
        "duplicate_unit_keys": duplicates,
    }


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Query indoor-unit data from a Zhonghong VRF Admin Panel."
    )
    parser.add_argument(
        "--host",
        required=True,
        help="Gateway host, optional port, or http:// URL without a path.",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_USER,
        help=f"HTTP Basic Auth user (default: {DEFAULT_USER}).",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help="HTTP Basic Auth password (default: empty).",
    )
    parser.add_argument(
        "--request-timeout",
        type=_positive_float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help=(
            f"Timeout for each page in seconds (default: {DEFAULT_REQUEST_TIMEOUT:g})."
        ),
    )
    parser.add_argument(
        "--total-timeout",
        type=_positive_float,
        default=DEFAULT_TOTAL_TIMEOUT,
        help=(
            "Timeout for the full query in seconds "
            f"(default: {DEFAULT_TOTAL_TIMEOUT:g})."
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=_positive_int,
        default=DEFAULT_MAX_PAGES,
        help=f"Maximum number of pages to query (default: {DEFAULT_MAX_PAGES}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detected response transport details to stderr.",
    )
    return parser


async def async_main(args: argparse.Namespace) -> int:
    """Execute the gateway query."""
    try:
        endpoint = parse_endpoint(args.host)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    client = ZhonghongQueryClient(
        endpoint,
        args.user,
        args.password,
        request_timeout=args.request_timeout,
    )
    try:
        result = await asyncio.wait_for(
            client.fetch_all(args.max_pages),
            timeout=args.total_timeout,
        )
    except TimeoutError:
        print(
            f"error: full query timed out after {args.total_timeout:g} seconds",
            file=sys.stderr,
        )
        return 1
    except GatewayError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    if args.verbose:
        print(
            "pagination debug:\n"
            + json.dumps(
                build_debug_output(endpoint, result),
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )

    output = {
        "page_count": result.page_count,
        "unit_count": len(result.units),
        "units": result.units,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line program."""
    args = build_argument_parser().parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
