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

import functools
import itertools
import re
import urllib


def quoted_unquoted_tokens(tokens):
  """Return a set of tokens in verbatim and quoted form."""
  return set(itertools.chain(tokens, (urllib.quote(k) for k in tokens)))


def tokens_regexp(tokens):
  """Returns a regexp matching any token."""
  return re.compile(r'(?:%s)' % '|'.join(
      '(%s)' % re.escape(t) for t in tokens
  ), re.I)


def tokens_regexp_quoted(tokens, fmt='%s'):
  """Returns a compiled regexp matching any token, verbatim and quoted."""
  return re.compile(r'(%s)' % '|'.join(
      fmt % re.escape(t) for t in quoted_unquoted_tokens(tokens)
  ))


def all_groups_match(regexp, text):
  """Test if all groups in regexp are present at least once in text."""
  matches = regexp.findall(text)
  return matches and all(any(t) for t in zip(*matches))


def _match_function(snippet, assets, match, template=None):
  """Base match function that replaces asset names with macros in snippet."""
  name = match.group(1)
  if '%' in name:
    name = urllib.unquote(name)
  try:
    asset = assets[name]
  except KeyError:
    return match.group(1)
  snippet.assets.append(asset.name)
  return (template or '%%%%FILE:%(id)s%%%%') % {'id': asset.id}


def match_function(snippet, assets, template=None):
  """Return match function that replaces asset names with macros in snippet."""
  return functools.partial(_match_function, snippet, assets, template=template)
