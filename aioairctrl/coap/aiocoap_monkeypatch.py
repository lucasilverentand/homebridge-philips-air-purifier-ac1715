import asyncio
import functools
import os
import socket

from aiocoap.messagemanager import MessageManager
from aiocoap.numbers.constants import EXCHANGE_LIFETIME


# Philips purifiers can retain Observe subscriptions after a client socket is
# closed. Reusing one local UDP port keeps reconnects attached to the same
# device-side session instead of leaking a new unreachable listener each time.
_PHILIPS_COAP_LOCAL_PORT = int(os.environ.get("PHILIPS_COAP_LOCAL_PORT", "56750"))
_create_datagram_endpoint = asyncio.BaseEventLoop.create_datagram_endpoint


async def _create_datagram_endpoint_with_stable_coap_port(
    self, protocol_factory, *args, **kwargs
):
    remote_addr = kwargs.get("remote_addr")
    if (
        remote_addr
        and len(remote_addr) >= 2
        and remote_addr[1] == 5683
        and kwargs.get("local_addr") is None
    ):
        kwargs["local_addr"] = ("0.0.0.0", _PHILIPS_COAP_LOCAL_PORT)
    return await _create_datagram_endpoint(self, protocol_factory, *args, **kwargs)


asyncio.BaseEventLoop.create_datagram_endpoint = (
    _create_datagram_endpoint_with_stable_coap_port
)


# Linux uses aiocoap's udp6 transport by default. It creates the socket itself,
# so the event-loop hook above is only a fallback for simple6 platforms.
from aiocoap.transports.udp6 import MessageInterfaceUDP6


async def _create_client_transport_with_stable_coap_port(cls, ctx, log, loop):
    sock = socket.socket(family=socket.AF_INET6, type=socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(("::", _PHILIPS_COAP_LOCAL_PORT))
    return await cls._create_transport_endpoint(sock, ctx, log, loop)


MessageInterfaceUDP6.create_client_transport_endpoint = classmethod(
    _create_client_transport_with_stable_coap_port
)


def _deduplicate_message(self, message):
    key = (message.remote, message.mid)
    self.log.debug("MP: New unique message received")
    self.loop.call_later(EXCHANGE_LIFETIME, functools.partial(self._recent_messages.pop, key))
    self._recent_messages[key] = None
    return False


MessageManager._deduplicate_message = _deduplicate_message

from aiocoap.protocol import ClientObservation
from aiocoap.error import ObservationCancelled, NotObservable, LibraryShutdown


def __del__(self):
    if self._future.done():
        try:
            self._future.result()
        except (ObservationCancelled, NotObservable):
            pass
        except LibraryShutdown:
            pass
        except asyncio.CancelledError:
            pass


ClientObservation._Iterator.__del__ = __del__
