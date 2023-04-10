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

__SELF__ = object()

class Constant:

  def __init__(self, name, value=__SELF__):
    self.name = name
    if value is __SELF__:
      self.value = self
    else:
      self.value = value

  def __repr__(self):
    return self.name

AMQP_PORT = 5672
AMQPS_PORT = 5671

UNLIMITED = Constant("UNLIMITED", 0xFFFFFFFF)

REJECTED = Constant("REJECTED")
RELEASED = Constant("RELEASED")
