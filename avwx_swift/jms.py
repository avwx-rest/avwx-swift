"""Solace messaging transport for FAA SWIM services.

SWIM's cloud distribution (SCDS) hands out a durable Solace queue per approved
subscription. This module is only the transport: connect, bind, pull, acknowledge. What the
messages mean belongs to the product module using it — `fns` for NOTAMs.

The API is deliberately synchronous, like `fil`. The Solace client is blocking and
thread-based, so an async caller should push these calls to a worker thread rather than have
the library guess at an event-loop model.

Typical use:

```python
from avwx_swift.jms import JmsConfig, JmsService

with JmsService(JmsConfig.from_env()) as service:
    for message in service.drain(limit=500):
        handle(message.payload)
    service.acknowledge_all()
```

Messages are acknowledged explicitly and never automatically. Solace redelivers anything
unacknowledged, so a consumer that dies mid-batch replays rather than losing messages —
which only works if the acknowledgement happens *after* the work is durably done.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import certifi
from solace.messaging.config.missing_resources_creation_configuration import (
    MissingResourcesCreationStrategy,
)
from solace.messaging.config.solace_properties import (
    authentication_properties,
    service_properties,
    transport_layer_properties,
    transport_layer_security_properties,
)
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.queue import Queue

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

log = logging.getLogger(__name__)

#: SCDS issues a queue per subscription; it is never created by the client.
DEFAULT_ENV_PREFIX = "SWIM_"
DEFAULT_IDLE_TIMEOUT = 10
DEFAULT_BATCH_SIZE = 500


class JmsConfigError(ValueError):
    """Required connection settings are missing."""


def _default_trust_store() -> Path:
    """The directory holding CA certificates, which is what Solace wants — not a file."""
    return Path(certifi.where()).parent


@dataclass(frozen=True, slots=True)
class JmsConfig:
    """Everything needed to reach a SWIM queue.

    The five required values arrive in the approval email SCDS sends, and are visible
    afterwards on the subscription's Connection Details tab.
    """

    username: str
    password: str
    queue_name: str
    url: str
    message_vpn: str
    #: Present in the connection details but unused by the Solace Python client, which
    #: binds to the queue directly rather than through a JNDI connection factory.
    connection_factory: str | None = None
    #: Directory of CA certificates. The broker presents a publicly trusted certificate,
    #: so the default bundle validates it without any extra setup.
    trust_store_path: Path = field(default_factory=_default_trust_store)
    #: Leave on. Disabling it hides an expired or misissued broker certificate, and this
    #: broker has never needed it off.
    validate_certificate: bool = True
    connection_retries: int = 1
    reconnection_attempts: int = 3
    reconnection_wait_ms: int = 3000

    @classmethod
    def from_env(cls, prefix: str = DEFAULT_ENV_PREFIX, **overrides: Any) -> Self:
        """Build a config from environment variables.

        Reads `<prefix>USERNAME`, `PASSWORD`, `QUEUE`, `URL`, `VPN`, and optionally
        `CONNECTION_FACTORY` — the names SCDS uses for them. Keyword arguments override
        whatever the environment supplies.

        Raises:
            JmsConfigError: naming every variable that is missing, rather than the first.
        """
        # Field name to the variable it reads, kept explicit so an error can name the
        # variable the caller actually has to set.
        sources = {
            "username": "USERNAME",
            "password": "PASSWORD",
            "queue_name": "QUEUE",
            "url": "URL",
            "message_vpn": "VPN",
            "connection_factory": "CONNECTION_FACTORY",
        }
        values = {field: os.environ.get(f"{prefix}{name}") for field, name in sources.items()}
        values |= overrides
        missing = [
            f"{prefix}{sources[field]}"
            for field in ("username", "password", "queue_name", "url", "message_vpn")
            if not values.get(field)
        ]
        if missing:
            msg = f"Missing connection settings: {', '.join(missing)}"
            raise JmsConfigError(msg)
        return cls(**values)  # type: ignore[arg-type]

    def as_properties(self) -> dict[str, Any]:
        """Translate into the property map the Solace client expects."""
        return {
            authentication_properties.SCHEME_BASIC_USER_NAME: self.username,
            authentication_properties.SCHEME_BASIC_PASSWORD: self.password,
            service_properties.VPN_NAME: self.message_vpn,
            transport_layer_properties.HOST: self.url,
            transport_layer_properties.CONNECTION_RETRIES: self.connection_retries,
            transport_layer_properties.RECONNECTION_ATTEMPTS: self.reconnection_attempts,
            transport_layer_properties.RECONNECTION_ATTEMPTS_WAIT_INTERVAL: self.reconnection_wait_ms,
            transport_layer_security_properties.TRUST_STORE_PATH: str(self.trust_store_path),
            transport_layer_security_properties.CERT_VALIDATED: self.validate_certificate,
        }


@dataclass(frozen=True, slots=True)
class JmsMessage:
    """One message off the queue, with the broker metadata that came with it."""

    payload: str
    properties: dict[str, str]

    def property(self, name: str, default: str | None = None) -> str | None:
        return self.properties.get(name, default)


class JmsService:
    """A held session against one SWIM queue.

    Usable as a context manager, which is the safer form: the session is closed even if
    the consumer raises partway through a batch.
    """

    def __init__(self, config: JmsConfig) -> None:
        self.config = config
        self._service: MessagingService | None = None
        self._receiver: Any = None
        self._unacknowledged: list[Any] = []

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def is_connected(self) -> bool:
        return bool(self._service and self._service.is_connected)

    @property
    def pending_acknowledgement(self) -> int:
        """How many messages have been read but not yet acknowledged."""
        return len(self._unacknowledged)

    def connect(self) -> None:
        """Open the session and bind to the queue."""
        service = MessagingService.builder().from_properties(self.config.as_properties()).build()
        service.connect()
        # Recorded before the bind, so close() can disconnect it if the bind fails.
        self._service = service
        # DO_NOT_CREATE: SCDS provisions the queue when it approves the subscription.
        # Creating one here would bind to an empty queue instead of failing, and the feed
        # would look healthy while delivering nothing.
        receiver = (
            service.create_persistent_message_receiver_builder()
            .with_missing_resources_creation_strategy(MissingResourcesCreationStrategy.DO_NOT_CREATE)
            .build(Queue.durable_exclusive_queue(self.config.queue_name))
        )
        try:
            receiver.start()
        except Exception:
            self.close()
            raise
        self._receiver = receiver
        log.debug("Bound to %s on %s", self.config.queue_name, self.config.url)

    def receive(self, timeout: int = DEFAULT_IDLE_TIMEOUT) -> JmsMessage | None:
        """Pull the next message, or None if the queue stays quiet for `timeout` seconds.

        The message is held for acknowledgement until `acknowledge_all` is called.
        """
        if self._receiver is None:
            msg = "Not connected"
            raise RuntimeError(msg)
        message = self._receiver.receive_message(timeout=timeout * 1000)
        if message is None:
            return None
        self._unacknowledged.append(message)
        return JmsMessage(
            payload=message.get_payload_as_string() or "",
            properties={str(k): str(v) for k, v in (message.get_properties() or {}).items()},
        )

    def drain(self, limit: int = DEFAULT_BATCH_SIZE, timeout: int = DEFAULT_IDLE_TIMEOUT) -> Iterator[JmsMessage]:
        """Yield up to `limit` messages, stopping early once the queue goes quiet.

        Whatever is left stays on the queue — it is durable, so a partial drain defers
        work rather than dropping it.
        """
        for _ in range(limit):
            message = self.receive(timeout)
            if message is None:
                return
            yield message

    def acknowledge_all(self) -> int:
        """Acknowledge every message read so far, and report how many.

        Only safe once the work those messages represent is durably done. Until this is
        called they remain on the broker and will be redelivered.
        """
        if self._receiver is None:
            return 0
        count = len(self._unacknowledged)
        for message in self._unacknowledged:
            self._receiver.ack(message)
        self._unacknowledged.clear()
        return count

    def close(self) -> None:
        """Tear down the receiver and session. Unacknowledged messages return to the queue."""
        if self._receiver is not None:
            try:
                self._receiver.terminate(grace_period=0)
            except Exception as exc:  # noqa: BLE001 - undelivered messages are expected here
                log.debug("Receiver terminate: %s", type(exc).__name__)
        if self._service is not None:
            self._service.disconnect()
        self._service = self._receiver = None
        self._unacknowledged.clear()
