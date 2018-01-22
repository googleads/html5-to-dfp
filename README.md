# HTML5 TO DFP

**NOTE: This is not an officially supported Google product.**

This repository contains an open source version of the HTML5 creative converter
tool for DFP, previously living as a service at https://html5-to-dfp.appspot.com/.

HTML5 to DFP makes it easy for traffickers to upload to DFP as custom creatives, HTML5 creative bundles (.zip files) created in Adobe Edge and Google Web Designer.

Please note that **HTML5 creative bundles are now directly supported in DFP** - you can upload them as easily as any other asset by using the new HTML5 creative type.

A [detailed Help Center article](https://support.google.com/dfp_premium/answer/7046902) is available for both DFP Premium and DFP Small Business

## Installation

The preferred way of running this project is as a standalone Appengine
application. Running it on your local machine is also possible, either
to test changes to the source code, or as a quick way of uploading a
single bundle.

To run on your local machine, all you need is the development appserver
provided by the Cloud SDK.

Running it on a live Appengine instance is slightly more complex, but
allows you to support multiple users, without worrying about managing
the development server.

### Local prerequisites

On your local machine, you need:

* the [Google Cloud SDK for Appengine Standard Environment](https://cloud.google.com/appengine/docs/standard/python/download)
* a version of Python 2.7
* the `pip` Python package (`sudo apt-get install python-pip` on Debian-like systems)

Once the above prerequisites are satisfied, run these two commands in the source
folder to download the third party packages required by this project:

``` shell
mkdir -p lib
pip install -t lib googleads
```

### Google Cloud console configuration

On the Google Cloud side, you need:

* a (possibly dedicated) project
* one set of credentials for each environment you plan on running
  (eg local development, production, etc.)

Credentials ("API and Services" / "Credentials" in the sidebar) have to
be of the "Web application" type. Remember to configure their
"Authorized redirect URIs" with the right URLs for each
environment, eg `http://localhost:8080/oauth2callback` for local
testing.

### Configuration

The only configuration data the app needs are the set of client id / secret for
each credential, and a unique random key for installation on appengine instances.

On the local development server, both the client id and secret are stored in
plaintext in the `env.py` file, in these lines of code:

```python
elif SERVER_SOFTWARE.startswith('Development'):
  DEBUG = bool(os.environ.get('DEBUG', True))
  # Put your development server credentials here
  CLIENT_ID = (
      'replace-with-your-development-client-id.apps.googleusercontent.com'
  )
  CLIENT_SECRET = 'replace-with-your-development-client-secret'
```

On appengine instances things are slightly more complicated. First, add your
project and client ids in the `env.py` file similarly to what's outlined above,
here:

```python
if APP_NAME == 'replace-with-your-project-id':
  # Put the client id for your production server here
  CLIENT_ID = (
      'replace-with-your-production-client-id.apps.googleusercontent.com'
  )
```

Then, you need to add a new NDB entity with the client secret:

* deploy the app on appengine
* in the Google Cloud console, navigate to "Datastore" / "Entities"
* create a new entity of type `SiteClientSecret` and set its `secret` property
  to the client secret for this environment's credentials

The last thing you might want to do is look at the `env.py` and `app.yaml` files,
and make sure the environment variables that set the API version are correct (for
example, you might want to update the API version is the one in the file has
been deprecated).


### Access control

The service is by default open to any valid DFP user, and lets them
upload creatives to the DFP networks they already have access to. For
security and resource usage reasons, you might want to limit access to
your instances to specific subsets of users (typically those in your
company).

You can easily restrict access by using IP-based access
controls through Appengine's "Firewall rules" feature, or by using
user/group access controls through the "Identity-Aware Proxy" feature.
