"""Solace JMS-like service implementation using Solace Python API."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solace.messaging.config.missing_resources_creation_configuration import MissingResourcesCreationStrategy
from solace.messaging.config.solace_properties import (
    authentication_properties,
    service_properties,
    transport_layer_properties,
)
from solace.messaging.messaging_service import MessagingService
from solace.messaging.receiver.message_receiver import MessageHandler
from solace.messaging.receiver.persistent_message_receiver import PersistentMessageReceiver
from solace.messaging.resources.queue import Queue

_DEFAULT_CERT_PATH = Path(__file__).parent / "cacerts"


log = logging.getLogger(__name__)


@dataclass
class JmsConfig:
    """Config values for data push service."""

    username: str
    password: str
    queue_name: str
    connection_factory: str
    url: str
    message_vpn: str

    thread_count: int = 4
    ssl_trust_store: Path = _DEFAULT_CERT_PATH
    jndi_connection_retries: int = 0  # -1 for infinite retries


class JmsService:
    """Solace JMS-like service implementation using Solace Python API."""

    config: JmsConfig
    service: MessagingService
    receiver: PersistentMessageReceiver | None = None

    def __init__(self, config: JmsConfig, event_handler: Any = None):
        self.config = config

        self.service = (
            MessagingService.builder()
            .from_properties(
                {
                    authentication_properties.SCHEME_BASIC_PASSWORD: config.password,
                    authentication_properties.SCHEME_BASIC_USER_NAME: config.username,
                    service_properties.VPN_NAME: config.message_vpn,
                    transport_layer_properties.CONNECTION_RETRIES: config.jndi_connection_retries,
                    transport_layer_properties.HOST: config.url,
                    transport_layer_properties.RECONNECTION_ATTEMPTS: config.jndi_connection_retries,
                    # transport_layer_security_properties.TRUST_STORE_PATH: config.ssl_trust_store.absolute().as_posix(),
                    # transport_layer_properties.RECONNECTION_ATTEMPTS_WAIT_INTERVAL: 3000,
                    # transport_layer_security_properties.CERT_VALIDATED: False,
                    # transport_layer_security_properties.CERT_REJECT_EXPIRED: False,
                    # transport_layer_security_properties.CERT_VALIDATE_SERVERNAME: False,
                }
            )
            .build()
        )

        # Blocking connect thread
        log.debug("Connecting to Messaging Service at %s...", self.config.url)
        self.service.connect()
        log.debug("Messaging Service connected? %s", self.service.is_connected)

        # Event Handling for the messaging service
        if event_handler:
            if hasattr(event_handler, "on_reconnected"):
                self.service.add_reconnection_listener(event_handler)
            if hasattr(event_handler, "on_reconnecting"):
                self.service.add_reconnection_attempt_listener(event_handler)
            if hasattr(event_handler, "on_service_interrupted"):
                self.service.add_service_interruption_listener(event_handler)

    def listen(self, handler: type[MessageHandler], queue_name: str | None = None) -> None:
        """Start listening for messages on the specified queue."""
        # NOTE: This assumes that a persistent queue already exists on the broker with the right topic subscription
        durable_exclusive_queue = Queue.durable_exclusive_queue(queue_name or self.config.queue_name)

        # Build a receiver and bind it to the durable exclusive queue
        self.receiver: PersistentMessageReceiver = (
            self.service.create_persistent_message_receiver_builder()
            .with_missing_resources_creation_strategy(MissingResourcesCreationStrategy.CREATE_ON_START)
            .build(durable_exclusive_queue)
        )
        self.receiver.start()

        # Callback for received messages
        self.receiver.receive_async(handler(self.receiver))
        log.debug("PERSISTENT receiver started... Bound to Queue [%s]", durable_exclusive_queue.get_name())

    def close(self) -> None:
        """Close the JMS service."""
        if self.receiver is None:
            msg = "Receiver was never started, nothing to close"
            raise ValueError(msg)
        if self.receiver and self.receiver.is_running():
            log.debug("Terminating receiver")
            self.receiver.terminate(grace_period=0)
        log.debug("Disconnecting Messaging Service")
        self.service.disconnect()
