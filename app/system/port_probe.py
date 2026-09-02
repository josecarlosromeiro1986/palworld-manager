import socket
from typing import Protocol


class PortProbe(Protocol):
    def is_open(self) -> bool: ...


class TcpPortProbe:
    def __init__(self, host: str, port: int, *, timeout_seconds: float = 1.0) -> None:
        if not host:
            raise ValueError("host da porta é obrigatório")
        if not 1 <= port <= 65535:
            raise ValueError("porta inválida")
        if timeout_seconds <= 0:
            raise ValueError("o timeout da porta deve ser positivo")
        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds

    def is_open(self) -> bool:
        try:
            with socket.create_connection(
                (self._host, self._port),
                timeout=self._timeout_seconds,
            ):
                return True
        except OSError:
            return False


class FakePortProbe:
    def __init__(self, *, open_: bool = False) -> None:
        self._open = open_

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool) -> None:
        self._open = open_
