"""EaseeCharger against a fake charger that speaks the real protocol."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_frames import CONFIG_BEFORE, DEBUG_SAMPLE, STATE_CHARGING

from easee_ble import client as client_module
from easee_ble import crypto
from easee_ble.client import DEFAULT_CHANNELS, EaseeCharger
from easee_ble.exceptions import EaseeCommandRefused, EaseeConnectionError, RecordError
from easee_ble.jpake import SERVER, ECJPake
from easee_ble.protocol import SERVICE_UUID, Channel

PIN = "1234"
SERIAL = "EMX00000"


class FakeCharacteristic:
    def __init__(self, channel: Channel, handle: int) -> None:
        self.uuid = channel.value
        self.handle = handle
        self.properties = ["write", "notify"]


class FakeService:
    def __init__(self) -> None:
        self.characteristics = [
            FakeCharacteristic(c, handle) for handle, c in enumerate(Channel, start=10)
        ]


class FakeServices:
    def __init__(self, service: FakeService | None) -> None:
        self._service = service

    def get_service(self, uuid: str) -> FakeService | None:
        return self._service if uuid == SERVICE_UUID else None


class FakeCharger:
    """The charger's side of the link: JPAKE server, record layer, canned frames."""

    def __init__(self, pin: str = PIN, *, service: bool = True) -> None:
        self._jpake = ECJPake(pin.encode(), SERVER)
        self.key: bytes | None = None
        self.services = FakeServices(FakeService() if service else None)
        self.is_connected = True
        self.mtu_size = 333
        self._mtu_size = 333
        self.disconnected_callback = None
        self._notify: dict[str, object] = {}
        self.requests: list[tuple[Channel, bytes]] = []
        self.paired = False
        self.silent = False
        self.refuse_until_bonded = False
        self.pair_result = True

    # -- bleak surface --------------------------------------------------------

    async def start_notify(self, char, callback) -> None:
        self._notify[char.uuid] = (char, callback)

    async def pair(self) -> bool:
        self.paired = True
        return self.pair_result

    async def disconnect(self) -> None:
        self.is_connected = False

    async def write_gatt_char(self, char, data: bytes, response: bool = True) -> None:
        if self.refuse_until_bonded and not self.paired:
            raise RuntimeError("ATT error 0x05: insufficient authentication")
        channel = Channel(char.uuid)
        self.requests.append((channel, bytes(data)))
        reply = self._reply(channel, bytes(data))
        if reply is not None and not self.silent:
            self._notify[char.uuid][1](char, bytearray(reply))

    # -- protocol -------------------------------------------------------------

    def _reply(self, channel: Channel, data: bytes) -> bytes | None:
        if channel is Channel.HELLO:
            return self._handshake(data)
        plaintext = crypto.decrypt(self.key, bytes.fromhex(data.decode("ascii")))
        return self._answer(channel, json.loads(plaintext))

    def _handshake(self, data: bytes) -> bytes:
        if self.key is None and not hasattr(self, "_round_one_seen"):
            self._round_one_seen = True
            self._jpake.read_round_one(data)
            return self._jpake.write_round_one()
        reply = self._jpake.write_round_two()
        self._jpake.read_round_two(data)
        self.key = self._jpake.derive_secret()
        return reply

    def _answer(self, channel: Channel, request: dict) -> bytes:
        if channel is Channel.CONFIG:
            return CONFIG_BEFORE
        if channel is Channel.STATE:
            return STATE_CHARGING
        if channel is Channel.DEBUG:
            return DEBUG_SAMPLE
        code = 0 if request.get("Id") == 999 else 1
        answer: dict = {"id": request.get("Id"), "code": code, "res": {"nws": 3}}
        if code == 0:
            answer["Comment"] = "command 999 not supported"
        return json.dumps(answer).encode()


@pytest.fixture
def charger(monkeypatch) -> FakeCharger:
    """Install a fake charger in place of the bleak connection."""
    fake = FakeCharger()

    async def establish_connection(_cls, _device, _name, **kwargs):
        fake.disconnected_callback = kwargs.get("disconnected_callback")
        return fake

    monkeypatch.setattr(client_module, "establish_connection", establish_connection)
    return fake


async def _connected(**kwargs) -> EaseeCharger:
    charger = EaseeCharger(object(), pin=PIN, serial=SERIAL, **kwargs)
    await charger.connect()
    return charger


# -- handshake ---------------------------------------------------------------


async def test_connect_completes_a_real_handshake(charger):
    link = await _connected()
    assert link.connected
    assert charger.key is not None
    assert link._session.session_key == charger.key
    # Two HELLO writes and nothing else: connect() does not poll or open a session.
    assert [c for c, _ in charger.requests] == [Channel.HELLO, Channel.HELLO]


async def test_the_wrong_pin_derives_a_different_key(charger, monkeypatch):
    monkeypatch.setattr(client_module, "WRITE_RETRY_DELAY", 0)
    link = EaseeCharger(object(), pin="9999", serial=SERIAL)
    await link.connect()
    # EC-JPAKE completes either way; the keys simply disagree, and nothing we
    # send afterwards will verify.
    assert link._session.session_key != charger.key
    with pytest.raises(RecordError, match="tag did not verify"):
        await link.poll()


async def test_a_missing_service_clears_the_cache_and_says_so(monkeypatch):
    fake = FakeCharger(service=False)

    async def establish_connection(_cls, _device, _name, **kwargs):
        return fake

    monkeypatch.setattr(client_module, "establish_connection", establish_connection)
    with pytest.raises(EaseeConnectionError, match="GATT discovery incomplete"):
        await EaseeCharger(object(), pin=PIN, serial=SERIAL).connect()


async def test_bonding_happens_when_the_charger_refuses_an_unbonded_link(charger):
    charger.refuse_until_bonded = True
    link = await _connected()
    assert charger.paired
    assert link.connected


# -- reading ------------------------------------------------------------------


async def test_poll_merges_config_and_state(charger):
    link = await _connected()
    data = await link.poll()
    assert data["phaseMode"] == 2  # from the Config frame
    assert data["chargerOpMode"] == 3  # from the State frame
    assert data["ledStripBrightness"] == 75
    assert link.unknown  # unnamed fields, kept by number


async def test_poll_before_connecting_is_refused(charger):
    link = EaseeCharger(object(), pin=PIN, serial=SERIAL)
    with pytest.raises(EaseeConnectionError, match="not authenticated"):
        await link.poll()


async def test_an_unsubscribed_channel_fails_fast(charger):
    """Rather than writing successfully and waiting out the reply timeout."""
    link = await _connected()
    with pytest.raises(EaseeConnectionError, match="was not subscribed to at connect"):
        await link.poll(channels=[Channel.DEBUG])


async def test_debug_is_readable_once_asked_for(charger):
    link = await _connected(channels=DEFAULT_CHANNELS | {Channel.DEBUG})
    data = await link.poll(channels=[Channel.DEBUG])
    assert data == {}  # nothing in Debug has a name yet
    assert link.unknown["DEBUG#14"].startswith("89")


async def test_polling_something_that_is_not_a_channel_is_a_programming_error(charger):
    link = await _connected()
    with pytest.raises(ValueError, match="not a pollable channel"):
        await link.poll(channels=[Channel.COMMAND])


async def test_a_silent_charger_times_out_with_a_diagnosis(charger, monkeypatch):
    link = await _connected()
    charger.silent = True
    monkeypatch.setattr(client_module, "REPLY_TIMEOUT", 0.05)
    with pytest.raises(EaseeConnectionError, match="notifications=on"):
        await link.poll()


# -- commands -----------------------------------------------------------------


async def test_perform_sends_a_command_and_accepts_the_answer(charger):
    link = await _connected()
    reply = await link.perform(lambda s: s.set_led_brightness(75))
    assert reply["code"] == 1
    sent = json.loads(crypto.decrypt(charger.key, bytes.fromhex(charger.requests[-1][1].decode())))
    assert sent == {"Id": 40, "Arguments": [{"Id": 1, "Value": "75"}]}


async def test_a_refusal_is_raised_with_its_reason(charger):
    link = await _connected()
    with pytest.raises(EaseeCommandRefused, match="command 999 not supported"):
        await link.perform(lambda s: s.command(999, [(1, 1)]))


# -- teardown -----------------------------------------------------------------


async def test_disconnect_releases_the_link(charger):
    link = await _connected()
    await link.disconnect()
    assert not link.connected
    assert not charger.is_connected
    assert link._session is None


async def test_a_dropped_link_notifies_once(charger):
    seen: list[object] = []
    link = await _connected(on_disconnect=seen.append)
    charger.disconnected_callback(charger)
    charger.disconnected_callback(charger)
    assert seen == [link]
    assert not link.connected


async def test_a_failing_disconnect_callback_cannot_escape(charger):
    def boom(_link):
        raise RuntimeError("consumer bug")

    link = await _connected(on_disconnect=boom)
    charger.disconnected_callback(charger)
    assert not link.connected


async def test_disconnect_survives_a_backend_that_hangs(charger, monkeypatch):
    link = await _connected()

    async def never() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(charger, "disconnect", never)
    monkeypatch.setattr(client_module, "DISCONNECT_TIMEOUT", 0.05)
    await link.disconnect()
    assert link._client is None


async def test_a_drop_mid_poll_fails_at_once_and_says_so(charger, monkeypatch):
    link = await _connected()
    charger.silent = True
    monkeypatch.setattr(client_module, "REPLY_TIMEOUT", 30.0)

    async def drop() -> None:
        await asyncio.sleep(0.05)
        charger.disconnected_callback(charger)

    with pytest.raises(EaseeConnectionError, match="link dropped while waiting"):
        await asyncio.gather(link.poll(), drop())


async def test_polling_a_link_already_known_to_be_gone_is_refused(charger):
    link = await _connected()
    charger.disconnected_callback(charger)
    with pytest.raises(EaseeConnectionError, match="reconnect first"):
        await link.poll()
