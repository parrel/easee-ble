"""Protocol constants for the Easee charger BLE interface."""

from __future__ import annotations

import enum

# Vendor service holding every channel below; visible only after connecting.
SERVICE_UUID = "be312306-9797-2ebc-c947-08eff95544bc"

# Bluetooth SIG company identifier seen in the charger's advertisement.
MANUFACTURER_ID = 3118


class Channel(enum.Enum):
    """The characteristics of :data:`SERVICE_UUID`."""

    HELLO = "2f7b83db-ae66-d0be-ae45-78ecf4509b3d"
    COMMAND = "f4ef9f9e-540e-0497-1449-84fe940045be"
    CONFIG = "7d0b575a-5f58-cf9c-c442-2cacd56e4102"
    STATE = "992166b7-8cb3-fba7-9f4b-166fb352c50d"
    STRUCTURE = "3368fdd0-d18a-61aa-034b-9abc497cbec1"
    DEBUG = "2737ec9f-2dc1-7a8e-2048-de136057f9ab"
    # The app knows this one, but a Charge Max does not expose it.
    TIME = "fe14720f-d849-4f83-5144-d0f5b4f3689d"


CHANNEL_BY_UUID = {c.value: c for c in Channel}
