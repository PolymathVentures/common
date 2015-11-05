import unittest
import json
import yaml
import importlib
import http.client

from operator import itemgetter
from flask.json import dumps
from flask.testing import FlaskClient
from flask.wrappers import Response
from contextlib import wraps


def assert_response_ok(http_method):
    """
    Wrap the given HTTP method and assert it returns 200 OK status code.
    """

    @wraps(http_method)
    def wrapper(*args, **kwargs):
        response = http_method(*args, **kwargs)
        assert response.status_code == http.client.OK
        return response
    return wrapper


class TestingClient(FlaskClient):
    """
    Customize the test client.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_ok = assert_response_ok(self.get)
        self.patch_ok = assert_response_ok(self.patch)
        self.post_ok = assert_response_ok(self.post)
        self.head_ok = assert_response_ok(self.head)
        self.put_ok = assert_response_ok(self.put)
        self.delete_ok = assert_response_ok(self.delete)
        self.trace_ok = assert_response_ok(self.trace)
        self.options_ok = assert_response_ok(self.options)

    def post_json_ok(self, url, payload):
        """
        Send a POST request with JSON content in the body.
        """

        return self.post_ok(url,
                            content_type='application/json',
                            data=dumps(payload, indent=2))

    def put_json_ok(self, url, payload):
        """
        Send a PUT request with JSON content in the body.
        """

        return self.put_ok(url,
                           content_type='application/json',
                           data=dumps(payload, indent=2))


class TestingResponse(Response):
    """
    Customize the standard response class to ease testing.
    """

    @property
    def json(self):
        return json.loads(self.data.decode('utf-8'))


class BaseFlaskTestCase(unittest.TestCase):
    """
    Base class for Flask and SQLAlchemy tests. This class resets the test database and populates it with any
    fixture data before every test. It also managed the Flask request context and the associated SQLAlchemy
    session to mimic the handling of an actual request.
    """

    def setUp(self):
        """
        Configure the Flask app for testing, setup a Flask request context, reset the test database, and
        load fixture data.
        """

        assert hasattr(self, 'app'), 'Please provide a reference to the Flask app'
        assert hasattr(self, 'db'), 'Please provide a reference to the Flask SQLAlchemy object'

        # Configure app for testing
        self.app.testing = True
        self.app.response_class = TestingResponse
        self.app.test_client_class = TestingClient

        self.context = self.app.test_request_context()
        self.context.push()

        self.client = self.app.test_client()

        self.create_schema()
        self.load_fixtures()

    def create_schema(self):
        """
        Wipe and re-create the test database schema.
        """

        # Sanity check before blowing everything away
        assert 'test' in self.app.config['SQLALCHEMY_DATABASE_URI'], 'You do not appear to be pointing to a test database'
        self.db.drop_all()
        self.db.create_all()

    def load_fixtures(self):
        """
        Load fixture data specified in the test class. To use, add a class-level attribute:

            fixtures = ['path/to/fixtures.yaml']

        The path should be relative to the Flask app. The fixtures YAML should look like this:

            - model: fully.qualified.ModelName
              records:
                  - id: 1
                    attribute1: value1
                    attribute2: value2
                    attribute3: value3
                  - id: 2
                    attribute1: value1
                    attribute2: value2
                    attribute3: value3
                ...

        (Some bits of the implementation copied from https://github.com/croach/Flask-Fixtures
        which sadly does not support Python 3.)
        """

        for fixture_path in getattr(self, 'fixtures', []):
            with open(fixture_path) as f:
                fixtures = yaml.load(f.read())
                for fixture in fixtures:
                    module_name, class_name = fixture['model'].rsplit('.', 1)
                    module = importlib.import_module(module_name)
                    model = getattr(module, class_name)
                    for fields in fixture['records']:
                        self.db.session.add(model(**fields))
            self.db.session.commit()

    def tearDown(self):
        """
        Pop the Flask request context. This emulates the normal flow in a Flask app where the context is
        popped when the app finishes handling the request. This is very important to do as it also cleans
        up the SQLAlchemy session.
        """

        # Need to manually rollback the transaction because, of course, Flask does not do this
        # automatically (on error or any other reason). Flask manages the session but has no opinion on
        # transaction handling.
        self.db.session.rollback()
        self.context.pop()

    def assertEntitiesContain(self, actual_entities, expected_entities):
        """
        Check that the given list of actual entities matches those expected. Entities are matched on the
        basis of their 'id'. The comparison of actual to expected is not a straight up equality check. Rather,
        this method verifies that every actual entity is a superset of the corresponding expected entity. In
        other words, all key/value pairs in expected must be in actual, but actual may also contain additional
        keys that are ignored.
        """

        self.assertEqual(len(actual_entities), len(expected_entities), 'Entity lengths don\'t match')

        actual_entities_by_id = {x['id']: x for x in actual_entities}
        for expected in expected_entities:
            id = expected['id']
            actual = actual_entities_by_id.get(id)
            self.assertIsNotNone(actual, 'No actual entity for id {id}'.format(id=id))
            if not set(actual.items()).issuperset(set(expected.items())):
                self.fail('Actual does not have everything expected: {actual}, {expected}'.format(actual=actual,
                                                                                                  expected=expected))

    def canonicalRepr(self, payload):
        """
        Canonicalize a payload intended to be consumed by Ember's Rest Adapter by sorting the lists. This enables
        payloads to be compared with == and leverages pytest's magic introspection.
        """

        return {key: sorted(value, key=itemgetter('id')) if isinstance(value, list) else value
                for key, value in payload.items()}