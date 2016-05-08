import json
import logging
import pickle

import requests
import yaml

import alooma_exceptions
import submodules

try:
    with open('logging.conf') as f:
        logging_conf = yaml.load(f)
        logging.config.dictConfig(logging_conf)
except IOError:
    logging.basicConfig(
            format='%(asctime)s [%(levelname)s] %(process)d %(name)s: '
                   '%(message)s')

logger = logging.getLogger(__name__)

EVENT_DROPPING_TRANSFORM_CODE = 'def transform(event):\n\treturn None'

DEFAULT_ENCODING = 'utf-8'


class Alooma(object):
    """
    A Python implementation wrapping the Alooma REST API. This API
    provides utility functions allowing a user to perform any action
    the Alooma UI permits, and more.
    """

    def __init__(self, hostname, username, password, port=8443,
                 url_prefix='', eager=False, session_file=None):
        """
        Initializes the Alooma Python API
        :param hostname:    The server to connect to. Typically will be of the
                            form "<your-company-name>.alooma.io"
        :param username:    Your Alooma username
        :param password:    The password associated with your username
        :param port:        (Optional) The destination port, default is 8443
        :param url_prefix:  (Optional) A prefix to append to the REST URL
        :param eager:       (Optional) If True, attempts to log in eagerly
        :param session_file:(Optional) A file containing a pickled session. The
                            API will use that session and save its session to it
                            when it is closed
        """
        self._hostname = hostname
        self._rest_url = 'https://%s:%d%s/rest/' % (hostname,
                                                    port,
                                                    url_prefix)
        self._username = username
        self._password = password
        self._requests_params = None
        self._session = None
        self._session_file = session_file

        if eager:
            self.__get_session()

        self._load_submodules()

    def _load_submodules(self):
        """
        Loads all submodules registered in the submodules package.
        Submodules are automatically registered by being put in the
        submodules subfolder. They must contain a 'SUBMODULE_CLASS'
        member pointing to the actual submodule class
        """
        for module_name in submodules.SUBMODULES:
            try:
                submodule = getattr(submodules, module_name)
                submodule_class = getattr(submodule, 'SUBMODULE_CLASS')
                setattr(self, module_name, submodule_class(self))
            except Exception as ex:
                logger.exception('The submodule "%s" could not be loaded. '
                                 'Exception: %s', module_name, ex)

    def _send_request(self, func, url, is_recheck=False, **kwargs):
        """
        Wraps REST requests to Alooma. This function ensures we are logged in
         and that all params exist, and catches any exceptions.
        :param func: a method from the requests package, i.e. requests.get()
        :param url: The destination URL for the REST request
        :param is_recheck: If this is a second try after losing a login
        :param kwargs: Additional arguments to pass to the wrapped function
        :return: The requests.model.Response object returned by the wrapped
        function
        """
        if not self._session:
            self.__get_session()

        params = self._requests_params.copy()
        params.update(kwargs)

        func_name = func.__name__
        session_func = getattr(self._session, func_name)

        response = session_func(url, **params)

        if self._response_is_ok(response):
            return response

        if response.status_code == 401 and not is_recheck:
            self.__login()
            return self._send_request(func, url, True, **kwargs)

        raise Exception('The rest call to %s failed: %s' % (response.url,
                                                            response.content))

    def __login(self):
        url = self._rest_url + 'login'
        login_data = {"email": self._username, "password": self._password}
        response = self._session.post(url, json=login_data)

        if response.status_code == 200:
            logger.debug('Logged in to Alooma server: %s', self._hostname)
        else:
            msg = 'Failed to login to %s with username: %s'
            logger.error(msg, self._hostname, self._username)
            raise alooma_exceptions.SessionError(
                    msg % (self._hostname, self._username))

    def __get_session(self):
        """
        Gets a previous sessions if specified, otherwise logs into the Alooma
        server associated with this API instance
        """
        self._requests_params = {'timeout': 60}
        session = self._session

        if not session:  # There is no session, get a new one or a stored one
            if self._session_file:
                try:
                    with open(self._session_file) as sf:
                        self._session = pickle.load(sf)
                        return
                except Exception as ex:
                    logger.exception('Failed to load session from "%s": %s.'
                                     'Creating a new session',
                                     self._session_file, ex)

            # There is no session file or we failed to load it
            self._session = requests.Session()
            self.__login()

    def close(self):
        self.__exit__()

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        if self._session:
            self._session.close()
            if self._session_file:
                try:
                    with open(self._session_file, 'w+') as sf:
                        pickle.dump(self._session, sf)
                except Exception as ex:
                    logger.exception('Failed to store the session in a file: '
                                     '%s', ex)

    @staticmethod
    def _response_is_ok(response):
        return 200 <= response.status_code < 300

    @staticmethod
    def _parse_response_to_json(response):
        return json.loads(response.content.decode(DEFAULT_ENCODING))
