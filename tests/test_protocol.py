"""Unit tests for the standalone Zhonghong protocol layers."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import unittest
from collections import deque
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

# Loading the protocol modules through a private package avoids importing the
# Home Assistant-dependent integration __init__ in this lightweight test env.
PACKAGE_NAME = "_zhong_hong_http_protocol_tests"
PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "zhong_hong_http"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault(PACKAGE_NAME, package)

client_module = importlib.import_module(f"{PACKAGE_NAME}.client")
const_module = importlib.import_module(f"{PACKAGE_NAME}.const")
models_module = importlib.import_module(f"{PACKAGE_NAME}.models")
transport_module = importlib.import_module(f"{PACKAGE_NAME}.transport")
profile_module = importlib.import_module(f"{PACKAGE_NAME}.profile")

ZhonghongClient = client_module.ZhonghongClient
IndoorUnit = models_module.IndoorUnit
GatewayState = models_module.GatewayState
ZhonghongDataError = models_module.ZhonghongDataError
parse_gateway_info = models_module.parse_gateway_info
parse_indoor_unit = models_module.parse_indoor_unit
TransportResponse = transport_module.TransportResponse
ZhonghongAuthenticationError = transport_module.ZhonghongAuthenticationError
Endpoint = transport_module.Endpoint
ZhonghongTransport = transport_module.ZhonghongTransport
parse_response = transport_module.parse_response
ZhonghongProfile = profile_module.ZhonghongProfile


class ConfigurationConstantsTests(unittest.TestCase):
    """Test user-configurable polling boundaries."""

    def test_indoor_unit_polling_range_is_five_to_three_hundred_seconds(
        self,
    ) -> None:
        self.assertEqual(const_module.MIN_SCAN_INTERVAL, 5)
        self.assertEqual(const_module.MAX_SCAN_INTERVAL, 300)

    def test_other_climate_refresh_is_opt_in(self) -> None:
        self.assertFalse(const_module.DEFAULT_REFRESH_ON_OTHER_CLIMATE_CHANGES)

    def test_control_readbacks_are_delayed_one_and_two_seconds(self) -> None:
        self.assertEqual(const_module.CONTROL_REFRESH_DELAYS, (1.0, 2.0))


def _unit_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "oa": 1,
        "ia": 4,
        "nm": "Example room",
        "on": 0,
        "mode": 2,
        "alarm": 9,
        "tempSet": "26",
        "tempIn": "29",
        "fan": 4,
        "idx": 3,
        "grp": 0,
        "OnoffLock": 0,
        "tempLock": 0,
        "highestVal": 26,
        "lowestVal": 26,
        "modeLock": 0,
        "FlowDirection1": 0,
        "FlowDirection2": 0,
        "MainRmc": 0,
    }
    payload.update(updates)
    return payload


def _response(payload: dict[str, object]) -> Any:
    return TransportResponse(
        json.dumps(payload, ensure_ascii=False).encode(),
        "body-only",
    )


class FakeTransport:
    """Return controlled responses while recording request parameters."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = deque(responses)
        self.requests: list[tuple[tuple[str, str | int], ...]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def async_request(self, parameters: tuple[tuple[str, str | int], ...]) -> Any:
        self.requests.append(parameters)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0)
        try:
            return self.responses.popleft()
        finally:
            self.in_flight -= 1


class ModelTests(unittest.TestCase):
    """Test strict conversion into domain models."""

    def test_gateway_info_strips_firmware_padding(self) -> None:
        info = parse_gateway_info(
            {
                "err": 0,
                "model": "000",
                "sw": "44.71.00.00.005  ",
                "id": "test-gateway-id",
                "hwerror": "00",
                "moduleerror": "00",
            }
        )

        self.assertEqual(info.model_code, "000")
        self.assertEqual(info.software_version, "44.71.00.00.005")
        self.assertEqual(info.hardware_error_code, "00")
        self.assertEqual(info.module_error_code, "00")

    def test_alarm_and_management_fields_are_ignored(self) -> None:
        first = parse_indoor_unit(_unit_payload(alarm=0))
        second = parse_indoor_unit(_unit_payload(alarm=99, OnoffLock=1))

        self.assertEqual(first, second)
        self.assertFalse(hasattr(first, "alarm"))

    def test_rejects_non_numeric_temperature(self) -> None:
        with self.assertRaisesRegex(ZhonghongDataError, "tempIn"):
            parse_indoor_unit(_unit_payload(tempIn="unknown"))

    def test_gateway_state_equality_only_compares_indoor_units(self) -> None:
        unit = parse_indoor_unit(_unit_payload())

        first = GatewayState({unit.key: unit}, 2, ("body-only", "body-only"))
        second = GatewayState({unit.key: unit}, 3, ("standard",))

        self.assertEqual(first, second)
        self.assertNotEqual(
            first,
            GatewayState(
                {unit.key: parse_indoor_unit(_unit_payload(tempIn="30"))},
                2,
                ("body-only",),
            ),
        )


class ResponseTests(unittest.TestCase):
    """Test standard and body-only response compatibility."""

    def test_accepts_body_only_response(self) -> None:
        body = b'{"err":0}'
        self.assertEqual(parse_response(body), TransportResponse(body, "body-only"))

    def test_accepts_standard_chunked_http_response(self) -> None:
        raw = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b'5\r\n{"err\r\n4\r\n":0}\r\n0\r\n\r\n'
        )
        self.assertEqual(parse_response(raw).body, b'{"err":0}')

    def test_maps_standard_authentication_error(self) -> None:
        with self.assertRaises(ZhonghongAuthenticationError):
            parse_response(b"HTTP/1.1 401 Unauthorized\r\n\r\n")

    def test_endpoint_normalizes_bracketed_ipv6(self) -> None:
        endpoint = Endpoint("[2001:db8::1]", 8080)
        self.assertEqual(endpoint.host, "2001:db8::1")
        self.assertEqual(endpoint.host_header, "[2001:db8::1]:8080")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    """Test the raw asynchronous exchange against a local fake gateway."""

    async def test_sends_http11_and_accepts_body_only_reply(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"err":0}')
        reader.feed_eof()
        writer = Mock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()

        with (
            self.assertLogs(transport_module.LOGGER, level="DEBUG") as logs,
            patch.object(
                transport_module.asyncio,
                "open_connection",
                AsyncMock(return_value=(reader, writer)),
            ),
        ):
            result = await ZhonghongTransport(
                Endpoint("192.0.2.1", 80), "admin", ""
            ).async_request((("f", 1),))

        self.assertEqual(result.body, b'{"err":0}')
        self.assertEqual(result.transport, "body-only")
        request = writer.write.call_args.args[0]
        self.assertIn(b"GET /cgi-bin/api.html?f=1 HTTP/1.1\r\n", request)
        self.assertIn(b"Authorization: Basic YWRtaW46\r\n", request)
        log_output = "\n".join(logs.output)
        self.assertIn("transport=body-only", log_output)
        self.assertNotIn("Authorization", log_output)
        self.assertNotIn("YWRtaW46", log_output)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    """Test pagination, gateway data, controls, and serialization."""

    async def test_queries_gateway_info_with_f1(self) -> None:
        transport = FakeTransport(
            [
                _response(
                    {
                        "err": 0,
                        "model": "000",
                        "sw": "44.71.00.00.005  ",
                        "id": "gateway-id",
                        "hwerror": "00",
                        "moduleerror": "00",
                    }
                )
            ]
        )
        client = ZhonghongClient(transport)

        info = await client.async_query_gateway_info()

        self.assertEqual(info.device_id, "gateway-id")
        self.assertEqual(transport.requests, [(("f", 1),)])

    async def test_aggregates_pages_until_empty_page(self) -> None:
        transport = FakeTransport(
            [
                _response({"err": 0, "unit": [_unit_payload(ia=1, idx=0)]}),
                _response({"err": "0", "unit": [_unit_payload(ia=2, idx=1)]}),
                _response({"err": 0, "unit": []}),
            ]
        )
        client = ZhonghongClient(transport)

        state = await client.async_query_units()

        self.assertEqual(state.page_count, 3)
        self.assertEqual(set(state.units), {(1, 1), (1, 2)})
        self.assertEqual(
            transport.requests,
            [
                (("f", 17), ("p", 0)),
                (("f", 17), ("p", 1)),
                (("f", 17), ("p", 2)),
            ],
        )

    async def test_accepts_empty_first_page(self) -> None:
        transport = FakeTransport([_response({"err": 0, "unit": []})])

        state = await ZhonghongClient(transport).async_query_units()

        self.assertEqual(state.page_count, 1)
        self.assertEqual(state.units, {})

    async def test_rejects_repeated_nonempty_page(self) -> None:
        page = _response({"err": 0, "unit": [_unit_payload()]})
        transport = FakeTransport([page, page])

        with self.assertRaisesRegex(ZhonghongDataError, "repeated a page"):
            await ZhonghongClient(transport).async_query_units()

    async def test_rejects_duplicate_control_index(self) -> None:
        transport = FakeTransport(
            [
                _response(
                    {
                        "err": 0,
                        "unit": [
                            _unit_payload(ia=1, idx=0),
                            _unit_payload(ia=2, idx=0),
                        ],
                    }
                )
            ]
        )

        with self.assertRaisesRegex(ZhonghongDataError, "duplicate indoor-unit index"):
            await ZhonghongClient(transport).async_query_units()

    async def test_rejects_malformed_json_and_unsuccessful_result(self) -> None:
        malformed_transport = FakeTransport(
            [TransportResponse(b"not-json", "body-only")]
        )
        with self.assertRaisesRegex(ZhonghongDataError, "valid UTF-8 JSON"):
            await ZhonghongClient(malformed_transport).async_query_units()

        error_transport = FakeTransport([_response({"err": 7, "unit": []})])
        with self.assertRaisesRegex(client_module.ZhonghongApiError, "err=7"):
            await ZhonghongClient(error_transport).async_query_units()

    async def test_stops_at_page_limit(self) -> None:
        transport = FakeTransport([_response({"err": 0, "unit": [_unit_payload()]})])
        with (
            patch.object(client_module, "MAX_PAGES", 1),
            self.assertRaisesRegex(ZhonghongDataError, "within 1 pages"),
        ):
            await ZhonghongClient(transport).async_query_units()

    async def test_control_sends_complete_confirmed_field_set(self) -> None:
        transport = FakeTransport([_response({"err": 0})])
        client = ZhonghongClient(transport)
        unit = parse_indoor_unit(_unit_payload())

        desired = await client.async_control(unit, is_on=True, target_temperature=25)

        self.assertEqual(
            transport.requests,
            [
                (
                    ("f", 18),
                    ("on", 1),
                    ("mode", 2),
                    ("tempSet", 25),
                    ("fan", 4),
                    ("idx", 3),
                )
            ],
        )
        parameter_names = {name for name, _value in transport.requests[0]}
        self.assertNotIn("FlowDirection1", parameter_names)
        self.assertNotIn("FlowDirection2", parameter_names)
        self.assertNotIn("alarm", parameter_names)
        self.assertTrue(desired.is_on)
        self.assertEqual(desired.target_temperature, 25)

    async def test_control_preserves_unmapped_cached_enums(self) -> None:
        transport = FakeTransport([_response({"err": 0})])
        client = ZhonghongClient(transport)
        unit = parse_indoor_unit(_unit_payload(mode=16, fan=6))

        await client.async_control(unit, is_on=False)

        self.assertIn(("mode", 16), transport.requests[0])
        self.assertIn(("fan", 6), transport.requests[0])

    async def test_alternate_temperature_profile_is_enforced(self) -> None:
        profile = ZhonghongProfile(
            minimum_temperature=18,
            maximum_temperature=30,
            target_temperature_step=1,
        )
        transport = FakeTransport([_response({"err": 0})])
        client = ZhonghongClient(transport, profile)
        unit = parse_indoor_unit(_unit_payload())

        with self.assertRaisesRegex(ValueError, "between 18 and 30"):
            await client.async_control(unit, target_temperature=17)
        await client.async_control(unit, target_temperature=18)

    async def test_control_requests_are_serialized_by_gateway(self) -> None:
        transport = FakeTransport([_response({"err": 0}), _response({"err": 0})])
        client = ZhonghongClient(transport)
        unit = parse_indoor_unit(_unit_payload())

        await asyncio.gather(
            client.async_control(unit, is_on=True),
            client.async_control(unit, target_temperature=25),
        )

        self.assertEqual(transport.max_in_flight, 1)

    async def test_whole_query_has_total_timeout(self) -> None:
        class SlowTransport(FakeTransport):
            async def async_request(
                self, parameters: tuple[tuple[str, str | int], ...]
            ) -> Any:
                await asyncio.sleep(1)
                return _response({"err": 0, "unit": []})

        with (
            patch.object(client_module, "QUERY_TIMEOUT", 0.001),
            self.assertRaisesRegex(
                transport_module.ZhonghongTransportError, "query timed out"
            ),
        ):
            await ZhonghongClient(SlowTransport([])).async_query_units()


if __name__ == "__main__":
    unittest.main()
