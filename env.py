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

"""App environment setup."""

import logging
import os

from google.appengine.api import app_identity


APP_NAME = app_identity.get_application_id()
SERVER_SOFTWARE = os.environ.get('SERVER_SOFTWARE', '')

DFP_API_VERSION = os.environ.get('DFP_API_VERSION', 'v201711')
DFP_APP_NAME = os.environ.get('DFP_APP_NAME', 'x5')
ASSET_SIZE_LIMIT = os.environ.get('ASSET_SIZE_LIMIT', 1000000)

DEBUG = False

logger = logging.getLogger('x5.env')


# Replace with your project id
if APP_NAME == 'replace-with-your-project-id':
  # Put the client id for your production server here
  CLIENT_ID = (
      'replace-with-your-production-client-id.apps.googleusercontent.com'
  )
elif SERVER_SOFTWARE.startswith('Development'):
  # Put your development server credentials here
  DEBUG = bool(os.environ.get('DEBUG', True))
  CLIENT_ID = (
      'replace-with-your-development-client-id.apps.googleusercontent.com'
  )
  CLIENT_SECRET = 'replace-with-your-development-client-secret'
else:
  CLIENT_ID = None
  logger.critical('Not in development and application id %s unknown.', APP_NAME)
