# Copyright 2014 - Rackspace Hosting
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

"""Common RPC service and API tools for Magnum."""

import oslo_messaging as messaging
from oslo_service import service

from magnum.common import profiler
from magnum.common import rpc
import magnum.conf
from magnum.objects import base as objects_base
from magnum.service import periodic
from magnum.servicegroup import magnum_service_periodic as servicegroup


CONF = magnum.conf.CONF


class Service(service.Service):

    def __init__(self, topic, server, handlers, binary):
        super(Service, self).__init__()
        # TODO(asalkeld) add support for version='x.y'
        target = messaging.Target(topic=topic, server=server)
        self._server = rpc.get_server(
            target, handlers,
            serializer=objects_base.MagnumObjectSerializer()
        )

        self.binary = binary
        profiler.setup(binary, CONF.host)

    def start(self):
        self._server.start()

    def create_periodic_tasks(self):
        if CONF.periodic_enable:
            periodic.setup(CONF, self.tg)
        servicegroup.setup(CONF, self.binary, self.tg)

    def stop(self):
        if self._server:
            self._server.stop()
            self._server.wait()
        super(Service, self).stop()

    @classmethod
    def create(cls, topic, server, handlers, binary):
        service_obj = cls(topic, server, handlers, binary)
        return service_obj


# Share a single RPCClient per unique (topic, server, timeout) tuple
# for the lifetime of the worker process.  The per-request context is injected
# via RPCClient.prepare(), which returns a lightweight _CallContext that reuses
# the same underlying transport connections.
_RPC_CLIENT_CACHE = {}
_RPC_CLIENT_CACHE_LOCK = None  # initialised lazily (post-fork safe)


def _get_cached_client(topic, server, timeout):
    """Return a process-level cached RPCClient for the given target parameters.

    The client is created once per (topic, server, timeout) combination and
    reused across all requests.  This keeps the RabbitMQ connection pool warm
    and avoids per-request TCP connect/disconnect cycles.
    """
    import threading
    global _RPC_CLIENT_CACHE_LOCK
    if _RPC_CLIENT_CACHE_LOCK is None:
        # Initialise lazily so the lock is created in the worker process after
        # werkzeug has forked, avoiding any shared-lock issues.
        _RPC_CLIENT_CACHE_LOCK = threading.Lock()

    key = (topic, server, timeout)
    client = _RPC_CLIENT_CACHE.get(key)
    if client is not None:
        return client

    with _RPC_CLIENT_CACHE_LOCK:
        client = _RPC_CLIENT_CACHE.get(key)
        if client is None:
            target = messaging.Target(topic=topic, server=server)
            client = rpc.get_client(
                target,
                serializer=objects_base.MagnumObjectSerializer(),
                timeout=timeout,
            )
            _RPC_CLIENT_CACHE[key] = client
    return client


class API(object):
    def __init__(self, context=None, topic=None, server=None,
                 timeout=None):
        self._context = context
        if topic is None:
            topic = ''
        self._topic = topic
        self._server = server
        self._timeout = timeout
        # Fetch (or create) the shared client; do NOT store as a per-instance
        # attribute called _client on the class so that subclasses that access
        # self._client directly (conductor_api.API OVO indirection methods)
        # still work – they use the shared client via the property below.
        self.__shared_client = _get_cached_client(topic, server, timeout)

    @property
    def _client(self):
        """Shared RPCClient; context is injected per-call via prepare()."""
        return self.__shared_client

    def _call(self, method, *args, **kwargs):
        # prepare() binds the context without touching transport connections.
        return self._client.prepare().call(
            self._context, method, *args, **kwargs)

    def _cast(self, method, *args, **kwargs):
        self._client.prepare().cast(
            self._context, method, *args, **kwargs)

    def echo(self, message):
        self._cast('echo', message=message)
