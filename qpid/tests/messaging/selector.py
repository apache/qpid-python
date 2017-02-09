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

import sys, os
from logging import getLogger
from unittest import TestCase
from qpid.selector import Selector
from qpid.messaging import *
from qpid.messaging.exceptions import InternalError

class SelectorTests(TestCase):
  """Make sure that using a connection after a selector stops raises and doesn't hang"""

  def setUp(self):
    self.log = getLogger("qpid.messaging")
    self.propagate = self.log.propagate
    self.log.propagate = False  # Disable for tests, expected log output is noisy

  def tearDown(self):
    # Clear out any broken selector so next test can function
    Selector.DEFAULT = None
    self.log.propagate = self.propagate  # Restore setting

  def configure(self, config):
    self.broker = config.broker

  def test_use_after_stop(self):
    """Create endpoints, stop the selector, try to use them"""
    c = Connection.establish(self.broker)
    ssn = c.session()
    r = ssn.receiver("foo;{create:always,delete:always}")
    s = ssn.sender("foo;{create:always,delete:always}")

    Selector.DEFAULT.stop()
    self.assertRaises(InternalError, c.session)
    self.assertRaises(InternalError, ssn.sender, "foo")
    self.assertRaises(InternalError, s.send, "foo")
    self.assertRaises(InternalError, r.fetch)
    self.assertRaises(InternalError, Connection.establish, self.broker)

  def test_use_after_fork(self):
    c = Connection.establish(self.broker)
    pid = os.fork()
    if pid:                     # Parent
      self.assertEqual((pid, 0), os.waitpid(pid, 0))
      self.assertEqual("child", c.session().receiver("child;{create:always}").fetch().content)
    else:                       # Child
      try:
        # Can establish new connections
        s = Connection.establish(self.broker).session().sender("child;{create:always}")
        self.assertRaises(InternalError, c.session) # But can't use parent connection
        s.send("child")
        os._exit(0)
      except Exception, e:
        print >>sys.stderr, "test child process error: %s" % e
        os.exit(1)
      finally:
        os._exit(1)             # Hard exit from child to stop remaining tests running twice
