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
import time, errno, os, atexit, traceback
from compat import select, SelectError, set, selectable_waiter, format_exc
from threading import Thread, Lock
from logging import getLogger
from qpid.messaging import InternalError

def _stack(skip=0):
  return ("".join(traceback.format_stack()[:-(1+skip)])).strip()

class SelectorStopped(InternalError):
  def __init__(self, msg, where=None):
    InternalError.__init__(self, text=msg)
    self.where = _stack(1)

def _check(ex, skip=0):
  if ex:
    log.error("illegal use of qpid.messaging at:\n%s\n%s" % (_stack(1), ex))
    where = getattr(ex, 'where')
    if where:
      log.error("qpid.messaging was previously stopped at:\n%s\n%s" % (where, ex))
    raise ex

log = getLogger("qpid.messaging")

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
          log.warning("process forked, child must not use parent qpid.messaging")
          Selector.DEFAULT.dead(SelectorStopped("forked child using parent qpid.messaging"))
        sel = Selector()
        sel.start()
        atexit.register(sel.stop)
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
    self.exception = None

  def wakeup(self):
    _check(self.exception)
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
    _check(self.exception)
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
      log.error("qpid.messaging thread died: %s" % e)
      self.exception = SelectorStopped(str(e))
    self.exception = self.exception or self.stopped
    self.dead(self.exception or SelectorStopped("qpid.messaging thread died: reason unknown"))

  def stop(self, timeout=None):
    """Stop the selector and wait for it's thread to exit. It cannot be re-started"""
    if self.thread and not self.stopped:
      self.stopped = SelectorStopped("qpid.messaging thread has been stopped")
      self.wakeup()
      self.thread.join(timeout)

  def dead(self, e):
    """Mark the Selector as dead if it is stopped for any reason.  Ensure there any future
    attempt to use the selector or any of its connections will throw an exception.
    """
    self.exception = e
    try:
      for sel in self.selectables.copy():
        c = sel.connection
        for ssn in c.sessions.values():
          for l in ssn.senders + ssn.receivers:
            disable(l, self.exception)
          disable(ssn, self.exception)
        disable(c, self.exception)
    except Exception, e:
      log.error("error stopping qpid.messaging (%s)\n%s", self.exception, format_exc())
    try:
      self.waiter.close()
    except Exception, e:
      log.error("error stopping qpid.messaging (%s)\n%s", self.exception, format_exc())

# Disable an object to avoid hangs due to forked mutex locks or a stopped selector thread
import inspect
def disable(obj, exception):
  assert(exception)
  # Replace methods to raise exception or be a no-op 
  for m in inspect.getmembers(
      obj, predicate=lambda m: inspect.ismethod(m) and not inspect.isbuiltin(m)):
    name = m[0]
    if name in ["close", "detach", "detach_all"]: # No-ops for these
      setattr(obj, name, lambda *args, **kwargs: None)
    else:                       # Raise exception for all others
      setattr(obj, name, lambda *args, **kwargs: _check(exception, 1))
