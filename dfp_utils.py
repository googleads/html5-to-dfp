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

"""DFP API utilities."""

import functools
import logging
import sys

import env

from googleads import common
from googleads import dfp
from googleads import oauth2
from suds import sudsobject
from suds import WebFault
from suds.cache import Cache

from google.appengine.api import memcache


logger = logging.getLogger('x5.dfp')


# Filter out unnamed attributes from suds objects we get back from the API.
_SUDS_MAP = {
    'Network': {
        'id': 'id', 'networkCode': 'code',
        'propertyCode': 'property', 'displayName': 'name'
    },
    'User': {
        'id': 'id', 'email': 'email', 'name': 'name'
    },
    'Company': {
        'id': 'id', 'name': 'name'
    }
}


class ServiceError(Exception):
  """Raised when DFP API returns an error."""
  pass


class AuthenticationError(ServiceError):
  """Raised if the suds error signals an authentication error."""
  pass


class PermissionError(ServiceError):
  """User does not have the necessary permission."""
  pass


class ApiAccessError(ServiceError):
  """DFP network is not enabled for API access."""
  pass


class AdvertiserError(ServiceError):
  """Advertiser ID is not valid for network."""
  pass


class MemcacheCache(Cache):

  def get(self, id):
    return memcache.get('wsdl_%s' % id)

  def put(self, id, object):
    return memcache.add('wsdl_%s' % id, object)

  def purge(self, id):
    return memcache.delete('wsdl_%s' % id)

  def clear(self):
    return


def _dfp_api_error_converter(f):
  """Converts the very generic WebFault to an actionable exception."""
  @functools.wraps(f)
  def wrapper(*args, **kwargs):
    try:
      return f(*args, **kwargs)
    except WebFault as e:
      tb = sys.exc_info()[2]
      try:
        api_errors = e.fault.detail.ApiExceptionFault.errors
      except AttributeError:
        raise ServiceError, ServiceError(e), tb
      # this is supposed to be a list but returns a dict when a single error
      # occurs.
      if not isinstance(api_errors, list):
        api_errors = [api_errors]
      for api_error in api_errors:
        e_str = api_error.errorString
        if e_str == 'PermissionError.PERMISSION_DENIED':
          raise PermissionError, PermissionError(e), tb
        elif e_str == 'AuthenticationError.NOT_WHITELISTED_FOR_API_ACCESS':
          raise ApiAccessError, ApiAccessError(e), tb
        elif e_str == 'AuthenticationError.AUTHENTICATION_FAILED':
          raise AuthenticationError, AuthenticationError(e), tb
        elif e_str == 'CommonError.NOT_FOUND':
          if api_error.fieldPath.endswith('.advertiserId'):
            raise AdvertiserError, AdvertiserError(e), tb
        raise ServiceError, ServiceError(e), tb
  return wrapper


class RefreshClient(oauth2.GoogleRefreshTokenClient):
  """Refresh token client that accepts a pre-made OAuth2Credentials instance."""

  def __init__(self, credentials, proxy_config=None):
    # pylint: disable=super-init-not-called
    self.oauth2credentials = credentials
    self.proxy_config = (
        proxy_config if proxy_config else common.ProxyConfig()
    )


def suds_to_dict(obj):
  """Converts a suds object instance to a dict."""
  mapping = _SUDS_MAP.get(obj.__class__.__name__)
  if mapping is None:
    return sudsobject.asdict(obj)
  return dict(
      (mapping[k], v) for k, v in sudsobject.asdict(obj).items() if k in mapping
  )


def get_client(credentials, network_code=None):
  """Returns a DFP API client instance for a specific network."""
  return dfp.DfpClient(
      RefreshClient(credentials), application_name=env.DFP_APP_NAME,
      network_code=network_code, enable_compression=True, cache=MemcacheCache()
  )


def do_query(method, query, values):
  """Executes a query on a DFP API method, returning a list of results."""

  # Trap exceptions here instead of in caller?
  statement = dfp.FilterStatement(query, values)
  data = []

  while True:
    response = method(statement.ToStatement())
    if 'results' in response:
      data += response['results']
      statement.offset += dfp.SUGGESTED_PAGE_LIMIT
    else:
      break

  return data


@_dfp_api_error_converter
def _get_network_user(credentials, network_code):
  network_client = get_client(credentials, network_code)
  return network_client.GetService(
      'UserService', version=env.DFP_API_VERSION
  ).getCurrentUser()


@_dfp_api_error_converter
def current_user_networks(credentials):
  """Fetches user networks from DFP.

  Retrieves list of DFP networks from the API, where the current user is
  an admin.

  Args:
    credentials: an oauth2client-compatible credentials instance

  Returns:
    A list of dictionaries, one for each network, containing the following
    fields: id, code, property, name, user (DFP user id).
  """

  client = get_client(credentials)
  networks = {}
  network_service = client.GetService(
      'NetworkService', version=env.DFP_API_VERSION
  )

  for network in network_service.getAllNetworks():
    # TODO(ludomagno): skip networks where isTest is True
    try:
      # TODO(ludomagno): we should probably use getattr as net is a suds object
      user = _get_network_user(credentials, network['networkCode'])
      if not user:
        continue
      # TODO(ludomagno): check if we need to relax this limit
      # TODO(ludomagno): verify if getattrs are needed
      if not getattr(user, 'isActive', False):
        continue
      network = suds_to_dict(network)
      network['user'] = suds_to_dict(user)
      networks[network['code']] = network
    except AuthenticationError:
      raise
    except ServiceError:
      logger.exception(
          'Error checking permissions for network %s',
          network['networkCode']
      )
  return networks


@_dfp_api_error_converter
def advertisers_list(credentials, network_code, prefix=None, as_dict=False):
  """Fetches list of advertisers for network_code, with optional prefix filter.

  Args:
      credentials: oauth2 credentials
      network_code: DFP network code
      prefix: optional prefix added to where clause
      as_dict: return results as dict instead of suds objects

  Returns:
      list of advertisers
  """

  client = get_client(credentials, network_code)
  service = client.GetService('CompanyService', version=env.DFP_API_VERSION)
  if prefix is None:
    # TODO(ludomagno): re-assess the need of type filter (ADVERTISER, etc.)
    query = 'ORDER by name'
    values = []
  else:
    if isinstance(prefix, str):
      prefix = prefix.decode('utf-8', errors='ignore')
    # TODO(ludomagno): re-assess the need of type filter (ADVERTISER, etc.)
    query = 'WHERE name LIKE :name ORDER by name'
    values = [{
        'key': 'name',
        'value': {'xsi_type': 'TextValue', 'value': prefix + u'%'}
    }]

  result = do_query(service.getCompaniesByStatement, query, values)
  return result if not as_dict else [suds_to_dict(r) for r in result]


@_dfp_api_error_converter
def submit_creative(credentials, network_code, creative):
  """Submits a new creative to the API."""
  client = get_client(credentials, network_code)
  service = client.GetService('CreativeService', version=env.DFP_API_VERSION)
  return service.createCreatives([creative])
