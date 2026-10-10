import errno

from usb.core import USBError

from antplus_recorder.app import open_error


def test_no_permission_on_linux_points_to_the_udev_rule(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert "udev" in open_error(USBError("Access denied", 3, errno.EACCES))


def test_access_denied_elsewhere_means_another_app(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert "another app" in open_error(USBError("Access denied", 3, errno.EACCES))
    assert "another app" in open_error(USBError("Resource busy", 6, errno.EBUSY))


def test_other_errors_are_passed_on():
    message = open_error(USBError("Input/output error", 1, errno.EIO))
    assert "Input/output error" in message
    assert "another app" not in message
