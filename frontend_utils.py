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

"""Utility functions used by the frontend handlers."""

from base64 import b64encode
import datetime
import email.header
import functools
import json
import logging
import os
import time

import env

from jinja2.utils import Markup
from oauth2client.contrib import appengine
from oauth2client.contrib import xsrfutil

from google.appengine.api import memcache
from google.appengine.api import users
from google.appengine.ext import ndb


logger = logging.getLogger('x5.frontend')


class SiteClientSecret(ndb.Model):
  """NDB Model for storage of the site's client secret used for OAuth2."""
  secret = ndb.StringProperty()


def client_secret():
  """Returns the client secret for the site, creates one if it doesn't exist."""
  if env.DEBUG:
    # Try the shortcut used for the development server.
    secret = getattr(env, 'CLIENT_SECRET', None)
    if secret:
      return secret
  secret = memcache.get('client_secret', namespace='frontend_utils#ns')
  if not secret:
    model = SiteClientSecret.get_by_id('site')
    if not model:
      # Administrator will need to enter this from the console, don't cache.
      logger.error('Change client secret from the appengine console.')
      secret = ''
    else:
      secret = model.secret
      memcache.add('client_secret', secret, namespace='frontend_utils#ns')
  return str(secret)


class SiteSessionKey(ndb.Model):
  """NDB Model for storage of the site's session key."""
  secret = ndb.StringProperty()


def session_key():
  """Returns the session key for the site, creates one if it doesn't exist."""
  secret = memcache.get('session_key', namespace='frontend_utils#ns')
  if not secret:
    model = SiteSessionKey.get_or_insert('site')
    if not model.secret:
      model.secret = os.urandom(16).encode('hex')
      model.put()
    secret = model.secret
    memcache.add('session_key', secret, namespace='frontend_utils#ns')
  return str(secret)


def json_default(value):
  """Basic transformations used when encoding to JSON."""
  if isinstance(value, datetime.datetime):
    return int(time.mktime(value.timetuple()) * 1000)
  return value


def jinja_x5_encode(d):
  """Jinja filter to encode a template variable to JSON+base64."""
  try:
    return Markup(b64encode(json.dumps(
        d, ensure_ascii=True, default=json_default, encoding='utf-8'
    )))
  except (TypeError, ValueError):
    logger.exception('Error converting template variable to json')
    return ''


def decode_header(s):
  """Decodes MIME header encoding used by prod blobstore for filenames."""
  if s.startswith('=?UTF-8?'):
    try:
      s = unicode(email.header.make_header(email.header.decode_header(s)))
    except (UnicodeDecodeError, UnicodeEncodeError):
      pass
  return s.decode('utf-8', errors='ignore') if isinstance(s, str) else s


def generate_token():
  """Returns a generated xsrf token for current user."""
  return xsrfutil.generate_token(
      appengine.xsrf_secret_key(),
      users.get_current_user().user_id(),
      action_id='x5'
  )


def xsrf_valid(method):
  """Validates the xsrf token found in POST field or headers."""

  @functools.wraps(method)
  def _validate_token(handler, *args, **kw):
    """Check xsrf token from POST field or header."""
    token = handler.request.POST.get('xsrf_token')
    token = token or handler.request.headers.get('x-xsrf-token')
    if not token:
      handler.abort(400, 'no token')
      return
    if isinstance(token, unicode):
      token = token.encode('utf-8', errors='ignore')
    valid = xsrfutil.validate_token(
        appengine.xsrf_secret_key(),
        token,
        users.get_current_user().user_id(),
        action_id='x5'
    )
    if not valid:
      handler.abort(400, 'invalid token')
    else:
      return method(handler, *args, **kw)

  return _validate_token
