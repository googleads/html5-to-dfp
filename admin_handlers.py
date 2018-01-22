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

"""Admin handlers to browse and download uploaded bundles."""

import collections
import logging
import os

import env
import jinja2
import webapp2
# pylint: disable=unused-import
import x5_transform

from google.appengine.api import datastore_errors
from google.appengine.api import users
from google.appengine.ext import ndb
from google.appengine.ext.webapp import blobstore_handlers


logger = logging.getLogger('x5.admin')

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATES_DIR),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True
)


DownloadItem = collections.namedtuple(
    'DownloadItem', 'key filename creative_id blob_key created'
)


class IndexHandler(webapp2.RequestHandler):

  def get(self):
    if not users.is_current_user_admin():
      self.abort(400)
    query = ndb.gql('''
        select creative_id, filename, blob_key, created
        from X5Transform
        order by created desc
    ''')
    template = JINJA_ENVIRONMENT.get_template('admin.html')
    results = []
    filenames = set()
    for r in query:
      if not r.creative_id:
        continue
      filename = os.path.basename(r.filename)
      if filename in filenames:
        continue
      filenames.add(filename)
      item = DownloadItem(
          r.key, filename, r.creative_id, r.blob_key, r.created
      )
      results.append(item)
    self.response.write(template.render({'results': results}))


class DownloadHandler(blobstore_handlers.BlobstoreDownloadHandler):

  def get(self, key):
    if not users.is_current_user_admin():
      self.abort(400)
    try:
      obj = ndb.Key(urlsafe=key).get()
    except datastore_errors.Error, e:
      self.abort(500, str(e))
    if obj is None:
      self.abort(404, 'no object')
    self.send_blob(
        obj.blob_key,
        save_as='%s - %s' % (obj.creative_id, obj.filename)
    )


app = webapp2.WSGIApplication([
    (r'/admin/?', IndexHandler),
    (r'/admin/download/([^/]+)/?', DownloadHandler),
], config={}, debug=env.DEBUG)
