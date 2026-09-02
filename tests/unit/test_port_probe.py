import socket

import pytest

from app.system.port_probe import FakePortProbe, TcpPortProbe


class Connection:
    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_tcp_port_probe_uses_configured_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, int], float]] = []

    def connect(address: tuple[str, int], timeout: float) -> Connection:
        calls.append((address, timeout))
        return Connection()

    monkeypatch.setattr(socket, "create_connection", connect)

    assert TcpPortProbe("127.0.0.1", 8212).is_open() is True
    assert calls == [(("127.0.0.1", 8212), 1.0)]


def test_tcp_port_probe_reports_closed_on_socket_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_address: tuple[str, int], timeout: float) -> Connection:
        del timeout
        raise OSError("indisponível")

    monkeypatch.setattr(socket, "create_connection", fail)

    assert TcpPortProbe("127.0.0.1", 8212).is_open() is False


def test_fake_port_probe_is_controllable() -> None:
    probe = FakePortProbe()

    assert probe.is_open() is False
    probe.set_open(True)
    assert probe.is_open() is True
