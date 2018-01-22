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

"""X5 HTML5 creative bundle."""

import base64
import cgi
import collections
import logging
import mimetypes
import os
import string
import zipfile

import env
from lxml import etree
import x5_converters
import x5_exceptions

from google.appengine.ext import blobstore


logger = logging.getLogger('x5.bundle')


_SNIPPET_MIMETYPES = ('text/html',)
_SCRIPT_MIMETYPES = ('application/javascript', 'application/x-javascript')
_INLINED_MIMETYPES = ('text/css', 'text/html', 'text/plain') + _SCRIPT_MIMETYPES
_UNSUPPORTED_MIMETYPES = ('image/svg+xml',)


def html_escape(s):
  s = cgi.escape(s, quote=1)
  return s


class X5CreativeResource(object):
  """Base class for HTML5 creative parts."""

  def __init__(self, obj_id, filename, filesize, mimetype):
    self.id = obj_id
    self.name = filename
    self.size = filesize
    self.content = None
    self._parsed_content = None
    self._converted = False
    self.assets = []
    self.mimetype = mimetype

  @property
  def root(self):
    return os.path.dirname(self.name)

  @property
  def basename(self):
    return os.path.basename(self.name)

  @property
  def parsed_content(self):
    return self._parsed_content

  @parsed_content.setter
  def parsed_content(self, value):
    if self.mimetype in _SCRIPT_MIMETYPES + _SNIPPET_MIMETYPES:
      value = x5_converters.escape_modulo_op(value)
    self._parsed_content = value
    self._converted = True

  @property
  def converted(self):
    return self._converted

  def as_dict(self, escaped=False):
    """Returns the resource as dictionary."""
    d = dict((k, getattr(self, k)) for k in (
        'id', 'name', 'size', 'content', 'parsed_content', 'assets',
        'mimetype', 'root', 'basename'
    ))
    if escaped:
      # TODO(ludomagno): move escaping in main.MetadataHandler
      d['name'] = html_escape(d['name'])
      d['basename'] = html_escape(d['basename'])
      d['root'] = html_escape(d['root'])
      d['assets'] = [html_escape(a) for a in d['assets']]
    return d

  def name_relative_to(self, root):
    if not root:
      return self.name
    if not self.name.startswith(root):
      raise ValueError("Path '%s' does not match path '%s'" % (self.name, root))
    l = len(root) if root.endswith('/') else len(root)+1
    return self.name[l:]

# TODO(ludomagno): verify encoding is utf-8 when reading content?


class X5Snippet(X5CreativeResource):
  """HTML5 creative snippet."""

  def __init__(self, obj_id, filename, filesize, mimetype, fileobj):
    super(X5Snippet, self).__init__(obj_id, filename, filesize, mimetype)
    self.content = fileobj.read()
    self.assets = []

  def as_dict(self, escaped=False):
    d = super(X5Snippet, self).as_dict(escaped)
    d['x5type'] = self.x5type
    return d

  def as_snippet(self):
    """Returns snippet as HTML fragment for consumption by the DFP API."""
    # pylint: disable=no-member
    if not self.parsed_content:
      return ''
    content = self.parsed_content.decode('utf-8', errors='ignore')
    tree = etree.HTML(content)
    buf = []
    buf.append((
        '<!-- Please make sure you review the creative and '
        'that it contains the clicktracking macro -->'
    ))
    head = tree.find('head')
    if head is not None:
      for el in head:
        # TODO(ludomagno): account for XML namespaces (consider tag from '}' ?)
        if callable(el.tag) or el.tag.lower() in ('meta', 'title'):
          continue
        buf.append(etree.tostring(el, encoding='utf-8', method='html'))
    body = tree.find('body')
    if body is None:
      return content
    body = etree.tostring(body, encoding='utf-8', method='html')
    buf.append(body[body.find('>')+1:body.rfind('<')])
    return ''.join(buf).decode('utf-8')


class X5Asset(X5CreativeResource):
  """HTML5 creative asset."""

  def __init__(self, obj_id, filename, filesize, mimetype, fileobj):
    super(X5Asset, self).__init__(obj_id, filename, filesize, mimetype)
    if self.inlineable:
      self.content = fileobj.read()
      self.assets = []

  def as_dict(self, escaped=False):
    d = super(X5Asset, self).as_dict(escaped)
    d['inlineable'] = self.inlineable
    d['inlined'] = self.inlined
    d['over_limit'] = self.over_limit
    d['unsupported'] = self.unsupported
    return d

  def as_creative_asset(self, transform_id, fileobj):
    """Returns asset in the format expected by the DFP API."""
    if self.over_limit or self.unsupported:
      content = chr(0)
    elif self.inlineable:
      content = self.parsed_content or self.content
      if isinstance(content, unicode):
        content = content.encode('utf-8')
    else:
      content = fileobj.read()
    return {
        'xsi_type': 'CustomCreativeAsset',
        'macroName': self.id,
        # The API complains if we don't send unicode here.
        'asset': {
            'assetByteArray': base64.b64encode(content).decode('utf-8'),
            'fileName': '%s-%s%s' % (
                self.id, transform_id, os.path.splitext(self.name)[1]
            )
        }
    }

  @property
  def over_limit(self):
    return self.size > env.ASSET_SIZE_LIMIT

  @property
  def unsupported(self):
    return self.mimetype is None or self.mimetype in _UNSUPPORTED_MIMETYPES

  @property
  def inlined(self):
    return bool(self.inlineable and self.assets)

  @property
  def inlineable(self):
    return self.mimetype in _INLINED_MIMETYPES


class X5Bundle(object):
  """HTML5 zipped creative bundle."""

  @classmethod
  def _open_zip(cls, transform_id, stream_reader):
    try:
      return zipfile.ZipFile(stream_reader)
    except (AttributeError, blobstore.Error), e:
      # Having no blob raises AttributeError.
      raise x5_exceptions.X5BundleError(
          'Error opening blob for bundle key %s: %s', transform_id, e
      )
    except (zipfile.BadZipfile, zipfile.LargeZipFile), e:
      raise x5_exceptions.X5BundleError(
          'Error opening zip from bundle key %s: %s' % (transform_id, e)
      )

  @classmethod
  def zip_factory(cls, transform_id, stream_reader):
    """Returns an X5 bundle instance from a zipped bundle."""
    zipped_bundle = cls._open_zip(transform_id, stream_reader)
    bundle = cls(transform_id)
    for info in zipped_bundle.infolist():
      if info.filename.endswith('/'):
        continue
      if '__MACOSX/' in info.filename:
        continue
      basename = os.path.basename(info.filename)
      if basename.startswith('.') or basename in ('Thumbs.db',):
        continue
      if info.filename.endswith('.DS_Store'):
        continue
      try:
        with zipped_bundle.open(info) as fileobj:
          bundle.add_member(info.filename, info.file_size, fileobj)
      except zipfile.BadZipfile, e:
        raise x5_exceptions.X5BundleError(
            'Error reading zip entry %s for bundle key %s: %s',
            info.filename, transform_id, e
        )
    if not bundle.snippets:
      raise x5_exceptions.X5BundleError('No snippets found.')
    return bundle

  def __init__(self, transform_id):
    self.transform_id = transform_id
    self.snippets = {}
    self.assets = {}
    self._macro_names = {}

  def get_creative_part(self, transform_id, stream_reader, snippet_name):
    """Get snippet and assets in the format expected by the DFP API."""
    if isinstance(snippet_name, unicode):
      snippet_name = snippet_name.encode('utf-8', errors='ignore')
    try:
      snippet = self.snippets[snippet_name]
    except KeyError:
      raise x5_exceptions.X5BundleError(
          'Invalid snippet name or bundle not populated'
      )
    zipped_bundle = self._open_zip(transform_id, stream_reader)
    # TODO(ludomagno): inject the assets table in the snippet
    creative_part = {
        'customCreativeAssets': [],
        'htmlSnippet': snippet.as_snippet()
    }
    for asset_name in set(snippet.assets):
      # Don't skip assets that are over quota as they are referenced in macros.
      asset = self.assets[asset_name]
      with zipped_bundle.open(asset_name) as fileobj:
        creative_part['customCreativeAssets'].append(asset.as_creative_asset(
            transform_id, fileobj
        ))
    return creative_part

  def add_member(self, filename, filesize, fileobj):
    """Add a file to this bundle."""
    if isinstance(filename, unicode):
      filename = filename.encode('utf-8', errors='ignore')
    try:
      mimetype, _ = mimetypes.guess_type(filename)
    except TypeError:
      mimetype = ''
    ext = (os.path.splitext(filename)[1] or '.noext').upper()[1:]
    if ext == 'NOEXT':
      return
    self._macro_names[ext] = self._macro_names.get(ext, 0) + 1
    obj_id = '%s%s' % (ext, self._macro_names[ext])
    if mimetype in _SNIPPET_MIMETYPES:
      obj = X5Snippet(obj_id, filename, filesize, mimetype, fileobj)
      self.snippets[obj.name] = obj
    else:
      obj = X5Asset(obj_id, filename, filesize, mimetype, fileobj)
      self.assets[obj.name] = obj

  def assets_relative_to(self, root):
    """Return assets dict with keys relative to root."""
    if isinstance(root, basestring):
      roots = [root]
    elif isinstance(root, X5CreativeResource):
      roots = [root.root]
    elif isinstance(root, collections.Iterable):
      roots = root
    assets = {}
    for asset in self.assets.values():
      for root in roots:
        try:
          name = asset.name_relative_to(root)
        except ValueError:
          continue
        assets[name] = asset
        break
    return assets

  def transform(self):
    """Apply the workflow to convert the bundle to a DFP creative."""
    if not self.assets:
      raise x5_exceptions.X5BundleError('No assets in bundle.')
    for snippet in self.snippets.values():
      for converter in x5_converters.X5_CONVERTERS:
        if not converter.match(snippet):
          continue
        try:
          converter(self).convert(snippet)
        except x5_exceptions.X5ConverterError, e:
          logger.exception('Conversion error')
          raise x5_exceptions.X5BundleError(
              'Error converting %s: %s', self.transform_id, e
          )
        else:
          snippet.x5type = converter.X5_TYPE
          break

  def _assets_table(self, snippet_name):
    """Returns an ASCII table of assets mappings."""
    table = ['snippet: %s' % snippet_name, '']
    snippet = self.snippets[snippet_name]
    assets = [self.assets[a] for a in set(snippet.assets)]
    fields = (
        'name', 'id', 'size', 'mimetype', 'inlined', 'over_limit', 'unsupported'
    )
    pads = dict((f, len(f)) for f in fields)
    for a in assets:
      for k, v in pads.items():
        pad = len(str(getattr(a, k)))
        pads[k] = max((v, pad))
    table.append(' '.join(str.ljust(f, pads[f]) for f in fields))
    table.append(' '.join('-' * pads[f] for f in fields))
    for a in assets:
      row = []
      for f in fields:
        v = getattr(a, f) or ''
        op = str.ljust if isinstance(v, basestring) else str.rjust
        row.append(op(str(v), pads[f]))
      table.append(' '.join(row))
    return '\n'.join(table)
