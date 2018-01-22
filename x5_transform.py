#    Copyright 2018 Google Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        https://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

"""X5 transform request."""

import base64
import datetime
import hashlib
import logging
import time
import urlparse

import x5_bundle
import x5_exceptions

from lxml import etree

from google.appengine.ext import blobstore
from google.appengine.ext import ndb


logger = logging.getLogger('x5.transform')


def tag_strip(s):
  return etree.tostring(
      etree.HTML(s), encoding='utf-8', method='text'
  ).decode('utf-8')


class X5Transform(ndb.Model):
  """Ndb instance for X5 transform request."""

  x5_id = ndb.StringProperty(required=True, indexed=True)
  blob_key = ndb.BlobKeyProperty(required=True, indexed=True)
  network_code = ndb.StringProperty(required=True, indexed=True)
  filename = ndb.StringProperty(required=False, indexed=True)
  created = ndb.DateTimeProperty(auto_now_add=True)
  # Stores the snippet filename as found in the zip manifest.
  snippet = ndb.StringProperty(required=False)
  creative_id = ndb.IntegerProperty(required=False, indexed=True)
  # Stores the full preview URL, compressed as it's ~1k, limit is 1.5k.
  creative_preview = ndb.StringProperty(
      required=False, indexed=False, compressed=True
  )
  modified = ndb.DateTimeProperty(required=False, auto_now=True)

  def _pre_put_hook(self):
    if self.network_code is None:
      raise x5_exceptions.X5TransformError(
          'Cannot generate id, empty network code'
      )
    if self.created is None:
      self.created = datetime.datetime.utcnow()
    if self.x5_id is None:
      self.x5_id = self._generate_x5_id()

  def _generate_x5_id(self):
    if not self.key or not self.key.parent():
      raise x5_exceptions.X5TransformError('Cannot generate id, no parent')
    return base64.b64encode(hashlib.md5('%s%s%s' % (
        self.network_code,
        self.key.parent().id(),
        time.mktime(self.created.timetuple())
    )).digest(), '_-')[:-2]

  @classmethod
  def parent_key(cls, user_id):
    return ndb.Key('X5User', user_id)

  @classmethod
  def user_transforms(cls, user_id):
    return cls.query(ancestor=cls.parent_key(user_id)).order(-cls.created)

  @property
  def _reader(self):
    if not hasattr(self, '_blobreader'):
      self._blobreader = blobstore.BlobReader(self.blob_key)
    self._blobreader.seek(0)
    return self._blobreader

  @property
  def snippets(self):
    return self.bundle.snippets

  @property
  def assets(self):
    return self.bundle.assets

  @property
  def bundle(self):
    if not hasattr(self, '_x5bundle'):
      try:
        x5bundle = x5_bundle.X5Bundle.zip_factory(self.x5_id, self._reader)
        x5bundle.transform()
      except blobstore.Error as e:
        raise x5_exceptions.X5TransformError('Cannot open blobstore blob: %s' %
                                             e.args[0])
      except x5_exceptions.X5BundleError as e:
        raise x5_exceptions.X5TransformError('Cannot transform the blob: %s' %
                                             e.args[0])
      self._x5bundle = x5bundle
    return self._x5bundle

  def get_creative(self, snippet_name, advertiser_id, url, size,
                   creative_name=None, interstitial=0):
    """Returns the creative in the format expected by the API.

    Args:
      snippet_name: name of the snippet to use for the creative, as present
          in the zip manifest.
      advertiser_id: advertiser id under which the creative will be registered.
      url: clickstring URL for the creative.
      size: creative size, in the 'widthxheight' format.
      creative_name: name to assign to creative, auto-generated if not set.
      interstitial: flag this as interstitial (currently unused).

    Returns:
      A dictionary with the creative fields to be passed to the API.
    """
    try:
      int(advertiser_id)
    except (TypeError, ValueError):
      raise x5_exceptions.X5TransformError(
          "Invalid advertiser id '%s'" % advertiser_id
      )
    try:
      width, height = [int(i) for i in size.split('x')]
    except (TypeError, ValueError):
      raise x5_exceptions.X5TransformError("Invalid size '%s'" % size)
    try:
      url_tokens = urlparse.urlsplit(url)
      if not url_tokens.scheme or not url_tokens.netloc:
        raise x5_exceptions.X5TransformError("Incorrect URL '%s'" % url)
    except (TypeError, ValueError):
      raise x5_exceptions.X5TransformError("Invalid URL '%s'" % url)

    try:
      creative = self.bundle.get_creative_part(
          self.x5_id, self._reader, snippet_name
      )
    except x5_exceptions.X5BundleError as e:
      raise x5_exceptions.X5TransformError(e.args[0])

    if creative_name:
      creative_name = tag_strip(creative_name)
    else:
      creative_name = 'X5 %s %s' % (self.filename, self.x5_id)

    creative.update({
        'xsi_type': 'CustomCreative',
        'name': creative_name,
        'advertiserId': advertiser_id,
        'size': {'width': width, 'height': height},
        'destinationUrl': url
    })

    return creative
