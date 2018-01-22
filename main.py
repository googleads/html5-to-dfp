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

"""Appengine app and app handlers for x5."""

import json
import logging
import os
import urllib

import dfp_utils
import env
import frontend_utils
import jinja2
import oauth2_utils
import webapp2
import x5_exceptions
import x5_transform

from webapp2_extras import sessions

from google.appengine.api import blobstore
from google.appengine.api import datastore_errors
from google.appengine.api import users
from google.appengine.ext import ndb
from google.appengine.ext.webapp import blobstore_handlers


logger = logging.getLogger('x5')

dfp_decorator = oauth2_utils.DFPDecorator(
    client_id=env.CLIENT_ID,
    client_secret=frontend_utils.client_secret(),
    scope='https://www.googleapis.com/auth/dfp'
)

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATES_DIR),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True
)
JINJA_ENVIRONMENT.globals['x5_ga_ua'] = getattr(env, 'GA_UA', None)
JINJA_ENVIRONMENT.filters['x5_encode'] = frontend_utils.jinja_x5_encode

# Blobstore will reject uploads > 50MBs.  This is a defensive limit and there's
# no way of handling the 413 error.  We will need to validate on the client
# side.  This limit is fairly high so it should allow for legitimate uses of the
# tool.
_BUNDLE_MAX_UPLOAD_BYTES = 50*1024*1024


class BaseHandler(webapp2.RequestHandler):
  """Base handler class implementing sessions and JSON response."""

  def initialize(self, request, response):
    super(BaseHandler, self).initialize(request, response)
    self.session_store = sessions.get_store(request=request)

  def dispatch(self):
    try:
      super(BaseHandler, self).dispatch()
    finally:
      self.session_store.save_sessions(self.response)

  @webapp2.cached_property
  def session(self):
    return self.session_store.get_session()

  def write_json(self, data=None, error=None):
    """Utility method to send a JSON response."""
    if error:
      self.status_int = 500
    try:
      content = json.dumps(
          {'data': data, 'error': error},
          ensure_ascii=False, default=frontend_utils.json_default
      )
    except (TypeError, ValueError) as e:
      logger.critical('Error encoding data to json: %s', e)
      self.status_int = 500
      content = json.dumps(
          {'error': e.args[0], 'data': None},
          ensure_ascii=False, default=frontend_utils.json_default
      )
    self.response.headers['Content-Type'] = 'application/json'
    self.response.headers['X-Content-Type-Options'] = 'nosniff'
    self.response.headers['Content-Disposition'] = (
        'attachment; filename="f.txt"'
    )
    self.response.write(content)


class IndexHandler(BaseHandler):
  """First stage form for uploading blob."""

  @dfp_decorator.dfp_access_required
  def get(self):
    user = users.get_current_user()
    template_values = {
        'xsrf_token': frontend_utils.generate_token(),
        'upload_action': blobstore.create_upload_url(
            '/upload/',
            max_bytes_total=_BUNDLE_MAX_UPLOAD_BYTES),
        'x5_networks': sorted(self.x5_networks.values()),
        'x5_transforms': list(x5_transform.X5Transform.user_transforms(
            user.user_id()
        )),
        'flashes': self.session.get_flashes(key='index')
    }
    template = JINJA_ENVIRONMENT.get_template('index.html')
    self.response.write(template.render(template_values))


class LogoutHandler(BaseHandler):
  """Logs out the user, and deletes session variables."""

  def get(self):
    self.session['x5_data'] = {}
    self.x5_networks = {}
    self.redirect(users.create_logout_url('/'))


class ZipUploadHandler(BaseHandler, blobstore_handlers.BlobstoreUploadHandler):
  """Receives blob, save X5 object and redirect."""

  @frontend_utils.xsrf_valid
  @dfp_decorator.dfp_access_required
  def post(self):
    # TODO(ludomagno): trap DeadlineExceededError in a task handler
    # https://cloud.google.com/appengine/articles/deadlineexceedederrors?hl=en
    try:
      blob_info = self.get_uploads()[0]
    except IndexError:
      logger.warning('No uploaded blobs')
      self.abort(500)

    blob_key = blob_info.key()
    network_code = self.request.POST.get('network')
    user_id = users.get_current_user().user_id()

    logger.info(
        'upload for network %s from user %s key %s',
        network_code, user_id, blob_key
    )

    # TODO(ludomagno): check that the blob is actually a zip file
    #                  don't leave it for when we parse it in x5_bundle
    network = self.x5_networks.get(network_code)
    if not network:
      self.abort(400)

    filename = frontend_utils.decode_header(getattr(blob_info, 'filename', ''))

    try:
      x5transform = x5_transform.X5Transform(
          parent=x5_transform.X5Transform.parent_key(user_id),
          blob_key=blob_key, network_code=network_code,
          filename=filename or None
      )
      x5_key = x5transform.put()
    except (blobstore.Error, datastore_errors.Error) as e:
      logger.critical('Error saving x5 transform: %s', e)
      self.abort(500)

    self.redirect('/metadata/%s/%s/' % (
        network_code, urllib.quote(str(x5_key.urlsafe()))
    ))


class MetadataHandler(BaseHandler):
  """Handler for the bundle check and submission user interface."""

  def _get_transform(self, network_code, transform_urlkey):
    user = users.get_current_user()
    user_id = user.user_id()
    logger.info(
        'metadata for network %s from user %s key %s',
        network_code, user_id, transform_urlkey
    )
    network = self.x5_networks.get(network_code)
    if not network:
      self.abort(400, 'no network')
    try:
      x5transform = ndb.Key(urlsafe=transform_urlkey).get()
    except datastore_errors.Error:
      # TODO(ludomagno): check for other exceptions
      logger.critical('No transform for key %s', transform_urlkey)
      self.abort(500, 'no object')
    if x5transform is None:
      self.abort(404, 'no object')
    if x5transform.key.parent() is None:
      logger.warning('Transform %s has no parent', transform_urlkey)
      self.abort(400, 'no parent')
    if x5transform.key.parent().id() != user_id:
      logger.warning(
          'User id %s does not match transform user id %s',
          user_id, x5transform.key.parent().id()
      )
      self.abort(400, 'wrong user id')
    if x5transform.network_code != network_code:
      logger.warning(
          'Network code %s does not match transform code %s',
          network_code, x5transform.network_code
      )
      self.abort(400, 'wrong network')
    return x5transform

  @dfp_decorator.dfp_access_required
  def get(self, network_code, transform_urlkey):
    try:
      x5transform = self._get_transform(network_code, transform_urlkey)
      template_values = {
          'xsrf_token': frontend_utils.generate_token(),
          'transform': x5transform.to_dict(exclude=(
              'blob_key', 'user_id', 'snippet',
              'metadata', 'creative_id', 'uploaded'
          )),
          # TODO(ludomagno): move encoding from as_dict to here
          'snippets': [
              snippet.as_dict(True) for snippet in x5transform.snippets.values()
          ],
          'assets': [
              asset.as_dict(True) for asset in x5transform.assets.values()
          ],
          'flashes': self.session.get_flashes(key='metadata')
      }
    except x5_exceptions.X5TransformError:
      # TODO(ludomagno): check in upload handler, delete/don't store on error.
      logger.exception('Error file transforming the bundle')
      self.session.add_flash('We encountered an error while processing the'
                             ' creative bundle.  Please ensure that the bundle'
                             ' is a zip archive and contains at least one HTML'
                             ' snippet and one asset in the archive.',
                             level='error',
                             key='index')
      self.redirect('/')
      return
    template = JINJA_ENVIRONMENT.get_template('metadata.html')
    self.response.write(template.render(template_values))

  @frontend_utils.xsrf_valid
  @dfp_decorator.dfp_access_required
  def post(self, network_code, transform_urlkey):
    x5transform = self._get_transform(network_code, transform_urlkey)
    metadata = {}
    for k in ('advertiser_id', 'snippet_id', 'url', 'size'):
      v = self.request.POST.get(k)
      if not v:
        logger.info("Field '%s' not in metadata form values.", k)
        self.abort(400, 'no value for %s' % k)
      elif k == 'snippet_id':
        k = 'snippet_name'
      metadata[k] = v
    metadata['creative_name'] = self.request.POST.get('creative_name')
    metadata['interstitial'] = self.request.POST.get('interstitial', 0)
    try:
      creative_data = dfp_utils.submit_creative(
          dfp_decorator.credentials, network_code,
          x5transform.get_creative(**metadata)
      )
    except x5_exceptions.X5TransformError as e:
      self.abort(500, e.args[0])
    except dfp_utils.PermissionError:
      self.session.add_flash('We encountered a permission error while'
                             ' uploading the creative.  Please make sure'
                             ' that you have creative editing rights on'
                             ' this DFP network.',
                             level='error',
                             key='metadata')
      self.redirect(self.request.url)
      return
    except dfp_utils.ApiAccessError:
      self.session.add_flash('We encountered a permission error while'
                             ' uploading the creative.  Please make sure'
                             ' that this DFP network is enabled for API'
                             ' access.',
                             level='error',
                             key='metadata')
      self.redirect(self.request.url)
      return
    except dfp_utils.AdvertiserError:
      self.session.add_flash('We encountered an error while uploading the'
                             ' creative. Please make sure that the advertiser'
                             ' ID is valid for this network.',
                             level='error',
                             key='metadata')
      self.redirect(self.request.url)
      return
    except dfp_utils.ServiceError as e:
      logger.exception('Creative upload error')
      self.abort(500, e.message)

    if not creative_data:
      logger.critical('No creatives from api for %s', transform_urlkey)
      self.abort(500, 'no creatives')

    try:
      x5transform.creative_id = creative_data[0]['id']
      x5transform.creative_preview = creative_data[0]['previewUrl']
      x5transform.put()
      # TODO(ludomagno): re-enable once we don't need to save bundles anymore
      # blobstore.delete(x5transform.blob_key)
    except (blobstore.Error, datastore_errors.Error) as e:
      logger.critical('Error saving x5 transform: %s', e)
      self.abort(500, e)

    self.session.add_flash('Upload successful.', key='index')

    self.redirect('/')


class AdvertisersHandler(BaseHandler):
  """Fetch advertisers from the DFP APIs for a given network."""

  @frontend_utils.xsrf_valid
  @dfp_decorator.dfp_access_required
  def get(self, network_code, prefix=None):
    data, error = [], None
    network = self.x5_networks.get(network_code)
    if not network:
      error = 'No access to DFP network'
    else:
      try:
        data = dfp_utils.advertisers_list(
            dfp_decorator.credentials, network_code, prefix, True
        )
      except dfp_utils.ServiceError as e:
        error = 'Error in DFP API call: %s' % e.message
    self.write_json(data, error)


config = {}
config['webapp2_extras.sessions'] = {
    'secret_key': frontend_utils.session_key(),
    'backends': {
        'securecookie': 'webapp2_extras.sessions.SecureCookieSessionFactory',
    },
}

app = webapp2.WSGIApplication([
    (dfp_decorator.callback_path, dfp_decorator.callback_handler()),
    (r'/logout/?', LogoutHandler),
    (r'/?', IndexHandler),
    (r'/upload/?', ZipUploadHandler),
    (r'/metadata/([0-9]+)/([^/]+)/?', MetadataHandler),
    (r'/advertisers/([0-9]+)/?', AdvertisersHandler),
    (r'/advertisers/([0-9]+)/([^/]+)/?', AdvertisersHandler),
], config=config, debug=env.DEBUG)
