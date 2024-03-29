#!/usr/bin/env python

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
Utility code to translate between python objects and AMQP encoded data
fields.

The unit test for this module is located in tests/codec.py
"""

from __future__ import absolute_import
import re, qpid, os
from . import spec08
from io import BytesIO
from struct import *
from .reference import ReferenceId
from logging import getLogger

try:
  unicode
except NameError:
  unicode = str

try:
  long
except NameError:
  long = int

log = getLogger("qpid.codec")

class EOF(Exception):
  pass

# This code appears to be dead
TYPE_ALIASES = {
  "long_string": "longstr",
  "unsigned_int": "long"
  }

class Codec:

  """
  class that handles encoding/decoding of AMQP primitives
  """

  def __init__(self, stream, spec):
    """
    initializing the stream/fields used
    """
    self.stream = stream
    self.spec = spec
    self.nwrote = 0
    self.nread = 0
    self.incoming_bits = []
    self.outgoing_bits = []

    # Before 0-91, the AMQP's set of types did not include the boolean type. However,
    # the 0-8 and 0-9 Java client uses this type so we encode/decode it too. However, this
    # can be turned off by setting the followng environment value.
    if "QPID_CODEC_DISABLE_0_91_BOOLEAN" in os.environ:
      self.understand_boolean = False
    else:
      self.understand_boolean = True

    log.debug("AMQP 0-91 boolean supported : %r", self.understand_boolean)

    self.types = {}
    self.codes = {}
    self.integertypes = [int, long]
    self.encodings = {
      float: "double", # python uses 64bit floats, send them as doubles
      bytes: "longstr",
      unicode: "longstr",
      None.__class__:"void",
      list: "sequence",
      tuple: "sequence",
      dict: "table"
      }

    if self.understand_boolean:
      self.encodings[bool] = "boolean"

    for constant in self.spec.constants:
      # This code appears to be dead
      if constant.klass == "field-table-type":
        type = constant.name.replace("field_table_", "")
        self.typecode(constant.id, TYPE_ALIASES.get(type, type))

    if not self.types:
      # long-string 'S'
      self.typecode(ord('S'), "longstr")
      # void 'V'
      self.typecode(ord('V'), "void")
      # long-int 'I' (32bit signed)
      self.typecode(ord('I'), "signed_int")
      # long-long-int 'l' (64bit signed)
      # This is a long standing pre-0-91-spec type used by the Java
      # client, 0-9-1 says it should be unsigned or use 'L')
      self.typecode(ord('l'), "signed_long")
      # double 'd'
      self.typecode(ord('d'), "double")
      # float 'f'
      self.typecode(ord('f'), "float")

      if self.understand_boolean:
        self.typecode(ord('t'), "boolean")

      ## The following are supported for decoding only ##

      # short-short-uint 'b' (8bit signed)
      self.types[ord('b')] = "signed_octet"
      # short-int 's' (16bit signed)
      # This is a long standing pre-0-91-spec type code used by the Java
      # client to send shorts, it should really be a short-string, or for 0-9-1 use 'U'
      self.types[ord('s')] = "signed_short"

  def typecode(self, code, type):
    self.types[code] = type
    self.codes[type] = code

  def resolve(self, klass, value):
    if(klass in self.integertypes):
      if (value >= -2147483648 and value <= 2147483647):
        return "signed_int"
      elif (value >= -9223372036854775808 and value <= 9223372036854775807):
        return "signed_long"
      else:
        raise ValueError('Integer value is outwith the supported 64bit signed range')
    if klass in self.encodings:
      return self.encodings[klass]
    for base in klass.__bases__:
      result = self.resolve(base, value)
      if result != None:
        return result

  def read(self, n):
    """
    reads in 'n' bytes from the stream. Can raise EOF exception
    """
    self.clearbits()
    data = self.stream.read(n)
    if n > 0 and len(data) == 0:
      raise EOF()
    self.nread += len(data)
    return data

  def write(self, s):
    """
    writes data 's' to the stream
    """
    self.flushbits()
    self.stream.write(s)
    self.nwrote += len(s)

  def flush(self):
    """
    flushes the bits and data present in the stream
    """
    self.flushbits()
    self.stream.flush()

  def flushbits(self):
    """
    flushes the bits(compressed into octets) onto the stream
    """
    if len(self.outgoing_bits) > 0:
      bytes = []
      index = 0
      for b in self.outgoing_bits:
        if index == 0: bytes.append(0)
        if b: bytes[-1] |= 1 << index
        index = (index + 1) % 8
      del self.outgoing_bits[:]
      for byte in bytes:
        self.encode_octet(byte)

  def clearbits(self):
    if self.incoming_bits:
      self.incoming_bits = []

  def pack(self, fmt, *args):
    """
    packs the data 'args' as per the format 'fmt' and writes it to the stream
    """
    self.write(pack(fmt, *args))

  def unpack(self, fmt):
    """
    reads data from the stream and unpacks it as per the format 'fmt'
    """
    size = calcsize(fmt)
    data = self.read(size)
    values = unpack(fmt, data)
    if len(values) == 1:
      return values[0]
    else:
      return values

  def encode(self, type, value):
    """
    calls the appropriate encode function e.g. encode_octet, encode_short etc.
    """
    if isinstance(type, spec08.Struct):
      self.encode_struct(type, value)
    else:
      getattr(self, "encode_" + type)(value)

  def decode(self, type):
    """
    calls the appropriate decode function e.g. decode_octet, decode_short etc.
    """
    if isinstance(type, spec08.Struct):
      return self.decode_struct(type)
    else:
      log.debug("Decoding using method: decode_" + type)
      return getattr(self, "decode_" + type)()

  def encode_bit(self, o):
    """
    encodes a bit
    """
    if o:
      self.outgoing_bits.append(True)
    else:
      self.outgoing_bits.append(False)

  def decode_bit(self):
    """
    decodes a bit
    """
    if len(self.incoming_bits) == 0:
      bits = self.decode_octet()
      for i in range(8):
        self.incoming_bits.append(bits >> i & 1 != 0)
    return self.incoming_bits.pop(0)

  def encode_octet(self, o):
    """
    encodes an UNSIGNED octet (8 bits) data 'o' in network byte order
    """

    # octet's valid range is [0,255]
    if (o < 0 or o > 255):
        raise ValueError('Valid range of octet is [0,255]')

    self.pack("!B", int(o))

  def decode_octet(self):
    """
    decodes an UNSIGNED octet (8 bits) encoded in network byte order
    """
    return self.unpack("!B")

  def decode_signed_octet(self):
    """
    decodes a signed octet (8 bits) encoded in network byte order
    """
    return self.unpack("!b")

  def encode_short(self, o):
    """
    encodes an UNSIGNED short (16 bits) data 'o' in network byte order
    AMQP 0-9-1 type: short-uint
    """

    # short int's valid range is [0,65535]
    if (o < 0 or o > 65535):
        raise ValueError('Valid range of short int is [0,65535]: %s' % o)

    self.pack("!H", int(o))

  def decode_short(self):
    """
    decodes an UNSIGNED short (16 bits) in network byte order
    AMQP 0-9-1 type: short-uint
    """
    return self.unpack("!H")

  def decode_signed_short(self):
    """
    decodes a signed short (16 bits) in network byte order
    AMQP 0-9-1 type: short-int
    """
    return self.unpack("!h")

  def encode_long(self, o):
    """
    encodes an UNSIGNED long (32 bits) data 'o' in network byte order
    AMQP 0-9-1 type: long-uint
    """

    # we need to check both bounds because on 64 bit platforms
    # struct.pack won't raise an error if o is too large
    if (o < 0 or o > 4294967295):
      raise ValueError('Valid range of long int is [0,4294967295]')

    self.pack("!L", int(o))

  def decode_long(self):
    """
    decodes an UNSIGNED long (32 bits) in network byte order
    AMQP 0-9-1 type: long-uint
    """
    return self.unpack("!L")

  def encode_signed_long(self, o):
    """
    encodes a signed long (64 bits) in network byte order
    AMQP 0-9-1 type: long-long-int
    """
    self.pack("!q", o)

  def decode_signed_long(self):
    """
    decodes a signed long (64 bits) in network byte order
    AMQP 0-9-1 type: long-long-int
    """
    return self.unpack("!q")

  def encode_signed_int(self, o):
    """
    encodes a signed int (32 bits) in network byte order
    AMQP 0-9-1 type: long-int
    """
    self.pack("!l", o)

  def decode_signed_int(self):
    """
    decodes a signed int (32 bits) in network byte order
    AMQP 0-9-1 type: long-int
    """
    return self.unpack("!l")

  def encode_longlong(self, o):
    """
    encodes an UNSIGNED long long (64 bits) data 'o' in network byte order
    AMQP 0-9-1 type: long-long-uint
    """
    self.pack("!Q", long(o))

  def decode_longlong(self):
    """
    decodes an UNSIGNED long long (64 bits) in network byte order
    AMQP 0-9-1 type: long-long-uint
    """
    return self.unpack("!Q")

  def encode_float(self, o):
    self.pack("!f", o)

  def decode_float(self):
    return self.unpack("!f")

  def encode_double(self, o):
    self.pack("!d", o)

  def decode_double(self):
    return self.unpack("!d")

  def encode_bin128(self, b):
    for idx in range (0,16):
      self.pack("!B", ord (b[idx]))

  def decode_bin128(self):
    result = ""
    for idx in range (0,16):
      result = result + chr (self.unpack("!B"))
    return result

  def encode_raw(self, len, b):
    for idx in range (0,len):
      self.pack("!B", b[idx])

  def decode_raw(self, len):
    result = ""
    for idx in range (0,len):
      result = result + chr (self.unpack("!B"))
    return result

  def enc_str(self, fmt, s):
    """
    encodes a string 's' in network byte order as per format 'fmt'
    """
    if not isinstance(s, bytes):
      s = s.encode()
    size = len(s)
    self.pack(fmt, size)
    self.write(s)

  def dec_str(self, fmt):
    """
    decodes a string in network byte order as per format 'fmt'
    """
    size = self.unpack(fmt)
    return self.read(size)

  def encode_shortstr(self, s):
    """
    encodes a short string 's' in network byte order
    """
    if not isinstance(s, bytes):
      s.encode()

    # short strings are limited to 255 octets
    if len(s) > 255:
        raise ValueError('Short strings are limited to 255 octets')

    self.enc_str("!B", s)

  def decode_shortstr(self):
    """
    decodes a short string in network byte order
    """
    return self.dec_str("!B")

  def encode_longstr(self, s):
    """
    encodes a long string 's' in network byte order
    """
    if isinstance(s, dict):
      self.encode_table(s)
    else:
      self.enc_str("!L", s)

  def decode_longstr(self):
    """
    decodes a long string 's' in network byte order
    """
    return self.dec_str("!L")

  def encode_table(self, tbl):
    """
    encodes a table data structure in network byte order
    """
    enc = BytesIO()
    codec = Codec(enc, self.spec)
    if tbl:
      for key, value in tbl.items():
        if self.spec.major == 8 and self.spec.minor == 0 and len(key) > 128:
          raise ValueError("field table key too long: '%s'" % key)
        type = self.resolve(value.__class__, value)
        if type == None:
          raise ValueError("no encoding for: " + str(value.__class__))
        codec.encode_shortstr(key)
        codec.encode_octet(self.codes[type])
        codec.encode(type, value)
    s = enc.getvalue()
    self.encode_long(len(s))
    self.write(s)

  def decode_table(self):
    """
    decodes a table data structure in network byte order
    """
    size = self.decode_long()
    start = self.nread
    result = {}
    while self.nread - start < size:
      key = self.decode_shortstr()
      log.debug("Field table entry key: %r", key)
      code = self.decode_octet()
      log.debug("Field table entry type code: %r", code)
      if code in self.types:
        value = self.decode(self.types[code])
      else:
        w = width(code)
        if fixed(code):
          value = self.read(w)
        else:
          value = self.read(self.dec_num(w))
      result[key] = value
      log.debug("Field table entry value: %r", value)
    return result

  def encode_timestamp(self, t):
    """
    encodes a timestamp data structure in network byte order
    """
    self.encode_longlong(t)

  def decode_timestamp(self):
    """
    decodes a timestamp data structure in network byte order
    """
    return self.decode_longlong()

  def encode_content(self, s):
    """
    encodes a content data structure in network byte order

    content can be passed as a string in which case it is assumed to
    be inline data, or as an instance of ReferenceId indicating it is
    a reference id
    """
    if isinstance(s, ReferenceId):
      self.encode_octet(1)
      self.encode_longstr(s.id)
    else:
      self.encode_octet(0)
      self.encode_longstr(s)

  def decode_content(self):
    """
    decodes a content data structure in network byte order

    return a string for inline data and a ReferenceId instance for
    references
    """
    type = self.decode_octet()
    if type == 0:
      return self.decode_longstr()
    else:
      return ReferenceId(self.decode_longstr())

  # new domains for 0-10:

  def encode_rfc1982_long(self, s):
    self.encode_long(s)

  def decode_rfc1982_long(self):
    return self.decode_long()

  def encode_rfc1982_long_set(self, s):
    self.encode_short(len(s) * 4)
    for i in s:
      self.encode_long(i)

  def decode_rfc1982_long_set(self):
    count = self.decode_short() / 4
    set = []
    for i in range(0, count):
      set.append(self.decode_long())
    return set

  def encode_uuid(self, s):
    self.pack("16s", s)

  def decode_uuid(self):
    return self.unpack("16s")

  def encode_void(self,o):
    #NO-OP, value is implicit in the type.
    return

  def decode_void(self):
    return None

  def enc_num(self, width, n):
    if width == 1:
      self.encode_octet(n)
    elif width == 2:
      self.encode_short(n)
    elif width == 3:
      self.encode_long(n)
    else:
      raise ValueError("invalid width: %s" % width)

  def dec_num(self, width):
    if width == 1:
      return self.decode_octet()
    elif width == 2:
      return self.decode_short()
    elif width == 4:
      return self.decode_long()
    else:
      raise ValueError("invalid width: %s" % width)

  def encode_struct(self, type, s):
    if type.size:
      enc = BytesIO()
      codec = Codec(enc, self.spec)
      codec.encode_struct_body(type, s)
      codec.flush()
      body = enc.getvalue()
      self.enc_num(type.size, len(body))
      self.write(body)
    else:
      self.encode_struct_body(type, s)

  def decode_struct(self, type):
    if type.size:
      size = self.dec_num(type.size)
      if size == 0:
        return None
    return self.decode_struct_body(type)

  def encode_struct_body(self, type, s):
    reserved = 8*type.pack - len(type.fields)
    assert reserved >= 0

    for f in type.fields:
      if s == None:
        self.encode_bit(False)
      elif f.type == "bit":
        self.encode_bit(s.get(f.name))
      else:
        self.encode_bit(s.has(f.name))

    for i in range(reserved):
      self.encode_bit(False)

    for f in type.fields:
      if f.type != "bit" and s != None and s.has(f.name):
        self.encode(f.type, s.get(f.name))

    self.flush()

  def decode_struct_body(self, type):
    reserved = 8*type.pack - len(type.fields)
    assert reserved >= 0

    s = qpid.Struct(type)

    for f in type.fields:
      if f.type == "bit":
        s.set(f.name, self.decode_bit())
      elif self.decode_bit():
        s.set(f.name, None)

    for i in range(reserved):
      if self.decode_bit():
        raise ValueError("expecting reserved flag")

    for f in type.fields:
      if f.type != "bit" and s.has(f.name):
        s.set(f.name, self.decode(f.type))

    self.clearbits()

    return s

  def encode_long_struct(self, s):
    enc = BytesIO()
    codec = Codec(enc, self.spec)
    type = s.type
    codec.encode_short(type.type)
    codec.encode_struct_body(type, s)
    self.encode_longstr(enc.getvalue())

  def decode_long_struct(self):
    codec = Codec(BytesIO(self.decode_longstr()), self.spec)
    type = self.spec.structs[codec.decode_short()]
    return codec.decode_struct_body(type)

  def decode_array(self):
    size = self.decode_long()
    code = self.decode_octet()
    count = self.decode_long()
    result = []
    for i in range(0, count):
      if code in self.types:
        value = self.decode(self.types[code])
      else:
        w = width(code)
        if fixed(code):
          value = self.read(w)
        else:
          value = self.read(self.dec_num(w))
      result.append(value)
    return result

  def encode_boolean(self, s):
    if (s):
      self.pack("!c", b"\x01")
    else:
      self.pack("!c", b"\x00")

  def decode_boolean(self):
    b = self.unpack("!c")
    if b == b"\x00":
      return False
    else:
      # AMQP spec says anything else is True
      return True



def fixed(code):
  return (code >> 6) != 2

def width(code):
  # decimal
  if code >= 192:
    decsel = (code >> 4) & 3
    if decsel == 0:
      return 5
    elif decsel == 1:
      return 9
    elif decsel == 3:
      return 0
    else:
      raise ValueError(code)
  # variable width
  elif code < 192 and code >= 128:
    lenlen = (code >> 4) & 3
    if lenlen == 3: raise ValueError(code)
    return 2 ** lenlen
  # fixed width
  else:
    return (code >> 4) & 7
