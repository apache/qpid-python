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

from qpid.testlib import TestBase
from qpid.content import Content
from qpid.harness import Skipped

import qpid.client

class EchoTests(TestBase):
  """Verify that messages can be sent and received retaining fidelity"""

  def setUp(self):
    super(EchoTests, self).setUp()
    self.frame_max_size = self.client.tune_params['frame_max']
    self.assertTrue(self.frame_max_size >= self.client.conn.FRAME_MIN_SIZE)

  def test_empty_message(self):
    body = ''
    self.echo_message(body)

  def test_small_message(self):
    body = self.uniqueString()
    self.echo_message(body)

  def test_largest_single_frame_message(self):
    max_size_within_single_frame = self.frame_max_size - self.client.conn.AMQP_HEADER_SIZE
    body = self.randomLongString(max_size_within_single_frame)
    self.echo_message(body)

  def test_multiple_frame_message(self):
    size = self.frame_max_size * 2 - (self.client.conn.FRAME_MIN_SIZE / 2)
    body = self.randomLongString(size)
    self.echo_message(body)

  def echo_message(self, body):
    channel = self.channel
    self.queue_declare(queue="q")
    channel.tx_select()
    consumer = self.consume("q", no_ack=False)
    channel.basic_publish(
            content=Content(body),
            routing_key="q")
    channel.tx_commit()
    msg = consumer.get(timeout=self.recv_timeout())
    channel.basic_ack(delivery_tag=msg.delivery_tag)
    channel.tx_commit()
    self.assertEqual(len(body), len(msg.content.body))
    self.assertEqual(body, msg.content.body)

  def test_large_message_received_in_many_content_frames(self):
    if self.client.conn.FRAME_MIN_SIZE == self.frame_max_size:
      raise Skipped("Test requires that frame_max_size (%d) exceeds frame_min_size (%d)" % (self.frame_max_size, self.frame_max_size))

    channel = self.channel

    queue_name = "q"
    self.queue_declare(queue=queue_name)

    channel.tx_select()

    body = self.randomLongString()
    channel.basic_publish(
      content=Content(body),
      routing_key=queue_name)
    channel.tx_commit()

    consuming_client = None
    try:
      # Create a second connection with minimum framesize.  The Broker will then be forced to chunk
      # the content in order to send it to us.
      consuming_client = qpid.client.Client(self.config.broker.host, self.config.broker.port or self.DEFAULT_PORT)
      tune_params = { "frame_max" : self.client.conn.FRAME_MIN_SIZE }
      consuming_client.start(username = self.config.broker.user or self.DEFAULT_USERNAME,
                             password = self.config.broker.password or self.DEFAULT_PASSWORD,
                             tune_params = tune_params)

      consuming_channel = consuming_client.channel(1)
      consuming_channel.channel_open()
      consuming_channel.tx_select()

      consumer_reply = consuming_channel.basic_consume(queue=queue_name, no_ack=False)
      consumer = consuming_client.queue(consumer_reply.consumer_tag)
      msg = consumer.get(timeout=self.recv_timeout())
      consuming_channel.basic_ack(delivery_tag=msg.delivery_tag)
      consuming_channel.tx_commit()

      self.assertEqual(len(body), len(msg.content.body))
      self.assertEqual(body, msg.content.body)
    finally:
      if consuming_client:
        consuming_client.close()

    def test_commit_ok_possibly_interleaved_with_message_delivery(self):
      """This test exposes an defect on the Java Broker (QPID-6094).  The Java Broker (0.32 and below)
         can contravene the AMQP spec by sending other frames between the message header/frames.
         As this is a long standing defect in the Java Broker, QPID-6082 changed
         the Python client to allow it to tolerate such illegal interleaving.
         """
      channel = self.channel

      queue_name = "q"
      self.queue_declare(queue=queue_name)

      count = 25
      channel.basic_qos(prefetch_count=count)

      channel.tx_select()

      bodies = []
      for i in range(count):
        body = self.randomLongString()
        bodies.append(body)
        channel.basic_publish(
          content=Content(bodies[i]),
          routing_key=queue_name)
        channel.tx_commit()

      # Start consuming.  Prefetch will mean the Broker will start to send us
      # all the messages accumulating them in the client.
      consumer = self.consume("q", no_ack=False)

      # Get and ack/commit the first message
      msg = consumer.get(timeout=self.recv_timeout())
      channel.basic_ack(delivery_tag=msg.delivery_tag)
      channel.tx_commit()
      # In the problematic case, the Broker interleaves our commit-ok response amongst the content
      # frames of message.   QPID-6082 means the Python client now tolerates this
      # problem and all messages should arrive correctly.

      expectedBody = bodies[0]
      self.assertEqual(len(expectedBody), len(msg.content.body))
      self.assertEqual(expectedBody, msg.content.body)

      for i in range(1, len(bodies)):
        msg = consumer.get(timeout=self.recv_timeout())

        expectedBody = bodies[i]
        self.assertEqual(len(expectedBody), len(msg.content.body))
        self.assertEqual(expectedBody, msg.content.body)


