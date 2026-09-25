"""Asynchronous transport for body-only Zhonghong HTTP responses."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv6Address
from urllib.parse import urlencode

from .const import DEFAULT_PORT, MAX_RESPONSE_BYTES, REQUEST_TIMEOUT

READ_SIZE = 64 * 1024


class ZhonghongTransportError(Exception):
    """Raised when communication with a gateway fails."""


class ZhonghongAuthenticationError(ZhonghongTransportError):
    """Raised when the gateway rejects HTTP Basic authentication."""


class ZhonghongResponseError(ZhonghongTransportError):
    """Raised when a response cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Validated local HTTP endpoint."""

    host: str
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        """Validate connection and Host-header values."""
        host = self.host.strip()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if not host or any(
            character.isspace() or ord(character) < 33 or ord(character) == 127
            for character in host
        ):
            raise ValueError("host must be a non-empty hostname or IP address")
        if any(character in host for character in "/?#@"):
            raise ValueError("host must not contain a URL path or credentials")
        if ":" in host:
            try:
                host = str(IPv6Address(host))
            except ValueError as err:
                raise ValueError("host is not a valid IPv6 address") from err
        else:
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError as err:
                raise ValueError("host is not a valid hostname") from err
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        object.__setattr__(self, "host", host)

    @property
    def host_header(self) -> str:
        """Return an HTTP Host header supporting IPv6 literals."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == DEFAULT_PORT else f"{host}:{self.port}"

    @property
    def configuration_url(self) -> str:
        """Return a URL suitable for the device registry."""
        return f"http://{self.host_header}"


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Body and protocol metadata returned by the gateway."""

    body: bytes
    transport: str


def _decode_chunked_body(body: bytes) -> bytes:
    """Decode an HTTP chunked response body."""
    decoded = bytearray()
    position = 0

    while True:
        line_end = body.find(b"\r\n", position)
        separator_length = 2
        if line_end == -1:
            line_end = body.find(b"\n", position)
            separator_length = 1
        if line_end == -1:
            raise ZhonghongResponseError("incomplete chunk-size line")

        size_text = body[position:line_end].split(b";", 1)[0].strip()
        try:
            chunk_size = int(size_text, 16)
        except ValueError as err:
            raise ZhonghongResponseError("invalid chunk-size line") from err

        position = line_end + separator_length
        if chunk_size == 0:
            return bytes(decoded)
        chunk_end = position + chunk_size
        if chunk_end > len(body):
            raise ZhonghongResponseError("incomplete chunked response body")
        decoded.extend(body[position:chunk_end])
        position = chunk_end

        if body[position : position + 2] == b"\r\n":
            position += 2
        elif body[position : position + 1] == b"\n":
            position += 1
        else:
            raise ZhonghongResponseError("chunk is missing its line terminator")


def parse_response(raw_response: bytes) -> TransportResponse:
    """Parse either a standard HTTP response or a body-only response."""
    if not raw_response:
        raise ZhonghongResponseError("gateway returned an empty response")
    if not raw_response.startswith(b"HTTP/"):
        return TransportResponse(raw_response, "body-only")

    header_end = raw_response.find(b"\r\n\r\n")
    separator_length = 4
    if header_end == -1:
        header_end = raw_response.find(b"\n\n")
        separator_length = 2
    if header_end == -1:
        raise ZhonghongResponseError("HTTP response headers are incomplete")

    header_lines = (
        raw_response[:header_end].decode("iso-8859-1").replace("\r\n", "\n").split("\n")
    )
    status_parts = header_lines[0].split(None, 2)
    if len(status_parts) < 2:
        raise ZhonghongResponseError("HTTP status line is invalid")
    try:
        status = int(status_parts[1])
    except ValueError as err:
        raise ZhonghongResponseError("HTTP status code is invalid") from err
    if status in (401, 403):
        raise ZhonghongAuthenticationError(
            f"gateway rejected credentials with HTTP status {status}"
        )
    if not 200 <= status < 300:
        raise ZhonghongResponseError(f"gateway returned HTTP status {status}")

    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise ZhonghongResponseError("HTTP response contains an invalid header")
        headers[name.strip().lower()] = value.strip()

    content_encoding = headers.get("content-encoding", "identity").lower()
    if content_encoding not in ("", "identity"):
        raise ZhonghongResponseError(
            f"unsupported HTTP content encoding: {content_encoding}"
        )

    body = raw_response[header_end + separator_length :]
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = _decode_chunked_body(body)
    return TransportResponse(body, status_parts[0])


class ZhonghongTransport:
    """Send HTTP requests without assuming a response status line exists."""

    def __init__(
        self,
        endpoint: Endpoint,
        username: str,
        password: str,
        *,
        request_timeout: float = REQUEST_TIMEOUT,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        """Initialize the transport."""
        self.endpoint = endpoint
        self._request_timeout = request_timeout
        self._max_response_bytes = max_response_bytes
        credentials = f"{username}:{password}".encode()
        self._authorization = base64.b64encode(credentials).decode("ascii")

    async def async_request(
        self, parameters: Sequence[tuple[str, str | int]]
    ) -> TransportResponse:
        """Send a GET request and return the parsed body."""
        target = f"/cgi-bin/api.html?{urlencode(parameters)}"
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {self.endpoint.host_header}\r\n"
            f"Authorization: Basic {self._authorization}\r\n"
            "Accept: application/json\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")

        try:
            raw_response = await asyncio.wait_for(
                self._async_exchange(request),
                timeout=self._request_timeout,
            )
        except TimeoutError as err:
            raise ZhonghongTransportError(
                f"gateway request timed out after {self._request_timeout:g} seconds"
            ) from err
        return parse_response(raw_response)

    async def _async_exchange(self, request: bytes) -> bytes:
        """Exchange bytes with the gateway over one TCP connection."""
        try:
            reader, writer = await asyncio.open_connection(
                self.endpoint.host, self.endpoint.port
            )
        except OSError as err:
            raise ZhonghongTransportError("could not connect to gateway") from err

        try:
            writer.write(request)
            await writer.drain()
            response = bytearray()
            while chunk := await reader.read(READ_SIZE):
                response.extend(chunk)
                if len(response) > self._max_response_bytes:
                    raise ZhonghongResponseError(
                        "gateway response exceeds the configured size limit"
                    )
            return bytes(response)
        except OSError as err:
            raise ZhonghongTransportError("gateway connection failed") from err
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
