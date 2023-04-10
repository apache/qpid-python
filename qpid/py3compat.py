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

"""Python 3 compatibility helpers"""

import sys

PY2 = sys.version_info.major == 2
PY3 = sys.version_info.major == 3


def cmp(a, b):
    return (a > b) - (a < b)


_convert = {
    '__eq__': lambda self, other: self.__cmp__(other) == 0,
    '__ne__': lambda self, other: self.__cmp__(other) != 0,
    '__lt__': lambda self, other: self.__cmp__(other) < 0,
    '__le__': lambda self, other: self.__cmp__(other) <= 0,
    '__gt__': lambda self, other: self.__cmp__(other) > 0,
    '__ge__': lambda self, other: self.__cmp__(other) >= 0,
}


def PY3__cmp__(cls):
    """Class decorator that fills in missing ordering methods when Python2's __cmp__ is provided."""
    if not hasattr(cls, '__cmp__'):
        raise ValueError('must define the __cmp__ Python2 operation')
    if sys.version_info < (3, 0, 0):
        return cls
    for op, opfunc in _convert.items():
        # Overwrite `raise NotImplemented` comparisons inherited from object
        if getattr(cls, op, None) is getattr(object, op, None):
            setattr(cls, op, opfunc)
    return cls
