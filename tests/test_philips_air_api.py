import base64
import hashlib
import sys
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from philips_air_api import (  # noqa: E402
    CRYPTO_AVAILABLE,
    HomeIDAESCrypto,
    ObserveDaemon,
    PARAM_LIGHT,
    PARAM_MODE,
    PARAM_POWER,
    PhilipsCondorAuth,
    parse_status,
)
try:
    from aioairctrl.coap import aiocoap_monkeypatch  # noqa: E402
except ModuleNotFoundError:
    aiocoap_monkeypatch = None


class ParseStatusTests(unittest.TestCase):
    def test_coap_status_keeps_normalised_mode_name(self):
        sensors = parse_status({
            "D03102": 1,
            "D0310C": 18,
            "D03104": 123,
            "D03103": 0,
            "D03221": 8,
            "D03120": 2,
        })

        self.assertTrue(sensors["power"])
        self.assertEqual(sensors["mode"], "turbo")
        self.assertEqual(sensors["mode_name"], "turbo")
        self.assertEqual(sensors["pm25"], 8)
        self.assertEqual(sensors["iaql"], 2)
        self.assertEqual(sensors["light_level"], 123)
        self.assertFalse(sensors["child_lock"])

    def test_dh_http_status_uses_short_field_names(self):
        sensors = parse_status({
            "pwr": "1",
            "mode": "M",
            "om": "s",
            "pm25": "12",
            "iaql": "4",
            "aqil": "50",
            "cl": "1",
        })

        self.assertTrue(sensors["power"])
        self.assertEqual(sensors["mode"], "sleep")
        self.assertEqual(sensors["mode_name"], "sleep")
        self.assertEqual(sensors["pm25"], 12)
        self.assertEqual(sensors["iaql"], 4)
        self.assertEqual(sensors["light_level"], 115)
        self.assertTrue(sensors["child_lock"])

    def test_homeid_merged_status_defaults_missing_filter_data_to_ok(self):
        sensors = parse_status({
            "pwr": "1",
            "mode": "A",
            "pm25": 5,
            "aqil": "100",
            "cl": False,
            "temp": 22,
            "rh": 48,
        })

        self.assertEqual(sensors["mode"], "auto")
        self.assertEqual(sensors["filter_life_percent"], 100)
        self.assertEqual(sensors["cleanup_percent"], 100)
        self.assertEqual(sensors["temperature"], 22)
        self.assertEqual(sensors["humidity"], 48)

    def test_ac1715_hyphenated_fields_normalise_power_and_mode(self):
        sensors = parse_status({
            "D01-05": "AC1715/10",
            "D03-02": "ON",
            "D03-05": 100,
            "D03-11": "A",
            "D03-12": "Auto General",
            "D03102": 0,
        })

        self.assertTrue(sensors["power"])
        self.assertEqual(sensors["mode"], "auto")
        self.assertEqual(sensors["light_level"], 123)


class AirPlusParsStatusTests(unittest.TestCase):
    def test_airplus_d0310d_normalised_to_power(self):
        """D0310D (Air+ MQTT power) is normalised to D03102 so parse_status picks it up."""
        raw = {"D0310D": 1, "D0310C": 18, "D03221": 8}
        result = parse_status(raw)
        self.assertTrue(result["power"])
        self.assertEqual(result["mode"], "turbo")
        self.assertEqual(result["pm25"], 8)

    def test_airplus_ac0650_auto_mode_value_1(self):
        """AC0650 reports auto as D0310C=1, not 0 — must parse as 'auto' not 'unknown'."""
        raw = {"D0310D": 1, "D0310C": 1, "D03221": 5}
        result = parse_status(raw)
        self.assertTrue(result["power"])
        self.assertEqual(result["mode"], "auto")
        self.assertEqual(result["pm25"], 5)

    def test_airplus_d0310d_does_not_overwrite_existing_d03102(self):
        """If D03102 is already present, D0310D must not clobber it."""
        raw = {"D0310D": 0, "D03102": 1, "D0310C": 19}
        result = parse_status(raw)
        # D03102=1 wins; D0310D=0 is ignored
        self.assertTrue(result["power"])
        self.assertEqual(result["mode"], "medium")


class HomeIDCryptoTests(unittest.TestCase):
    def test_homeid_aes_round_trip(self):
        if not CRYPTO_AVAILABLE:
            self.skipTest("pycryptodomex is not installed in this Python environment")

        test_aes_key = bytes(range(16)).hex()
        payload = {"pwr": "1", "mode": "A"}

        encrypted = HomeIDAESCrypto.encrypt(payload, test_aes_key)
        decrypted = HomeIDAESCrypto.decrypt(encrypted, test_aes_key)

        self.assertEqual(decrypted, '{"pwr": "1", "mode": "A"}')

    def test_philips_condor_auth_response(self):
        challenge = b"12345678"
        client_id = b"client-id-123456"
        client_secret = b"client-secret-123456"
        challenge_header = "PHILIPS-Condor " + base64.b64encode(challenge).decode()
        client_id_b64 = base64.b64encode(client_id).decode()
        client_secret_b64 = base64.b64encode(client_secret).decode()

        response = PhilipsCondorAuth.create_credentials(
            challenge_header,
            client_id_b64,
            client_secret_b64,
        )

        expected_digest = hashlib.sha256(challenge + client_id + client_secret).digest()
        expected = "PHILIPS-Condor " + base64.b64encode(client_id + expected_digest).decode()
        self.assertEqual(response, expected)


class _FakeCoAPClient:
    def __init__(self, result=True, statuses=None, status_error=None):
        self.result = result
        self.statuses = list(statuses or [])
        self.status_error = status_error
        self.shutdown_calls = 0
        self.control_calls = []
        self.status_calls = 0

    async def shutdown(self):
        self.shutdown_calls += 1

    async def set_control_value(self, key, value):
        self.control_calls.append((key, value))
        return self.result

    async def get_status(self):
        self.status_calls += 1
        if self.status_error:
            raise self.status_error
        if not self.statuses:
            raise RuntimeError("No fake status configured")
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]


class ObserveDaemonTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_state_persists_for_restart_field_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            daemon = ObserveDaemon("192.0.2.1")
            daemon._state_cache_path = state_file
            with patch("builtins.print"):
                daemon._publish_status({
                    "D03-02": "OFF",
                    "D03-12": "Auto General",
                })

            restored = ObserveDaemon("192.0.2.1")
            restored._state_cache_path = state_file
            restored._cached_state = None
            restored._load_persisted_state()
            self.assertEqual(restored._cached_state, {
                "D03-02": "OFF",
                "D03-12": "Auto General",
            })

    async def test_disconnect_does_not_cancel_or_await_itself(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient()
        daemon._client = client
        daemon._connected = True

        async def disconnect_from_observe_task():
            daemon._observe_task = asyncio.current_task()
            await daemon.disconnect()

        await disconnect_from_observe_task()
        self.assertEqual(client.shutdown_calls, 1)
        self.assertIsNone(daemon._client)

    async def test_control_fails_when_current_state_cannot_be_refreshed(self):
        daemon = ObserveDaemon("192.0.2.1")
        daemon._client = _FakeCoAPClient(status_error=TimeoutError("offline"))
        daemon._connected = True

        with self.assertRaisesRegex(Exception, "Unable to refresh device state"):
            await daemon._send_control(PARAM_POWER, 1)

    async def test_stale_state_is_refreshed_and_command_is_polled_to_confirmation(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient(statuses=[
            {"D03-02": "OFF"},
            {"D03-02": "ON"},
        ])
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon._cached_state = {"D03-02": "OFF"}
        daemon._last_update = time.time() - 600
        daemon.COMMAND_CONFIRM_TIMEOUT = 1

        with patch("builtins.print"):
            await daemon._send_control(PARAM_POWER, 1)

        self.assertEqual(client.control_calls, [("D03-02", "ON")])
        self.assertEqual(client.status_calls, 2)
        self.assertEqual(daemon._cached_state, {"D03-02": "ON"})

    async def test_heartbeat_refreshes_state_without_reconnecting_observer(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient(statuses=[{"D03-02": "OFF"}])
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon.HEARTBEAT_INTERVAL = 0.01

        with patch("builtins.print"):
            heartbeat = asyncio.create_task(daemon._heartbeat_loop())
            await asyncio.sleep(0.025)
            daemon._shutdown_event.set()
            await heartbeat

        self.assertGreaterEqual(client.status_calls, 1)
        self.assertEqual(client.shutdown_calls, 0)
        self.assertEqual(daemon._cached_state, {"D03-02": "OFF"})

    async def test_control_waits_for_matching_observe_update(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient()
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon._cached_state = {PARAM_POWER: 0}
        daemon._last_update = time.time()
        daemon.COMMAND_CONFIRM_TIMEOUT = 0.5

        async def confirm_update():
            await asyncio.sleep(0.05)
            daemon._cached_state = {PARAM_POWER: 1}
            daemon._last_update = time.time()

        update_task = asyncio.create_task(confirm_update())
        await daemon._send_control(PARAM_POWER, 1)
        await update_task
        self.assertEqual(client.control_calls, [(PARAM_POWER, 1)])

    async def test_control_uses_device_ack_when_observe_update_is_missing(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient()
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon._cached_state = {PARAM_POWER: 0}
        daemon._last_update = time.time()
        daemon.COMMAND_CONFIRM_TIMEOUT = 0.01

        with patch("builtins.print"):
            await daemon._send_control(PARAM_POWER, 1)

        self.assertEqual(daemon._cached_state, {PARAM_POWER: 1})
        self.assertEqual(client.control_calls, [(PARAM_POWER, 1)])

    async def test_airplus_power_uses_d0310d_and_confirms_alias(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient()
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon._cached_state = {"D0310D": 0}
        daemon._last_update = time.time()
        daemon.COMMAND_CONFIRM_TIMEOUT = 0.5

        async def confirm_update():
            await asyncio.sleep(0.05)
            daemon._cached_state = {"D0310D": 1}
            daemon._last_update = time.time()

        update_task = asyncio.create_task(confirm_update())
        await daemon._send_control(PARAM_POWER, 1)
        await update_task
        self.assertEqual(client.control_calls, [("D0310D", 1)])
        self.assertEqual(await daemon._execute_command("power", []), {"power": True})

    async def test_ac1715_power_uses_hyphenated_string_field(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient()
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon._cached_state = {"D03-02": "OFF", PARAM_POWER: 0}
        daemon._last_update = time.time()
        daemon.COMMAND_CONFIRM_TIMEOUT = 0.5

        async def confirm_update():
            await asyncio.sleep(0.05)
            daemon._cached_state = {"D03-02": "ON", PARAM_POWER: 0}
            daemon._last_update = time.time()

        update_task = asyncio.create_task(confirm_update())
        await daemon._send_control(PARAM_POWER, 1)
        await update_task
        self.assertEqual(client.control_calls, [("D03-02", "ON")])
        self.assertEqual(await daemon._execute_command("power", []), {"power": True})

    async def test_ac1715_light_uses_d03_05_and_boolean_brightness(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient()
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon._cached_state = {"D03-02": "OFF", "D03-05": 100}
        daemon._last_update = time.time()
        daemon.COMMAND_CONFIRM_TIMEOUT = 0.5

        async def confirm_update():
            await asyncio.sleep(0.05)
            daemon._cached_state = {"D03-02": "OFF", "D03-05": 0}
            daemon._last_update = time.time()

        update_task = asyncio.create_task(confirm_update())
        await daemon._send_control(PARAM_LIGHT, 0)
        await update_task
        self.assertEqual(client.control_calls, [("D03-05", 0)])
        self.assertEqual(await daemon._execute_command("light", []), {"light": 0})

    async def test_ac1715_mode_uses_d03_12_and_confirms_label(self):
        daemon = ObserveDaemon("192.0.2.1")
        client = _FakeCoAPClient()
        daemon._client = client
        daemon._connected = True
        daemon._observing = True
        daemon._cached_state = {
            "D03-02": "ON",
            "D03-11": "S",
            "D03-12": "Sleep",
        }
        daemon._last_update = time.time()
        daemon.COMMAND_CONFIRM_TIMEOUT = 0.5

        async def confirm_update():
            await asyncio.sleep(0.05)
            daemon._cached_state = {
                "D03-02": "ON",
                "D03-11": "A",
                "D03-12": "Auto General",
            }
            daemon._last_update = time.time()

        update_task = asyncio.create_task(confirm_update())
        await daemon._send_control(PARAM_MODE, 1)
        await update_task
        self.assertEqual(client.control_calls, [("D03-12", "Auto General")])
        self.assertEqual(await daemon._execute_command("mode", []), {"mode": "auto"})

    async def test_ac1715_turbo_and_gentle_use_model_labels(self):
        cases = (
            (18, "Turbo", "turbo"),
            (20, "Gentle/Speed 1", "gentle"),
            (19, "Speed 2", "medium"),
        )
        for target, label, normalised in cases:
            with self.subTest(label=label):
                daemon = ObserveDaemon("192.0.2.1")
                client = _FakeCoAPClient()
                daemon._client = client
                daemon._connected = True
                daemon._observing = True
                daemon._cached_state = {
                    "D03-02": "ON",
                    "D03-12": "Auto General",
                }
                daemon._last_update = time.time()
                daemon.COMMAND_CONFIRM_TIMEOUT = 0.5

                async def confirm_update():
                    await asyncio.sleep(0.05)
                    daemon._cached_state = {"D03-02": "ON", "D03-12": label}
                    daemon._last_update = time.time()

                update_task = asyncio.create_task(confirm_update())
                await daemon._send_control(PARAM_MODE, target)
                await update_task
                self.assertEqual(client.control_calls, [("D03-12", label)])
                self.assertEqual(
                    await daemon._execute_command("mode", []),
                    {"mode": normalised},
                )


@unittest.skipIf(aiocoap_monkeypatch is None, "aiocoap is not installed")
class StableCoAPPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_philips_coap_uses_stable_local_udp_port(self):
        endpoint = AsyncMock(return_value=(object(), object()))
        with patch.object(aiocoap_monkeypatch, "_create_datagram_endpoint", endpoint):
            await aiocoap_monkeypatch._create_datagram_endpoint_with_stable_coap_port(
                object(), object(), remote_addr=("192.0.2.1", 5683)
            )

        self.assertEqual(
            endpoint.await_args.kwargs["local_addr"],
            ("0.0.0.0", aiocoap_monkeypatch._PHILIPS_COAP_LOCAL_PORT),
        )

    async def test_non_coap_udp_does_not_get_forced_local_port(self):
        endpoint = AsyncMock(return_value=(object(), object()))
        with patch.object(aiocoap_monkeypatch, "_create_datagram_endpoint", endpoint):
            await aiocoap_monkeypatch._create_datagram_endpoint_with_stable_coap_port(
                object(), object(), remote_addr=("192.0.2.1", 1234)
            )

        self.assertNotIn("local_addr", endpoint.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
