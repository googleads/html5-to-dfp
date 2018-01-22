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

"""OAuth2 utilities."""


import logging

from xml.sax.saxutils import escape

import dfp_utils

from oauth2client.contrib import appengine

from google.appengine.api import users
from google.appengine.ext import db


logger = logging.getLogger('x5.oauth')
logging.getLogger('suds').setLevel(logging.WARNING)


class DFPDecorator(appengine.OAuth2Decorator):

  def _display_error_message(self, request_handler, message=None):
    # TODO(ludomagno): show an error page
    if message:
      self._message = message
    request_handler.response.out.write('<html><body>')
    request_handler.response.out.write(escape(self._message, True))
    request_handler.response.out.write('</body></html>')

  def dfp_access_required(self, method):
    """Execute the oauth-required decorator then run DFP checks."""

    def wrapper(request_handler, *args, **kw):
      """Decorator wrapper."""

      user_id = users.get_current_user().user_id()
      request_handler.x5_networks = {}

      # Check session first in case user already passed checks.
      if request_handler.session and 'x5_data' in request_handler.session:
        x5_data = request_handler.session['x5_data']
        if isinstance(x5_data, dict) and x5_data.get('id') == user_id:
          request_handler.x5_networks = x5_data.get('networks', {})
          return method(request_handler, *args, **kw)

      request_handler.session['x5_data'] = {}

      # pylint: disable=broad-except
      try:
        networks = dfp_utils.current_user_networks(self.credentials)
      except dfp_utils.AuthenticationError:
        # App permissions might have been revoked.
        logger.warning('deleting credentials for user %s', user_id)
        try:
          db.delete(db.Key.from_path('CredentialsModel', user_id))
          # pylint: disable=bare-except
        except:
          logger.exception('error deleting credentials for user %s', user_id)
        return self._display_error_message(
            request_handler,
            'Error during authentication, please try to refresh the page.'
        )

      if not networks:
        return self._display_error_message(
            request_handler, 'No valid networks found'
        )

      request_handler.x5_networks = networks
      request_handler.session['x5_data'] = {'id': user_id, 'networks': networks}

      return method(request_handler, *args, **kw)

    return self.oauth_required(wrapper)
