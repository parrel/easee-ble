# Changelog

Notable changes per release. Versions follow [SemVer](https://semver.org).

## 1.0.0 - 2026-09-12

The API is now stable

## 0.2.0 - 2026-09-10

Breaking: commands now carry the Easee app's own names, and all but three are confirmed on a real charger.

### Added

- Reboot the charger, play its connection light animation, and run its self test.
- Manage RFID keys: add by tag number, remove one, remove all, and put the charger in pairing mode.
- Require a key before charging starts (the app's private access).
- Authorise and deauthorise charging with a key, not yet tried on a charger.
- Factory reset, not yet tried on a charger.
- Pause and resume charging.
- Set the dynamic circuit current, the fallback current used without a network, and idle current.
- Read the networks a WiFi scan finds.
- Read the MID meter's public key, or show it on the charger's display.
- New readings: LED mode, whether a key is required, idle current, OCPP, dynamic circuit current, energy in the last hour and running hours.

### Changed

- `restart` is now `reboot`, and `open_session` is now `play_lights`.
- The RFID key functions are now `list_local_rfids`, `get_local_rfid` and `add_local_rfid`, without a slot.
- `set_circuit_max_current` is now `set_circuit_rated_current`, the fuse.
- `set_offline_max_circuit_current` is now `set_max_circuit_current`, reading back as `circuitMaxCurrentP1-3`.
- Config fields 3-5 are now read as the fallback current, `fallbackCircuitCurrentP1-3`.

### Fixed

- The L1-L3 and L2-L3 voltages were swapped.
- Connecting failed on macOS.
- The self test no longer times out while it is still running.
- A slow reply to an earlier command is no longer mistaken for the answer to the current one.
- Connection failures now always raise `EaseeConnectionError`, with a hint when the computer's Bluetooth pairing is stale.

### Removed

- `set_access_control`, deprecated since 0.1.0.
- `CONFIRMED_REASON_CODES`, which only labelled codes and changed nothing.

## 0.1.1 - 2026-09-09

### Fixed

- A poll interrupted by a link drop now fails immediately with "the link
  dropped" instead of waiting out `REPLY_TIMEOUT` and reporting the CCCD
  subscription diagnostic, which pointed at the wrong cause.
