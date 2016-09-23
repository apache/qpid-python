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
import time, errno, os
from compat import select, SelectError, set, selectable_waiter, format_exc
from threading import Thread, Lock
from logging import getLogger

log = getLogger("qpid.messaging")

class SelectorException(Exception): pass

class Acceptor:

  def __init__(self, sock, handler):
    self.sock = sock
    self.handler = handler

  def fileno(self):
    return self.sock.fileno()

  def reading(self):
    return True

  def writing(self):
    return False

  def readable(self):
    sock, addr = self.sock.accept()
    self.handler(sock)

class Selector:

  lock = Lock()
  DEFAULT = None
  _current_pid = None

  @staticmethod
  def default():
    Selector.lock.acquire()
    try:
      if Selector.DEFAULT is None or Selector._current_pid != os.getpid():
        # If we forked, mark the existing Selector dead.
        if Selector.DEFAULT is not None:
          log.warning("qpid.messaging: process was forked")
          Selector.DEFAULT.dead(
            SelectorException("qpid.messaging: forked child process used parent connection"), True)
        sel = Selector()
        sel.start()
        Selector.DEFAULT = sel
        Selector._current_pid = os.getpid()
      return Selector.DEFAULT
    finally:
      Selector.lock.release()

  def __init__(self):
    self.selectables = set()
    self.reading = set()
    self.writing = set()
    self.waiter = selectable_waiter()
    self.reading.add(self.waiter)
    self.stopped = False
    self.thread = None
    self.exception = None

  def wakeup(self):
    if self.exception:
      log.error(str(self.exception))
      raise self.exception
    self.waiter.wakeup()

  def register(self, selectable):
    self.selectables.add(selectable)
    self.modify(selectable)

  def _update(self, selectable):
    if selectable.reading():
      self.reading.add(selectable)
    else:
      self.reading.discard(selectable)
    if selectable.writing():
      self.writing.add(selectable)
    else:
      self.writing.discard(selectable)
    return selectable.timing()

  def modify(self, selectable):
    self._update(selectable)
    self.wakeup()

  def unregister(self, selectable):
    self.reading.discard(selectable)
    self.writing.discard(selectable)
    self.selectables.discard(selectable)
    self.wakeup()

  def start(self):
    self.stopped = False
    self.exception = None
    self.thread = Thread(target=self.run)
    self.thread.setDaemon(True)
    self.thread.start();

  def run(self):
    try:
      while not self.stopped and not self.exception:
        wakeup = None
        for sel in self.selectables.copy():
          t = self._update(sel)
          if t is not None:
            if wakeup is None:
              wakeup = t
            else:
              wakeup = min(wakeup, t)

        rd = []
        wr = []
        ex = []

        while True:
          try:
            if wakeup is None:
              timeout = None
            else:
              timeout = max(0, wakeup - time.time())
            rd, wr, ex = select(self.reading, self.writing, (), timeout)
            break
          except SelectError, e:
            # Repeat the select call if we were interrupted.
            if e[0] == errno.EINTR:
              continue
            else:
              # unrecoverable: promote to outer try block
              raise

        for sel in wr:
          if sel.writing():
            sel.writeable()

        for sel in rd:
          if sel.reading():
            sel.readable()

        now = time.time()
        for sel in self.selectables.copy():
          w = sel.timing()
          if w is not None and now > w:
            sel.timeout()
    except Exception, e:
      log.error("qpid.messaging: I/O thread has died: %s\n%s" % (e, format_exc()))
      dead(e, False)
      raise
    self.dead(SelectorException("qpid.messaging: I/O thread exited"), False)

  def stop(self, timeout=None):
    """Stop the selector and wait for it's thread to exit.
    Ignored for the shared default() selector, which stops when the process exits.

    """
    if self.DEFAULT == self:    # Never stop the DEFAULT Selector
      return
    self.stopped = True
    self.wakeup()
    self.thread.join(timeout)
    self.dead(SelectorException("qpid.messaging: I/O thread stopped"), False)

  def dead(self, e, forked):
    """Mark the Selector as dead if it is stopped for any reason.
    Ensure there any future calls to wait() will raise an exception.
    If the thread died because of a fork() then ensure further that
    attempting to take the connections lock also raises.
    """
    self.thread = None
    self.exception = e
    for sel in self.selectables.copy():
      try:
        # Mark the connection as failed
        sel.connection.error = e
        if forked:
          # Replace connection's locks, they may be permanently locked in the forked child.
          c = sel.connection
          c.error = e
          c._lock = BrokenLock(e)
          for ssn in c.sessions.values():
            ssn._lock = c._lock
            for l in ssn.senders + ssn.receivers:
              l._lock = c._lock
      except:
        pass
    try:
      if forked:
        self.waiter.close()       # Don't mess with the parent's FDs
      else:
        self.waiter.wakeup()      # In case somebody re-waited while we were cleaning up.
    except:
      pass

class BrokenLock(object):
  """Dummy lock-like object that raises an exception. Used in forked child to
      replace locks that may be held in the parent process."""
  def __init__(self, exception):
    self.exception = exception

  def acquire(self):
    log.error(str(self.exception))
    raise self.exception
