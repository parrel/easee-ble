# easee-ble

Local **Bluetooth** control for [Easee](https://easee.com) EV chargers. Talk to the charger directly with the PIN printed on the unit.

```bash
pip install easee-ble
```

Requires Python 3.11+. For Home Assistant, use
[ha-easee-ble](https://github.com/parrel/ha-easee-ble), which is built on this.

## Quick start

```python
import asyncio
from bleak import BleakScanner
from easee_ble import EaseeCharger, PhaseMode

async def main():
    device = await BleakScanner.find_device_by_address("AA:BB:CC:DD:EE:FF")
    charger = EaseeCharger(device, pin="1234", serial="EMX00000")

    await charger.connect()                # handshake; the link is held open
    print(await charger.poll())            # {'chargerOpMode': 3, 'totalPower': 2.69, ...}

    await charger.perform(lambda s: s.set_charger_enabled(True))
    await charger.perform(lambda s: s.set_phase_mode(PhaseMode.AUTO))

    await charger.disconnect()

asyncio.run(main())
```

You need two things from the charger: the **PIN** printed on the unit, and its
**serial** (`EMXXXXXX`, also on the unit and in the Easee app).

### Finding your charger

Scan by service UUID if you do not know the address:

```python
from easee_ble import SERVICE_UUID

device = await BleakScanner.find_device_by_filter(
    lambda d, adv: SERVICE_UUID in adv.service_uuids
)
```

Chargers usually advertise only **intermittently**. To get one to show up,
either set Bluetooth to always-on in the Easee app, or open a window with a long
press of the charger's touch button.

## Reading

`poll()` merges State and Config into one dict of named fields:

```python
data = await charger.poll()
data["chargerOpMode"]        # 3
data["totalPower"]           # 2.69
data["maxChargerCurrent"]    # 16
charger.unknown              # fields we have no name for yet, by number
```

Two fields belong together. `chargerOpMode` describes the **car and cable** - it
still reads `AWAITING_START` for a charger that has been switched off.
`reasonForNoCurrent` says **why** no current flows, and reads 53
(*charger disabled*) in exactly that case. Show both:

```python
from easee_ble import charger_op_mode, reason_for_no_current

charger_op_mode(data["chargerOpMode"])                # ChargerOpMode.CHARGING
reason_for_no_current(data.get("reasonForNoCurrent")) # 'Charger disabled'
```

Both return `None` on a value this library has not seen, where `ChargerOpMode()`
and `NetworkStatus()` raise. The descriptions are display text; key on
`reason_for_no_current_slug()` if you need something stable.

Fields at their default value are **absent** from the wire, not zero. `poll()`
already fills a curated set back in as `0`, so treat a missing field as unknown
rather than surprising.

## Commands

Every command goes through `perform()`, which builds the request and checks the
answer:

```python
await charger.perform(lambda s: s.set_charger_enabled(True))
await charger.perform(lambda s: s.set_max_charger_current(16))
await charger.perform(lambda s: s.set_dynamic_charger_current(10))
await charger.perform(lambda s: s.set_circuit_rated_current(16))        # the fuse; or (p1, p2, p3)
await charger.perform(lambda s: s.set_max_circuit_current(25))
await charger.perform(lambda s: s.set_phase_mode(PhaseMode.LOCKED_3_PHASE))
await charger.perform(lambda s: s.set_led_brightness(75))               # 0-100
await charger.perform(lambda s: s.set_cable_locked(True))
await charger.perform(lambda s: s.pause_charging())
await charger.perform(lambda s: s.resume_charging(16))
```


`PhaseMode` is `LOCKED_1_PHASE`, `AUTO` or `LOCKED_3_PHASE`.

### RFID keys

```python
from easee_ble import command_payload

reply = await charger.perform(lambda s: s.list_local_rfids())
names = command_payload(reply)["utns"]
await charger.perform(lambda s: s.add_local_rfid("Alice", "04a1b2c3d4e5f6"))  # the tag's UID
await charger.perform(lambda s: s.remove_local_rfid("04a1b2c3d4e5f6"))
await charger.perform(lambda s: s.set_local_authorization(True))   # charging needs a key
```

Pairing mode (`set_rfid_pairing_mode()`) hands a scanned tag to your Easee
account in the cloud, not to the charger's own list.

### WiFi

```python
from easee_ble import wifi_networks

reply = await charger.perform(lambda s: s.scan_wifi())
wifi_networks(reply)                  # [{'ssid': 'Home', 'rssi': -64}, ...]
await charger.perform(lambda s: s.set_wifi("Home", "passphrase"))
```

### Other reads

Besides `poll()`, individual frames are available through `perform()`:

```python
await charger.perform(lambda s: s.poll_state())
await charger.perform(lambda s: s.poll_config())
await charger.perform(lambda s: s.poll_structure())
await charger.perform(lambda s: s.poll_debug())
```

A reply only arrives on a subscribed channel, and the last two are not
subscribed to by default. Ask for them at connect - `EaseeCharger(...,
channels=DEFAULT_CHANNELS | {Channel.DEBUG})` - or the call raises. `poll()`
takes the same argument to read them alongside Config and State.

## Errors

```python
from easee_ble import EaseeCommandRefused, EaseeConnectionError, JPakeError

try:
    await charger.connect()
    await charger.perform(lambda s: s.set_max_charger_current(32))
except JPakeError:            # almost always the wrong PIN
    ...
except EaseeCommandRefused:   # the charger answered, and said no
    ...
except EaseeConnectionError:  # connecting or talking to it failed
    ...
```

A reply is **not** an acknowledgement - the charger answers a command it refuses
just as promptly as one it accepts. `perform()` checks for you and raises
`EaseeCommandRefused`; anything else, `command_accepted()` and
`command_refusal()` check by hand.

## Connection notes

- The connection is **long-lived**: connect once and keep polling. Connecting is
  the expensive, failure-prone part.
- **Not safe for concurrent use.** One request may be in flight at a time -
  serialise with a lock if several tasks share a charger.
- Pass `on_disconnect=` to be told the moment the link drops rather than at your
  next poll; check `charger.connected` before using it.
- The charger has a single connection slot. Always `disconnect()` when done.

```python
charger = EaseeCharger(device, pin="1234", serial="EMX00000",
                       on_disconnect=lambda c: print("link lost"))
```

## Without bleak

`Session` is sans-io: it builds requests and parses replies and does no I/O, so
you can drive it over any transport.

```python
from easee_ble import Session

s = Session(pin="1234", serial="EMX00000")
req = s.start_handshake()          # write req.data to req.channel, feed the reply back
req = s.read_round_one(reply)
s.read_round_two(reply)            # s.established is now True
frame = s.parse(req.channel, reply)
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests run against captured bytes from a real charger, so they need no hardware.

## Notes

**Unofficial.** Reverse-engineered. Not affiliated with or endorsed by Easee. No warranty, changing charger settings is at your own risk. Barely tested.

