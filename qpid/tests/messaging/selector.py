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
from qpid.selector import Selector, SelectorStopped
from qpid.messaging import *

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
    cstr = str(c)
    ssn = c.session()
    ssnrepr = repr(ssn)
    r = ssn.receiver("foo;{create:always,delete:always}")
    rstr = str(r)
    s = ssn.sender("foo;{create:always,delete:always}")
    srepr = str(s)

    Selector.DEFAULT.stop()

    # The following should be no-ops
    c.close()
    c.detach("foo")
    ssn.close()
    s.close()
    r.close()

    # str/repr should return the same result
    self.assertEqual(cstr, str(c))
    self.assertEqual(ssnrepr, repr(ssn))
    self.assertEqual(rstr, str(r))
    self.assertEqual(srepr, repr(s))

    # Other functions should raise exceptions
    self.assertRaises(SelectorStopped, c.session)
    self.assertRaises(SelectorStopped, ssn.sender, "foo")
    self.assertRaises(SelectorStopped, s.send, "foo")
    self.assertRaises(SelectorStopped, r.fetch)
    self.assertRaises(SelectorStopped, Connection.establish, self.broker)

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
        self.assertRaises(SelectorStopped, c.session) # But can't use parent connection
        s.send("child")
        os._exit(0)
      except Exception, e:
        print >>sys.stderr, "test child process error: %s" % e
        os.exit(1)
      finally:
        os._exit(1)             # Hard exit from child to stop remaining tests running twice
