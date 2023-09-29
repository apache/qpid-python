#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
# 
#   http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

"""
An AMQP client implementation that uses a custom delegate for
interacting with the server.
"""

from __future__ import absolute_import
import os, threading
from .peer import Peer, Channel, Closed
from .delegate import Delegate
from .util import get_client_properties_with_defaults
from .connection08 import Connection, Frame, connect
from .spec08 import load
from .queue import Queue
from .reference import ReferenceId, References
from .saslmech.finder import get_sasl_mechanism
from .saslmech.sasl import SaslException


class Client:

  def __init__(self, host, port, spec = None, vhost = None):
    self.host = host
    self.port = port
    if spec:
      self.spec = spec
    else:
      from .specs_config import amqp_spec_0_9
      self.spec = load(amqp_spec_0_9)
    self.structs = StructFactory(self.spec)
    self.sessions = {}

    self.mechanism = None
    self.response = None
    self.locale = None
    self.sasl = None

    self.vhost = vhost
    if self.vhost == None:
      self.vhost = "/"

    self.queues = {}
    self.lock = threading.Lock()

    self.closed = False
    self.reason = None
    self.started = threading.Event()
    self.peer = None

  def wait(self):
    self.started.wait()
    if self.closed:
      raise Closed(self.reason)

  def queue(self, key):
    self.lock.acquire()
    try:
      try:
        q = self.queues[key]
      except KeyError:
        q = Queue(0)
        self.queues[key] = q
    finally:
      self.lock.release()
    return q

  def start(self, response=None, mechanism=None, locale="en_US", tune_params=None,
            username=None, password=None,
            client_properties=None, connection_options=None, sasl_options = None,
            channel_options=None):
    if response is not None and (username is not None or password is not None):
      raise RuntimeError("client must not specify both response and (username, password).")
    if response is not None:
      self.response = response
      authzid, self.username, self.password = response.split("\0")
    else:
      self.username = username
      self.password = password
    self.mechanism = mechanism
    self.locale = locale
    self.tune_params = tune_params
    self.client_properties=get_client_properties_with_defaults(provided_client_properties=client_properties, version_property_key="version")
    self.sasl_options = sasl_options
    self.socket = connect(self.host, self.port, connection_options)
    self.conn = Connection(self.socket, self.spec)
    self.peer = Peer(self.conn, ClientDelegate(self), Session, channel_options)

    self.conn.init()
    self.peer.start()
    self.wait()
    self.channel(0).connection_open(self.vhost)

  def channel(self, id):
    self.lock.acquire()
    try:
      ssn = self.peer.channel(id)
      ssn.client = self
      self.sessions[id] = ssn
    finally:
      self.lock.release()
    return ssn

  def session(self):
    self.lock.acquire()
    try:
      id = None
      for i in range(1, 64*1024):
        if i not in self.sessions:
          id = i
          break
    finally:
      self.lock.release()
    if id == None:
      raise RuntimeError("out of channels")
    else:
      return self.channel(id)

  def close(self):
    if self.peer:
      try:
        if not self.closed:
          channel = self.channel(0)
          if channel and not channel._closed:
             try:
               channel.connection_close(reply_code=200)
             except:
               pass
          self.closed = True
      finally:
        self.peer.stop()

class ClientDelegate(Delegate):

  def __init__(self, client):
    Delegate.__init__(self)
    self.client = client

  def connection_start(self, ch, msg):
    if self.client.mechanism is None and self.client.response is not None:
        # Supports users passing the response argument
        self.client.mechanism = "PLAIN"

    serverSupportedMechs = msg.frame.args[3].split()
    if self.client.mechanism is None:
      self.client.sasl = get_sasl_mechanism(serverSupportedMechs, self.client.username, self.client.password,
                                            sasl_options=self.client.sasl_options)
    else:
      if self.client.mechanism not in serverSupportedMechs:
        raise SaslException("sasl negotiation failed: no mechanism agreed. Client requested: '%s' Server supports: %s"
                            % (self.client.mechanism, serverSupportedMechs))
      self.client.sasl = get_sasl_mechanism([self.client.mechanism], self.client.username, self.client.password,
                                            sasl_options=self.client.sasl_options)
    if self.client.sasl is None:
      raise SaslException("sasl negotiation failed: no mechanism agreed. Client requested: '%s' Server supports: %s"
                          % (self.client.mechanism, serverSupportedMechs))
    self.client.mechanism = self.client.sasl.mechanismName()

    if self.client.response is None:
      self.client.response = self.client.sasl.initialResponse()

    msg.start_ok(mechanism=self.client.mechanism,
                 response=self.client.response or "",
                 locale=self.client.locale,
                 client_properties=self.client.client_properties)

  def connection_secure(self, ch, msg):
    msg.secure_ok(response=self.client.sasl.response(msg.challenge))

  def connection_tune(self, ch, msg):
    tune_params = dict(zip(('channel_max', 'frame_max', 'heartbeat'), (msg.frame.args)))
    if self.client.tune_params:
      tune_params.update(self.client.tune_params)
    msg.tune_ok(**tune_params)
    self.client.tune_params = tune_params
    self.client.started.set()

  def message_transfer(self, ch, msg):
    self.client.queue(msg.destination).put(msg)

  def message_open(self, ch, msg):
    ch.references.open(msg.reference)

  def message_close(self, ch, msg):
    ch.references.close(msg.reference)

  def message_append(self, ch, msg):
    ch.references.get(msg.reference).append(msg.bytes)

  def message_acquired(self, ch, msg):
    ch.control_queue.put(msg)

  def basic_deliver(self, ch, msg):
    self.client.queue(msg.consumer_tag).put(msg)

  def channel_pong(self, ch, msg):
    msg.ok()

  def channel_close(self, ch, msg):
    ch.closed(msg)

  def channel_flow(self, ch, msg):
    # On resuming we don't want to send a message before flow-ok has been sent.
    # Therefore, we send flow-ok before we set the flow_control flag.
    if msg.active:
        msg.flow_ok()
    ch.set_flow_control(not msg.active)
    # On suspending we don't want to send a message after flow-ok has been sent.
    # Therefore, we send flow-ok after we set the flow_control flag.
    if not msg.active:
        msg.flow_ok()

  def session_ack(self, ch, msg):
    pass

  def session_closed(self, ch, msg):
    ch.closed(msg)

  def connection_close(self, ch, msg):
    self.client.peer.closed(msg)

  def execution_complete(self, ch, msg):
    ch.completion.complete(msg.cumulative_execution_mark)

  def execution_result(self, ch, msg):
    future = ch.futures[msg.command_id]
    future.put_response(ch, msg.data)

  def closed(self, reason):
    self.client.closed = True
    self.client.reason = reason
    self.client.started.set()
    with self.client.lock:
      for queue in list(self.client.queues.values()):
        queue.close(reason)

class StructFactory:

  def __init__(self, spec):
    self.spec = spec
    self.factories = {}

  def __getattr__(self, name):
    if name in self.factories:
      return self.factories[name]
    elif name in self.spec.domains.byname:
      f = lambda *args, **kwargs: self.struct(name, *args, **kwargs)
      self.factories[name] = f
      return f
    else:
      raise AttributeError(name)

  def struct(self, name, *args, **kwargs):
    return self.spec.struct(name, *args, **kwargs)

class Session(Channel):

  def __init__(self, *args):
    Channel.__init__(self, *args)
    self.references = References()
    self.client = None

  def open(self):
    self.session_open()

  def close(self):
    self.session_close()
    self.client.lock.acquire()
    try:
      del self.client.sessions[self.id]
    finally:
      self.client.lock.release()
