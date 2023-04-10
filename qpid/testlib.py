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

#
# Support library for qpid python tests.
#

from __future__ import absolute_import
from __future__ import print_function
import string
import random

import unittest, traceback, socket
import qpid.client, qmf.console
try:
    import Queue
except ImportError:
    import queue as Queue
from qpid.content import Content
from qpid.message import Message
from qpid.harness import Skipped
from qpid.exceptions import VersionError

import qpid.messaging
from qpidtoollibs import BrokerAgent

class TestBase(unittest.TestCase):
    """Base class for Qpid test cases.

    self.client is automatically connected with channel 1 open before
    the test methods are run.

    Deletes queues and exchanges after.  Tests call
    self.queue_declare(channel, ...) and self.exchange_declare(chanel,
    ...) which are wrappers for the Channel functions that note
    resources to clean up later.
    """
    DEFAULT_USERNAME = "guest"
    DEFAULT_PASSWORD = "guest"
    DEFAULT_PORT = 5672

    def configure(self, config):
        self.config = config

    def setUp(self):
        self.queues = []
        self.exchanges = []
        self.client = self.connect()
        self.channel = self.client.channel(1)
        self.version = (self.client.spec.major, self.client.spec.minor)
        if self.version == (8, 0) or self.version == (0, 9):
            self.channel.channel_open()
        else:
            self.channel.session_open()

    def tearDown(self):
        try:
            for ch, q in self.queues:
                ch.queue_delete(queue=q)
            for ch, ex in self.exchanges:
                ch.exchange_delete(exchange=ex)
        except:
            print("Error on tearDown:")
            print(traceback.print_exc())

        self.client.close()

    def recv_timeout(self):
        """Timeout used when a message is anticipated."""
        return float(self.config.defines.get("qpid.recv_timeout", "1"))

    def recv_timeout_negative(self):
        """Timeout used when a message is NOT expected."""
        return float(self.config.defines.get("qpid.recv_timeout_negative", "0.5"))

    def connect(self, host=None, port=None, user=None, password=None, tune_params=None, client_properties=None, channel_options=None):
        """Create a new connction, return the Client object"""
        host = host or self.config.broker.host
        port = port or self.config.broker.port or self.DEFAULT_PORT
        user = user or self.config.broker.user or self.DEFAULT_USERNAME
        password = password or self.config.broker.password or self.DEFAULT_PASSWORD
        client = qpid.client.Client(host, port)
        try:
          client.start(username = user, password=password, tune_params=tune_params, client_properties=client_properties, channel_options=channel_options)
        except qpid.client.Closed as e:
            if isinstance(e.args[0], VersionError):
                raise Skipped(e.args[0])
            else:
                raise e
        except socket.error as e:
            raise Skipped(e)
        return client

    def queue_declare(self, channel=None, *args, **keys):
        channel = channel or self.channel
        reply = channel.queue_declare(*args, **keys)
        self.queues.append((channel, keys["queue"]))
        return reply

    def exchange_declare(self, channel=None, ticket=0, exchange='',
                         type='', passive=False, durable=False,
                         auto_delete=False,
                         arguments={}):
        channel = channel or self.channel
        reply = channel.exchange_declare(ticket=ticket, exchange=exchange, type=type, passive=passive,durable=durable, auto_delete=auto_delete, arguments=arguments)
        self.exchanges.append((channel,exchange))
        return reply

    def uniqueString(self):
        """Generate a unique string, unique for this TestBase instance"""
        if not "uniqueCounter" in dir(self): self.uniqueCounter = 1;
        return "Test Message " + str(self.uniqueCounter)

    def randomLongString(self, length=65535):
      body = ''.join(random.choice(string.ascii_uppercase) for _ in range(length))
      return body

    def consume(self, queueName, no_ack=True):
        """Consume from named queue returns the Queue object."""

        reply = self.channel.basic_consume(queue=queueName, no_ack=no_ack)
        return self.client.queue(reply.consumer_tag)

    def subscribe(self, channel=None, **keys):
        channel = channel or self.channel
        consumer_tag = keys["destination"]
        channel.message_subscribe(**keys)
        channel.message_flow(destination=consumer_tag, unit=0, value=0xFFFFFFFF)
        channel.message_flow(destination=consumer_tag, unit=1, value=0xFFFFFFFF)

    def assertEmpty(self, queue):
        """Assert that the queue is empty"""
        try:
            queue.get(timeout=1)
            self.fail("Queue is not empty.")
        except Queue.Empty: None              # Ignore

    def assertPublishGet(self, queue, exchange="", routing_key="", properties=None):
        """
        Publish to exchange and assert queue.get() returns the same message.
        """
        body = self.uniqueString()
        self.channel.basic_publish(
            exchange=exchange,
            content=Content(body, properties=properties),
            routing_key=routing_key)
        msg = queue.get(timeout=1)
        self.assertEqual(body, msg.content.body)
        if (properties):
            self.assertEqual(properties, msg.content.properties)

    def assertPublishConsume(self, queue="", exchange="", routing_key="", properties=None):
        """
        Publish a message and consume it, assert it comes back intact.
        Return the Queue object used to consume.
        """
        self.assertPublishGet(self.consume(queue), exchange, routing_key, properties)

    def assertChannelException(self, expectedCode, message):
        if self.version == (8, 0) or self.version == (0, 9):
            if not isinstance(message, Message): self.fail("expected channel_close method, got %s" % (message))
            self.assertEqual("channel", message.method.klass.name)
            self.assertEqual("close", message.method.name)
        else:
            if not isinstance(message, Message): self.fail("expected session_closed method, got %s" % (message))
            self.assertEqual("session", message.method.klass.name)
            self.assertEqual("closed", message.method.name)
        self.assertEqual(expectedCode, message.reply_code)


    def assertConnectionException(self, expectedCode, message):
        if not isinstance(message, Message): self.fail("expected connection_close method, got %s" % (message))
        self.assertEqual("connection", message.method.klass.name)
        self.assertEqual("close", message.method.name)
        self.assertEqual(expectedCode, message.reply_code)

#0-10 support
from qpid.connection import Connection
from qpid.util import connect, ssl, URL

class TestBase010(unittest.TestCase):
    """
    Base class for Qpid test cases. using the final 0-10 spec
    """
    DEFAULT_USERNAME = "guest"
    DEFAULT_PASSWORD = "guest"
    DEFAULT_PORT = 5672
    DEFAULT_PORT_TLS = 5671

    def configure(self, config):
        self.config = config
        self.broker = config.broker
        self.defines = self.config.defines

    def setUp(self):
        self.conn = self.connect()
        self.session = self.conn.session("test-session", timeout=10)
        self.qmf = None
        self.test_queue_name = self.id()

    def startQmf(self, handler=None):
        self.qmf = qmf.console.Session(handler)
        self.qmf_broker = self.qmf.addBroker(str(self.broker))

    def startBrokerAccess(self):
        """
        New-style management access to the broker.  Can be used in lieu of startQmf.
        """
        if 'broker_conn' not in self.__dict__:
            self.broker_conn = qpid.messaging.Connection(str(self.broker))
            self.broker_conn.open()
            self.broker_access = BrokerAgent(self.broker_conn)

    def connect(self, host=None, port=None):
        url = self.broker
        if url.scheme == URL.AMQPS:
            default_port = self.DEFAULT_PORT_TLS
        else:
            default_port = self.DEFAULT_PORT
        try:
            sock = connect(host or url.host, port or url.port or default_port)
        except socket.error as e:
            raise Skipped(e)
        if url.scheme == URL.AMQPS:
            sock = ssl(sock)
        conn = Connection(sock, username=url.user or self.DEFAULT_USERNAME,
                          password=url.password or self.DEFAULT_PASSWORD)
        try:
            conn.start(timeout=10)
        except VersionError as e:
            raise Skipped(e)
        return conn

    def tearDown(self):
        if not self.session.error(): self.session.close(timeout=10)
        self.conn.close(timeout=10)
        if self.qmf:
            self.qmf.delBroker(self.qmf_broker)

    def subscribe(self, session=None, **keys):
        session = session or self.session
        consumer_tag = keys["destination"]
        session.message_subscribe(**keys)
        session.message_flow(destination=consumer_tag, unit=0, value=0xFFFFFFFF)
        session.message_flow(destination=consumer_tag, unit=1, value=0xFFFFFFFF)
