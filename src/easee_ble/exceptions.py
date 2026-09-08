"""Every exception this library raises, in one place."""

from __future__ import annotations


class EaseeError(Exception):
    """Base class for every error raised by this library."""


class ProtocolError(EaseeError):
    """A message could not be built or understood."""


class FrameError(ProtocolError):
    """A response frame was malformed."""


class IncompleteFrame(FrameError):
    """A frame stopped short of the length its own header declares: not whole yet."""


class RecordError(ProtocolError):
    """A record-layer frame was malformed, or its tag did not verify."""


class SessionError(EaseeError):
    """A session was used out of order - polling before authenticating, say."""


class JPakeError(EaseeError):
    """EC-JPAKE failed. A verification failure usually means the wrong PIN."""


class EaseeConnectionError(EaseeError):
    """Connecting to, or talking to, the charger failed."""


class EaseeCommandRefused(EaseeConnectionError):
    """The charger answered a command and said no."""
