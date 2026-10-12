"""Keep the computer from going to sleep while recording, which would stop the stick."""

import contextlib
import ctypes
import ctypes.util
import sys
from collections.abc import Iterator

UTF8 = 0x08000100  # kCFStringEncodingUTF8
ASSERTION_ON = 255  # kIOPMAssertionLevelOn
# Like caffeinate -i: the system stays awake, the display may still turn off
ASSERTION_TYPE = "PreventUserIdleSystemSleep"


@contextlib.contextmanager
def stay_awake() -> Iterator[None]:
    """On macOS, hold a power assertion while the app runs; nothing elsewhere.

    The system drops the assertion when the process ends, even after a crash.
    """
    assertion = _create_assertion() if sys.platform == "darwin" else None
    try:
        yield
    finally:
        if assertion is not None:
            assertion.release()


class _Assertion:
    def __init__(self, iokit: ctypes.CDLL, identifier: ctypes.c_uint32) -> None:
        self.iokit = iokit
        self.identifier = identifier

    def release(self) -> None:
        self.iokit.IOPMAssertionRelease(self.identifier)


def _create_assertion() -> _Assertion | None:
    """None if the frameworks can't be loaded or the system refuses."""
    try:
        core = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
    except (OSError, TypeError):
        return None
    core.CFStringCreateWithCString.restype = ctypes.c_void_p
    core.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    core.CFRelease.argtypes = [ctypes.c_void_p]
    iokit.IOPMAssertionCreateWithName.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]

    kind = core.CFStringCreateWithCString(None, ASSERTION_TYPE.encode(), UTF8)
    reason = core.CFStringCreateWithCString(
        None, b"antplus-recorder is recording", UTF8
    )
    identifier = ctypes.c_uint32()
    status = iokit.IOPMAssertionCreateWithName(
        kind, ASSERTION_ON, reason, ctypes.byref(identifier)
    )
    core.CFRelease(kind)
    core.CFRelease(reason)
    return _Assertion(iokit, identifier) if status == 0 else None
