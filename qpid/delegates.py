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

import os, session
import qpid.connection
from util import notify, get_client_properties_with_defaults
from datatypes import RangedSet
from exceptions import VersionError, Closed
from logging import getLogger
from ops import Control
import sys
from qpid import sasl

log = getLogger("qpid.io.ctl")

class Delegate:

  def __init__(self, connection, delegate=session.client):
    self.connection = connection
    self.delegate = delegate

  def received(self, op):
    ssn = self.connection.attached.get(op.channel)
    if ssn is None:
      ch = qpid.connection.Channel(self.connection, op.channel)
    else:
      ch = ssn.channel

    if isinstance(op, Control):
      log.debug("RECV %s", op)
      getattr(self, op.NAME)(ch, op)
    elif ssn is None:
      ch.session_detached()
    else:
      ssn.received(op)

  def connection_close(self, ch, close):
    self.connection.close_code = (close.reply_code, close.reply_text)
    ch.connection_close_ok()
    raise Closed(close.reply_text)

  def connection_close_ok(self, ch, close_ok):
    self.connection.opened = False
    self.connection.closed = True
    notify(self.connection.condition)

  def connection_heartbeat(self, ch, hrt):
    pass

  def session_attach(self, ch, a):
    try:
      self.connection.attach(a.name, ch, self.delegate, a.force)
      ch.session_attached(a.name)
    except qpid.connection.ChannelBusy:
      ch.session_detached(a.name)
    except qpid.connection.SessionBusy:
      ch.session_detached(a.name)

  def session_attached(self, ch, a):
    notify(ch.session.condition)

  def session_detach(self, ch, d):
    #send back the confirmation of detachment before removing the
    #channel from the attached set; this avoids needing to hold the
    #connection lock during the sending of this control and ensures
    #that if the channel is immediately reused for a new session the
    #attach request will follow the detached notification.
    ch.session_detached(d.name)
    ssn = self.connection.detach(d.name, ch)

  def session_detached(self, ch, d):
    self.connection.detach(d.name, ch)

  def session_request_timeout(self, ch, rt):
    ch.session_timeout(rt.timeout);

  def session_command_point(self, ch, cp):
    ssn = ch.session
    ssn.receiver.next_id = cp.command_id
    ssn.receiver.next_offset = cp.command_offset

  def session_completed(self, ch, cmp):
    ch.session.sender.completed(cmp.commands)
    if cmp.timely_reply:
      ch.session_known_completed(cmp.commands)
    notify(ch.session.condition)

  def session_known_completed(self, ch, kn_cmp):
    ch.session.receiver.known_completed(kn_cmp.commands)

  def session_flush(self, ch, f):
    rcv = ch.session.receiver
    if f.expected:
      if rcv.next_id == None:
        exp = None
      else:
        exp = RangedSet(rcv.next_id)
      ch.session_expected(exp)
    if f.confirmed:
      ch.session_confirmed(rcv._completed)
    if f.completed:
      ch.session_completed(rcv._completed)

class Server(Delegate):

  def start(self):
    self.connection.read_header()
    # XXX
    self.connection.write_header(0, 10)
    qpid.connection.Channel(self.connection, 0).connection_start(mechanisms=["ANONYMOUS"])

  def connection_start_ok(self, ch, start_ok):
    ch.connection_tune(channel_max=65535)

  def connection_tune_ok(self, ch, tune_ok):
    pass

  def connection_open(self, ch, open):
    self.connection.opened = True
    ch.connection_open_ok()
    notify(self.connection.condition)

class Client(Delegate):

  def __init__(self, connection, username=None, password=None,
               mechanism=None, heartbeat=None, **kwargs):
    Delegate.__init__(self, connection)
    provided_client_properties = kwargs.get("client_properties")
    self.client_properties=get_client_properties_with_defaults(provided_client_properties)

    ##
    ## self.acceptableMechanisms is the list of SASL mechanisms that the client is willing to
    ## use.  If it's None, then any mechanism is acceptable.
    ##
    self.acceptableMechanisms = None
    if mechanism:
      self.acceptableMechanisms = mechanism.split(" ")
    self.heartbeat = heartbeat
    self.username  = username
    self.password  = password

    self.sasl = sasl.Client()
    if username and len(username) > 0:
      self.sasl.setAttr("username", str(username))
    if password and len(password) > 0:
      self.sasl.setAttr("password", str(password))
    self.sasl.setAttr("service", str(kwargs.get("service", "qpidd")))
    if "host" in kwargs:
      self.sasl.setAttr("host", str(kwargs["host"]))
    if "min_ssf" in kwargs:
      self.sasl.setAttr("minssf", kwargs["min_ssf"])
    if "max_ssf" in kwargs:
      self.sasl.setAttr("maxssf", kwargs["max_ssf"])
    self.sasl.init()

  def start(self):
    # XXX
    cli_major = 0
    cli_minor = 10
    self.connection.write_header(cli_major, cli_minor)
    magic, _, _, major, minor = self.connection.read_header()
    if not (magic == "AMQP" and major == cli_major and minor == cli_minor):
      raise VersionError("client: %s-%s, server: %s-%s" %
                         (cli_major, cli_minor, major, minor))

  def connection_start(self, ch, start):
    mech_list = ""
    for mech in start.mechanisms:
      if (not self.acceptableMechanisms) or mech in self.acceptableMechanisms:
        mech_list += str(mech) + " "
    mech = None
    initial = None
    try:
      mech, initial = self.sasl.start(mech_list)
    except Exception as e:
      raise Closed(str(e))
    ch.connection_start_ok(client_properties=self.client_properties,
                           mechanism=mech, response=initial)

  def connection_secure(self, ch, secure):
    resp = None
    try:
      resp = self.sasl.step(secure.challenge)
    except Exception as e:
      raise Closed(str(e))
    ch.connection_secure_ok(response=resp)

  def connection_tune(self, ch, tune):
    ch.connection_tune_ok(heartbeat=self.heartbeat)
    ch.connection_open()
    self.connection.user_id = self.sasl.auth_username()
    self.connection.security_layer_tx = self.sasl

  def connection_open_ok(self, ch, open_ok):
    self.connection.security_layer_rx = self.sasl
    self.connection.opened = True
    notify(self.connection.condition)

  def connection_heartbeat(self, ch, hrt):
    ch.connection_heartbeat()
