"""The bleak client: one authenticated connection to one charger."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from .const import (
    CONNECT_ATTEMPTS,
    CONNECT_TIMEOUT,
    DISCONNECT_TIMEOUT,
    HANDSHAKE_MTU,
    LINK_TIMEOUT,
    PAIR_TIMEOUT,
    REPLY_TIMEOUT,
    SETUP_TIMEOUT,
    WRITE_ATTEMPTS,
    WRITE_RETRY_DELAY,
    WRITE_TIMEOUT,
)
from .exceptions import EaseeCommandRefused, EaseeConnectionError
from .frames import Frame, command_accepted, command_refusal, with_aliases
from .protocol import SERVICE_UUID, Channel
from .session import Request, Session

_LOGGER = logging.getLogger(__name__)


# What the official app uses. Each extra subscription costs a round-trip at connect.
DEFAULT_CHANNELS = frozenset({Channel.HELLO, Channel.COMMAND, Channel.CONFIG, Channel.STATE})

# No handshake without HELLO, no commands without COMMAND, whatever the caller passes.
_REQUIRED_CHANNELS = frozenset({Channel.HELLO, Channel.COMMAND})

# How to ask for each pollable channel; DEBUG and STRUCTURE also need `channels=`.
_POLL_REQUESTS: dict[Channel, Callable[[Session], Request]] = {
    Channel.CONFIG: lambda s: s.poll_config(),
    Channel.STATE: lambda s: s.poll_state(),
    Channel.DEBUG: lambda s: s.poll_debug(),
    Channel.STRUCTURE: lambda s: s.poll_structure(),
}

# Changing this changes the keys every caller gets back: a break, not a default tweak.
DEFAULT_POLL_CHANNELS: tuple[Channel, ...] = (Channel.CONFIG, Channel.STATE)


class EaseeCharger:
    """An authenticated bleak connection to one charger, held open."""

    def __init__(
        self,
        device: BLEDevice,
        pin: str,
        serial: str,
        *,
        on_disconnect: Callable[[EaseeCharger], None] | None = None,
        channels: Iterable[Channel] | None = None,
    ) -> None:
        self._device = device
        self._pin = pin
        self._serial = serial
        # Called once when the link drops, deliberate disconnects included.
        self._on_disconnect = on_disconnect
        # Replaces DEFAULT_CHANNELS rather than extending it; HELLO and COMMAND stay.
        self._channels = (
            DEFAULT_CHANNELS if channels is None else frozenset(channels) | _REQUIRED_CHANNELS
        )
        self._client: BleakClient | None = None
        self._session: Session | None = None
        self._queues: dict[Channel, asyncio.Queue[bytes]] = {}
        self._chars: dict[Channel, BleakGATTCharacteristic] = {}
        self._notify_ok: dict[Channel, bool] = {}
        self._uuid_to_channel = {c.value: c for c in Channel}
        # Why bonding did not happen, used to explain a later "insufficient authentication".
        self._bond_note: str | None = None
        # Handle -> channel, for the CCCD-less notification path.
        self._handle_to_channel: dict[int, Channel] = {}
        # Whether this connection has been bonded on demand already.
        self._bonded = False
        # Set by the disconnect callback; the backend's belief can outlive the link.
        self._link_lost = False
        # Wakes anything waiting on a reply, so a known drop is not waited out.
        self._link_lost_event = asyncio.Event()
        # Unsubscribe callbacks from that path, released on disconnect.
        self._notify_cancels: list[Callable[[], None]] = []
        # Unnamed fields from the last poll, keyed "<CHANNEL>#<field number>".
        self.unknown: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        """Whether this connection is still usable."""
        return self._client is not None and self._client.is_connected and not self._link_lost

    def _on_disconnected(self, _client: Any) -> None:
        """Backend told us the link dropped. Believe it immediately."""
        if self._link_lost:
            return
        self._link_lost = True
        self._link_lost_event.set()
        _LOGGER.info("charger %s: the Bluetooth link dropped", self._serial)
        if self._on_disconnect is not None:
            # Never let a consumer's callback escape into bleak's disconnect path.
            try:
                self._on_disconnect(self)
            except Exception:
                _LOGGER.exception("charger %s: disconnect callback failed", self._serial)

    async def connect(self) -> None:
        """Connect, subscribe to notifications, and authenticate."""
        # use_services_cache skips a GATT rediscovery; pairing waits until the charger asks.
        self._link_lost = False
        self._link_lost_event.clear()
        try:
            async with asyncio.timeout(LINK_TIMEOUT):
                client = await establish_connection(
                    BleakClient,
                    self._device,
                    self._serial,
                    disconnected_callback=self._on_disconnected,
                    timeout=CONNECT_TIMEOUT,
                    max_attempts=CONNECT_ATTEMPTS,
                    use_services_cache=True,
                )
        except TimeoutError as exc:
            raise EaseeConnectionError(
                f"charger {self._serial}: could not establish a link within {LINK_TIMEOUT:.0f}s"
            ) from exc
        self._client = client
        self._queues = {c: asyncio.Queue() for c in Channel}
        try:
            async with asyncio.timeout(SETUP_TIMEOUT):
                await self._resolve_and_authenticate(client)
        except TimeoutError as exc:
            await self.disconnect()
            raise EaseeConnectionError(
                f"charger {self._serial}: connected, but discovery, bonding and "
                f"the handshake did not complete within {SETUP_TIMEOUT:.0f}s"
            ) from exc
        except BaseException:
            await self.disconnect()
            raise
        await self._reconcile_link_lost(client)

    async def _reconcile_link_lost(self, client: BleakClient) -> None:
        """Settle a drop reported *while* we were connecting."""
        if not self._link_lost:
            return
        if client.is_connected:
            _LOGGER.warning(
                "charger %s: a disconnect was reported while connecting, but the "
                "handshake completed and the backend still reports the link up - "
                "treating the report as stale",
                self._serial,
            )
            self._link_lost = False
            return
        await self.disconnect()
        raise EaseeConnectionError(f"charger {self._serial}: the link dropped during the handshake")

    async def _resolve_and_authenticate(self, client: BleakClient) -> None:
        # Resolve the vendor service up front; a partial GATT cache is cleared and retried.
        await self._check_mtu(client)

        _LOGGER.info("charger %s: resolving services", self._serial)
        service = client.services.get_service(SERVICE_UUID)
        if service is None:
            await self._clear_cache(client)
            raise EaseeConnectionError(
                f"charger {self._serial}: GATT discovery incomplete "
                f"(service {SERVICE_UUID} not found; cache cleared) - retrying"
            )
        self._chars = {}
        for char in service.characteristics:
            channel = self._uuid_to_channel.get(char.uuid)
            if channel is None:
                continue
            self._chars[channel] = char
            if channel not in self._channels:
                continue
            if {"notify", "indicate"} & set(char.properties):
                try:
                    await client.start_notify(char, self._on_notify)
                    self._notify_ok[channel] = True
                except Exception as exc:
                    # This firmware exposes no CCCD yet notifies anyway; subscribe out-of-band.
                    subscribed = await self._subscribe_without_cccd(client, char, channel)
                    self._notify_ok[channel] = subscribed
                    _LOGGER.debug(
                        "start_notify(%s) failed (%s); CCCD-less subscribe: %s",
                        char.uuid,
                        exc,
                        "ok" if subscribed else "unavailable",
                    )

        if self._chars and not any(self._notify_ok.values()):
            # No path for a reply at all: no CCCD, and reads come back empty over a proxy.
            await self._clear_cache(client)
            raise EaseeConnectionError(
                f"charger {self._serial}: could not subscribe to notifications on "
                f"any channel, and this charger's replies cannot arrive any other "
                f"way (it exposes no CCCD descriptor, and reads return empty). "
                f"This adapter cannot drive it."
                f"The connection was served by {self.radio}"
            )

        if Channel.HELLO not in self._chars:
            await self._clear_cache(client)
            raise EaseeConnectionError(
                f"charger {self._serial}: handshake characteristic missing "
                f"(cache cleared) - retrying"
            )
        await self._authenticate()

    async def _subscribe_without_cccd(
        self, client: BleakClient, char: BleakGATTCharacteristic, channel: Channel
    ) -> bool:
        """Register for notifications without writing a CCCD descriptor."""
        backend = getattr(client, "_backend", client)
        api = getattr(backend, "_client", None)
        address = getattr(backend, "_address_as_int", None)
        start_notify = getattr(api, "bluetooth_gatt_start_notify", None)
        if api is None or address is None or start_notify is None:
            return False

        handle = char.handle
        self._handle_to_channel[handle] = channel
        try:
            cancel = await start_notify(
                address, handle, lambda _handle, data: self._on_notify_handle(_handle, data)
            )
        except Exception as exc:
            _LOGGER.debug("CCCD-less subscribe on %s failed: %s", channel.name, exc)
            return False
        self._remember_cancel(cancel)
        return True

    def _remember_cancel(self, cancel: Any) -> None:
        """Keep whatever ``bluetooth_gatt_start_notify`` handed back, unsubscribably."""
        if isinstance(cancel, tuple):
            cancel = cancel[-1]
        if callable(cancel) and not asyncio.iscoroutinefunction(cancel):
            self._notify_cancels.append(cancel)

    def _on_notify_handle(self, handle: int, data: bytes) -> None:
        """Notification callback for the CCCD-less path, which reports handles."""
        channel = self._handle_to_channel.get(handle)
        if channel is None:
            _LOGGER.debug("notification on unmapped handle %s", handle)
            return
        self._queues[channel].put_nowait(bytes(data))

    @staticmethod
    async def _negotiated_mtu(client: BleakClient) -> int | None:
        """The real MTU, or None when the backend cannot tell us."""
        backend = getattr(client, "_backend", client)
        if not getattr(backend, "_mtu_size", None):
            acquire = getattr(backend, "_acquire_mtu", None)
            if acquire is not None:
                try:
                    await acquire()
                except Exception as exc:
                    _LOGGER.debug("could not acquire MTU: %s", exc)
                    return None
        try:
            return int(client.mtu_size)
        except Exception as exc:
            _LOGGER.debug("backend did not report an MTU: %s", exc)
            return None

    async def _check_mtu(self, client: BleakClient) -> None:
        """Note an MTU too small for the handshake, but do not refuse to try."""
        mtu = await self._negotiated_mtu(client)
        if not mtu:
            # Backend will not say. Do not guess anything from silence.
            return
        if mtu < HANDSHAKE_MTU:
            _LOGGER.info(
                "charger %s: link reports MTU %s, below the %s the handshake "
                "needs. Proceeding anyway - the value may be a proxy's "
                "placeholder for a cached connection, and a real small MTU is "
                "still carried by a long write. Watch for a failure on the "
                "HELLO write if this is the actual problem",
                self._serial,
                mtu,
                HANDSHAKE_MTU,
            )
            return
        _LOGGER.info("charger %s: negotiated MTU %s", self._serial, mtu)

    async def _ensure_bonded(self, client: BleakClient) -> bool:
        """Bond before writing: the vendor characteristics demand encryption."""
        self._bond_note = None
        pair = getattr(client, "pair", None)
        if pair is None:
            self._bond_note = "this Bluetooth backend cannot pair"
            return False
        try:
            async with asyncio.timeout(PAIR_TIMEOUT):
                bonded = await pair()
            _LOGGER.debug("pair() returned %s", bonded)
            return True
        except TimeoutError:
            # Do not give up: it may already be bonded from an earlier attempt.
            self._bond_note = f"pairing timed out after {PAIR_TIMEOUT:.0f}s"
            _LOGGER.warning(
                "charger %s: pairing timed out; trying the handshake anyway",
                self._serial,
            )
        except NotImplementedError as exc:
            # Distinguishable by the message: a proxy that cannot pair says so.
            detail = str(exc)
            if detail:
                self._bond_note = detail
                _LOGGER.debug("pairing unavailable: %s", detail)
            else:
                _LOGGER.debug("backend pairs implicitly; nothing to do")
        except Exception as exc:
            self._bond_note = f"pairing failed: {exc}"
            _LOGGER.debug("pair() failed (%s); continuing to the handshake", exc)
        return False

    @staticmethod
    async def _clear_cache(client: BleakClient) -> None:
        """Best-effort: drop the cached GATT table so the next connect rediscovers."""
        seen = False
        for target in (client, getattr(client, "_backend", None)):
            clear = getattr(target, "clear_cache", None)
            if clear is None:
                continue
            seen = True
            try:
                await clear()
                _LOGGER.debug("cleared GATT cache via %s", type(target).__name__)
                return
            except Exception as exc:
                _LOGGER.debug("clear_cache via %s failed: %s", type(target).__name__, exc)
        if not seen:
            _LOGGER.debug("no clear_cache available on this bleak backend")

    def _on_notify(self, sender: Any, data: bytearray) -> None:
        uuid = getattr(sender, "uuid", str(sender))
        channel = self._uuid_to_channel.get(uuid)
        if channel is None:
            _LOGGER.debug("notification on unknown characteristic %s", uuid)
            return
        self._queues[channel].put_nowait(bytes(data))

    async def _write_with_retry(self, char: BleakGATTCharacteristic, request: Request) -> None:
        """Write, retrying a transient GATT failure on the same connection."""
        assert self._client is not None
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            try:
                async with asyncio.timeout(WRITE_TIMEOUT):
                    await self._client.write_gatt_char(char, request.data, response=True)
                if attempt > 1:
                    _LOGGER.info(
                        "charger %s: write to %s succeeded on attempt %d",
                        self._serial,
                        request.channel.name,
                        attempt,
                    )
                return
            except TimeoutError as exc:
                # An outer deadline cancels with CancelledError, so it passes straight through.
                raise EaseeConnectionError(
                    f"charger {self._serial}: the write to "
                    f"{request.channel.name} never completed within "
                    f"{WRITE_TIMEOUT:.0f}s - the link is up but silent"
                ) from exc
            except Exception as exc:
                detail = str(exc).lower()
                needs_bond = "authentication" in detail or "not paired" in detail
                if needs_bond and not self._bonded:
                    # The charger asks for encryption by refusing, so bond now, not at connect.
                    _LOGGER.info(
                        "charger %s: refused for want of an encrypted link; bonding",
                        self._serial,
                    )
                    self._bonded = await self._ensure_bonded(self._client)
                    if self._bonded:
                        continue
                    raise
                if needs_bond or attempt == WRITE_ATTEMPTS or not self._client.is_connected:
                    raise
                _LOGGER.info(
                    "charger %s: write to %s failed (attempt %d/%d): %s - retrying",
                    self._serial,
                    request.channel.name,
                    attempt,
                    WRITE_ATTEMPTS,
                    exc,
                )
                await asyncio.sleep(WRITE_RETRY_DELAY)

    async def _write_and_wait(self, request: Request) -> bytes:
        if self._client is None:
            raise EaseeConnectionError("not connected")
        if self._link_lost:
            raise EaseeConnectionError(
                f"charger {self._serial}: the Bluetooth link dropped; reconnect first"
            )
        if request.channel not in self._channels:
            # The write would succeed and the reply go nowhere: a bare REPLY_TIMEOUT.
            raise EaseeConnectionError(
                f"channel {request.channel.name} was not subscribed to at "
                f"connect, so its reply cannot arrive. Construct the charger "
                f"with channels={{Channel.{request.channel.name}, ...}} to read it"
            )
        # Drop any stale reply so we read the one for this write.
        queue = self._queues[request.channel]
        while not queue.empty():
            queue.get_nowait()
        # Write to the resolved characteristic, so a half-resolved table surfaces at connect.
        char = self._chars.get(request.channel)
        if char is None:
            raise EaseeConnectionError(f"channel {request.channel.name} not available")
        try:
            await self._write_with_retry(char, request)
        except Exception as exc:
            # ATT error 5 is the charger refusing an unencrypted link; the raw message is opaque.
            if "authentication" in str(exc).lower() or "not paired" in str(exc).lower():
                why = f" ({self._bond_note})" if self._bond_note else ""
                raise EaseeConnectionError(
                    f"charger {self._serial} refused the write: the link must be "
                    f"bonded first{why}. Pair the charger with this Bluetooth "
                    f"adapter, or use an adapter that supports pairing. [{exc}]"
                ) from exc
            raise

        # The charger pushes a notification on the channel written to; there is no other path.
        try:
            return await self._wait_for_reply(queue, request.channel)
        except TimeoutError as exc:
            raise EaseeConnectionError(
                f"charger {self._serial}: wrote {len(request.data)} bytes to "
                f"{request.channel.name} but got no reply within "
                f"{REPLY_TIMEOUT:.0f}s. notifications="
                f"{'on' if self._notify_ok.get(request.channel) else 'OFF'}. "
                f"This charger exposes no CCCD descriptor, so notifications are "
                f"subscribed to out-of-band (see _subscribe_without_cccd); if "
                f"that failed there is no path for a reply at all"
            ) from exc

    async def _wait_for_reply(self, queue: asyncio.Queue[bytes], channel: Channel) -> bytes:
        """Wait for the reply, giving up as soon as the link is known to be gone."""
        reply = asyncio.ensure_future(queue.get())
        dropped = asyncio.ensure_future(self._link_lost_event.wait())
        try:
            done, _ = await asyncio.wait(
                (reply, dropped),
                timeout=REPLY_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # A reply that did arrive wins, even if the link died in the same pass.
            if reply in done:
                return reply.result()
            if dropped in done:
                raise EaseeConnectionError(
                    f"charger {self._serial}: the Bluetooth link dropped while "
                    f"waiting for a reply on {channel.name}"
                )
            raise TimeoutError
        finally:
            reply.cancel()
            dropped.cancel()

    async def _authenticate(self) -> None:
        _LOGGER.info("charger %s: starting EC-JPAKE handshake", self._serial)
        session = Session(self._pin, self._serial)
        reply = await self._write_and_wait(session.start_handshake())
        reply = await self._write_and_wait(session.read_round_one(reply))
        session.read_round_two(reply)
        self._session = session
        _LOGGER.info("charger %s: authenticated", self._serial)

    async def poll(self, *, channels: Sequence[Channel] | None = None) -> dict[str, Any]:
        """Poll Config and State - or ``channels`` - and merge their named fields."""
        if self._session is None:
            raise EaseeConnectionError("not authenticated")
        wanted = DEFAULT_POLL_CHANNELS if channels is None else tuple(channels)
        unpollable = [c.name for c in wanted if c not in _POLL_REQUESTS]
        if unpollable:
            raise ValueError(f"not a pollable channel: {', '.join(unpollable)}")
        data: dict[str, Any] = {}
        unknown: dict[str, Any] = {}
        for request in (_POLL_REQUESTS[c](self._session) for c in wanted):
            reply = await self._write_and_wait(request)
            frame = self._session.parse(request.channel, reply)
            if isinstance(frame, Frame):
                data.update(frame.named)
                name = frame.type.name if frame.type else f"0x{frame.type_id:02x}"
                unknown.update({f"{name}#{n}": v for n, v in frame.unknown.items()})
        self._report_unknown(unknown)
        self.unknown = unknown
        data = with_aliases(data)
        _LOGGER.debug(
            "charger %s: poll returned %d named fields (%s) and %d unnamed (%s)",
            self._serial,
            len(data),
            ", ".join(sorted(data)),
            len(unknown),
            ", ".join(sorted(unknown)),
        )
        return data

    def _report_unknown(self, unknown: dict[str, Any]) -> None:
        """Say out loud when an unidentified field moves."""
        before, after = self.unknown, unknown
        if not before:
            return

        def steady(value: Any) -> bool:
            """Whole numbers only - bool is an int, so this covers flags too."""
            return isinstance(value, int)

        moved = {
            key: (before.get(key), after.get(key))
            for key in set(before) | set(after)
            if before.get(key) != after.get(key)
            and (steady(before.get(key, 0)) and steady(after.get(key, 0)))
        }
        if moved:
            _LOGGER.info(
                "charger %s: unidentified field(s) moved: %s. If you just changed "
                "a setting, that field is what carries it",
                self._serial,
                ", ".join(
                    f"{k} {'(absent)' if o is None else o} -> {'(absent)' if n is None else n}"
                    for k, (o, n) in sorted(moved.items())
                ),
            )

    @property
    def radio(self) -> str | None:
        """Which adapter or proxy is actually serving this connection."""
        backend = getattr(self._client, "_backend", None)
        for attr in ("_source", "source", "_device_path"):
            value = getattr(backend, attr, None)
            if isinstance(value, str) and value:
                return value
        return type(backend).__name__ if backend is not None else None

    async def perform(self, make_request: Callable[[Session], Request]) -> Any:
        """Run one command: ``make_request(session)`` builds it, we write it."""
        if self._session is None:
            raise EaseeConnectionError("not authenticated")
        request = make_request(self._session)
        reply = await self._write_and_wait(request)
        response = self._session.parse(request.channel, reply)
        if isinstance(response, dict) and not command_accepted(response):
            # The whole reply, verbatim: the exception only carries the reason.
            _LOGGER.debug("charger %s: command refused, full reply: %s", self._serial, response)
            raise EaseeCommandRefused(f"charger refused the command: {command_refusal(response)}")
        return response

    async def disconnect(self) -> None:
        """Tear the connection down, releasing everything it holds."""
        client, self._client = self._client, None
        self._session = None
        self._chars = {}
        self._notify_ok = {}
        self._handle_to_channel = {}
        self._bonded = False
        # Unsubscribe before dropping the link, or a handler leaks per channel per reconnect.
        cancels, self._notify_cancels = self._notify_cancels, []
        for cancel in cancels:
            try:
                cancel()
            except Exception as exc:
                _LOGGER.debug("unsubscribe failed: %s", exc)
        if client is not None:
            await self._disconnect_bounded(client)

    async def _disconnect_bounded(self, client: BleakClient) -> None:
        """Wait a bounded time for the disconnect - but never cancel it."""
        task = asyncio.ensure_future(client.disconnect())
        # asyncio.wait leaves its future alone on timeout and on cancellation.
        done, _ = await asyncio.wait({task}, timeout=DISCONNECT_TIMEOUT)
        if not done:
            _LOGGER.warning(
                "charger %s: disconnect still running after %.0fs; leaving it to "
                "finish in the background so the connection slot is released",
                self._serial,
                DISCONNECT_TIMEOUT,
            )
            task.add_done_callback(self._log_late_disconnect)
            return
        try:
            task.result()
        except Exception as exc:
            _LOGGER.debug("disconnect failed: %s", exc)

    def _log_late_disconnect(self, task: asyncio.Future[Any]) -> None:
        """Say how a detached disconnect ended, so a leaked slot is not silent."""
        if task.cancelled():
            _LOGGER.warning(
                "charger %s: detached disconnect was cancelled; the link may still be held",
                self._serial,
            )
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.warning(
                "charger %s: detached disconnect failed (%s); the link may still "
                "be held on the adapter or proxy",
                self._serial,
                exc,
            )
        else:
            _LOGGER.info(
                "charger %s: detached disconnect completed; slot released",
                self._serial,
            )
