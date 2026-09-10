"""Timing and sizing constants for the BLE client."""

from __future__ import annotations

# How long to wait for the charger's notification answering a write.
REPLY_TIMEOUT = 15.0
# The same, for commands answered only once they finish; a self test ran about four minutes.
SLOW_REPLY_TIMEOUT = 300.0
# How long to wait for the whole connect.
CONNECT_TIMEOUT = 30.0
# Ceiling for establishing the link, across all connection attempts.
LINK_TIMEOUT = 60.0
# Attempts bleak-retry-connector may make inside that budget.
CONNECT_ATTEMPTS = 2
# Deadline for tearing a connection down.
DISCONNECT_TIMEOUT = 10.0
# How long to wait for bonding.
PAIR_TIMEOUT = 35.0
# Ceiling for discovery, bonding and the handshake together.
SETUP_TIMEOUT = 90.0
# Deadline for a single write.
WRITE_TIMEOUT = 20.0
# ATT MTU carrying the 330-byte EC-JPAKE round one in one write; advisory only.
HANDSHAKE_MTU = 333
# Attempts for a single write before giving up on the connection.
WRITE_ATTEMPTS = 3
# Pause between those attempts.
WRITE_RETRY_DELAY = 1.0
