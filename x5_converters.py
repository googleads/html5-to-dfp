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

"""X5 converters for different HTML5 creative types."""

import collections
import functools
import logging
import os
import re
import urllib

import x5_exceptions
import x5_utils


logger = logging.getLogger('x5.converters')


_ESCAPE_MODULO_OP = re.compile(r'([^%])%([acghinstu])')


def escape_modulo_op(script_block):
  """Add a space after the modulo operator so it's not treated as a macro."""
  return _ESCAPE_MODULO_OP.sub(r'\1% \2', script_block)


class X5ConverterDefault(object):
  """Default converter for general HTML5 bundles."""

  X5_TYPE = 'default'

  # pylint: disable=unused-argument

  @classmethod
  def match(cls, snippet):
    """Checks if a bundle matches this type."""
    return True

  # pylint: enable=unused-argument

  def __init__(self, bundle):
    self.bundle = bundle

  def convert(self, snippet, append_assets_to=None, template=None):
    return self._convert_default(snippet, append_assets_to, template)

  def _convert_default(self, snippet, append_assets_to=None, template=None):
    """Converts the snippet and its assets in place."""
    assets = self.bundle.assets_relative_to(snippet.root)
    regexp = x5_utils.tokens_regexp_quoted(assets.keys())
    match_func = x5_utils.match_function(snippet, assets, template=template)
    snippet.parsed_content = regexp.sub(match_func, snippet.content)
    if append_assets_to:
      append_assets_to.assets += snippet.assets
    else:
      # Find a way to keep snippet if we want to recurse.
      for asset_name in snippet.assets:
        asset = self.bundle.assets[asset_name]
        if not asset.inlineable or asset.converted:
          continue
        self._convert_default(asset, append_assets_to=snippet)


class X5ConverterEdge(X5ConverterDefault):
  """Converter for Adobe Edge bundles."""

  X5_TYPE = 'edge'

  _edge_runtime = collections.namedtuple('EdgeRuntime', 'src name version')

  _MATCH_REGEXP = re.compile((
      '(?:'
      r'(edge\.[0-9]\.[0-9]\.[0-9]\.min\.js)|'
      '(\\<\\!\\-\\-Adobe\\ Edge\\ Runtime\\-\\-\\>)|'
      r'(AdobeEdge\.loadComposition)|'
      '(\\<\\!\\-\\-Adobe\\ Edge\\ Runtime\\ End\\-\\-\\>)'
      ')'
  ))
  _RUNTIME_REGEXP = re.compile((
      r'<script\s[^>]*src="'
      r'(?P<src>[^"]*(?P<name>edge\.(?P<version>[0-9\.]+)\.min\.js))'
      r'"[^>]*>'
  ))
  _RUNTIME_URL = (
      'https://animate.adobe.com/runtime/'
      '%(version)s/edge.%(version)s.min.js'
  )
  _JS_REGEXP = re.compile((
      r"(?P<pre>AdobeEdge.loadComposition\(')"
      r"(?P<name>[^']+)"
      r"(?P<post>', '[A-Za-z0-9_-]+', \{)"
  ))
  _PATHS_REGEXP = re.compile(r"\b(im|aud|vid|js)='([^']*?)/?'")
  _CLICKTAGS = [
      'var clickTag="%%CLICK_URL_UNESC%%" + "%%DEST_URL_ESC%%";',
      'var clickTarget="_blank";'
  ]
  _WINDOWOPEN_REGEXP = re.compile(
      r'''window\.open\(['"][^'"]*['"]((?:,[^\)]+)?)\)'''
  )

  @staticmethod
  def _edge_js_match_regexp(runtime, assets):
    """Returns a regexp that matches asset names with quoting context."""
    return x5_utils.tokens_regexp_quoted([
        k for k in assets.keys() if not k.endswith(runtime)
    ], fmt=r'.{2}%s.{2}')

  @staticmethod
  def _edge_js_match_function(snippet, assets, match):
    """Returns a match function to replace asset names with x5 variables."""
    name = match.group(1)
    if '%' in name:
      name = urllib.unquote(name)
    try:
      asset = assets[name[2:-2]]
    except KeyError:
      return match.group(1)
    snippet.assets.append(asset.name)
    if name.startswith(r'\"') or name.startswith(r"\'"):
      # From this in edge js: '<a href=\"asset.name\">'
      # To this:  '<a href=[\"' + ]__x5__.macro_ID[ + '\"]>'
      return "' + __x5__.macro_%s + '" % asset.id
    elif name[1] in ('"', "'"):
      # From this edge js: var g23=['"]970x90.jpg['"],
      # To this: var g23=__x5__.macro_ID,
      return '%s__x5__.macro_%s%s' % (name[0], asset.id, name[-1])
    return '%s__x5__.macro_%s%s' % (name[:2], asset.id, name[-2:])

  @classmethod
  def match(cls, snippet):
    """Checks if a bundle matches this type."""
    return x5_utils.all_groups_match(cls._MATCH_REGEXP, snippet.content)

  def _detect_edge_runtime(self, content):
    """Detects the Edge runtime and returns a runtime named tuple."""
    m = self._RUNTIME_REGEXP.search(content)
    if not m:
      raise x5_exceptions.X5ConverterError(
          'Edge detected in %s but no runtime found' % self.bundle.transform_id
      )
    return self._edge_runtime(**m.groupdict())

  def _find_edge_js(self, content, assets_root):
    """Finds and returns the Edge js asset."""
    js_match = self._JS_REGEXP.search(content)
    if not js_match:
      raise x5_exceptions.X5ConverterError(
          'Edge detected in %s but no js found' %  self.bundle.transform_id
      )
    js_name = urllib.unquote('%s_edge.js' % js_match.group('name'))
    snippet_assets = self.bundle.assets_relative_to(assets_root)
    try:
      return js_match, snippet_assets[js_name]
    except KeyError:
      raise x5_exceptions.X5ConverterError(
          'Edge detected in %s but no js asset found' % self.bundle.transform_id
      )

  def _fix_edge_js(self, js_asset, snippet_root, runtime):
    """Replaces paths and asset references with X5 variables in Edge js file."""
    paths = self._PATHS_REGEXP.findall(js_asset.content)
    paths = [
        os.path.join(snippet_root, p[1]) for p in paths if p[1]
    ] + [snippet_root]
    js_asset.content = self._PATHS_REGEXP.sub(
        r"\1=''", js_asset.content
    )
    assets = self.bundle.assets_relative_to(paths)
    assets_regexp = self._edge_js_match_regexp(runtime, assets)
    js_asset.parsed_content = assets_regexp.sub(
        functools.partial(self._edge_js_match_function, js_asset, assets),
        js_asset.content
    )

  def _fix_edge_clickurl(self, content):
    """Replaces instances of window.open("url") with window.open(clickTag)."""
    return self._WINDOWOPEN_REGEXP.sub(
        r'window.open(clickTag\1)', content
    )

  def _fix_edge_js_assets(self, js_asset, snippet):
    """Associates the Edge js assets with the snippet and returns X5 vars."""
    x5vars = []
    for asset_name in set(js_asset.assets):
      asset = self.bundle.assets[asset_name]
      snippet.assets.append(asset_name)
      x5vars.append(
          '__x5__.macro_%(id)s = "%%%%FILE:%(id)s%%%%";' % {'id': asset.id}
      )
      if not asset.inlineable:
        continue
      self._convert_default(asset, append_assets_to=snippet)
    js_asset.assets = []
    return x5vars

  def convert(self, snippet):
    """Converts the snippet and its assets in place."""
    content = snippet.content
    runtime = self._detect_edge_runtime(content)
    content = content.replace(
        runtime.src, self._RUNTIME_URL % {'version': runtime.version}
    )
    js_match, js_asset = self._find_edge_js(content, snippet.root)
    snippet.assets.append(js_asset.name)
    self._fix_edge_js(js_asset, snippet.root, runtime.name)
    js_asset.parsed_content = self._fix_edge_clickurl(js_asset.parsed_content)
    content_parts = [
        content[:js_match.start()],
        '\n// start x5 injected variables'
    ]
    content_parts += self._CLICKTAGS
    content_parts.append('var __x5__ = {};')
    content_parts += self._fix_edge_js_assets(js_asset, snippet)
    content_parts.append('// end x5 injected variables\n')
    content_parts.append('// Firefox and IE rendering latency remover\n')
    content_parts.append('AdobeEdge.yepnope.errorTimeout = 5e2;\n\n')
    content_parts.append('%s%s%s' % (
        js_match.group('pre'),
        '%%%%FILE:%s%%%%&_=' % js_asset.id,
        js_match.group('post')
    ))
    content_parts.append(
        content[js_match.end():]
    )
    snippet.parsed_content = '\n'.join(content_parts)


class X5ConverterHype(X5ConverterDefault):
  """Converter for Tumult Hype bundles."""

  X5_TYPE = 'hype'

  _MATCH_REGEXP = re.compile(
      r'<script\s[^>]*src=["\'][^"\']+_hype_generated_script.js\?[0-9]+["\']'
  )
  _SCRIPT_REGEXP = re.compile((
      r'<script\s[^>]*src=["\']'
      r'([^"\']+_hype_generated_script.js)(?:\?[0-9]+)?'
      r'["\'][^>]*/?>(?:\s*</script>)?'
  ))
  _FOLDER_VAR_REGEXP = re.compile(r'var f\s*=\s*"[^"]+",')
  # pylint: disable=line-too-long
  _DOMAIN_FIX_SCRIPT = (
      "var hypeElementContainer = '%s_hype_container';\n"
      "function hypeUpdate(){\n"
      "  var hypeDivElements = document.getElementById(hypeElementContainer)\n"
      "      .getElementsByTagName('DIV');\n"
      "  var ph = window.location.protocol + '//' + window.location.host + '/';\n"
      "  for (hi=0; hi<hypeDivElements.length; hi++) {\n"
      "    if (hypeDivElements[hi].style.backgroundImage.indexOf('url') > -1) {\n"
      "      hypeDivElements[hi].style.backgroundImage = hypeDivElements[hi]"
      ".style.backgroundImage.replace('url(\"/', 'url(\"').replace(ph, '')\n"
      "    }\n"
      "  }\n"
      "}\n"
      "onload=hypeUpdate;\n"
  )
  # pylint: enable=line-too-long

  @classmethod
  def match(cls, snippet):
    """Checks if a bundle matches this type."""
    return cls._MATCH_REGEXP.search(snippet.content)

  def _parse_hype_script_tag(self, content):
    m = self._SCRIPT_REGEXP.search(content)
    if not m:
      raise x5_exceptions.X5ConverterError('Hype script tag not found.')
    return m.start(), m.end(), m.group(1)

  def convert(self, snippet):
    content = snippet.content
    tag_start, tag_end, asset_name = self._parse_hype_script_tag(content)
    asset_name = os.path.basename(asset_name)
    if asset_name not in self.bundle.assets:
      raise x5_exceptions.X5ConverterError(
          'Hype script %s not found.', asset_name
      )
    hype_content = self.bundle.assets[asset_name].content
    hype_content = self._FOLDER_VAR_REGEXP.sub('var f="",', hype_content)
    domain_fix_script = self._DOMAIN_FIX_SCRIPT % asset_name.replace(
        '_hype_generated_script.js', ''
    )
    content = content[:tag_start] + content[tag_end:]
    content = content.replace(
        '</body>',
        '<script>\n%s\n%s\n</script>\n</body>' % (
            hype_content, domain_fix_script
        )
    )
    snippet.content = content
    del self.bundle.assets[asset_name]
    return self._convert_default(snippet)


X5_CONVERTERS = [X5ConverterEdge, X5ConverterHype, X5ConverterDefault]
