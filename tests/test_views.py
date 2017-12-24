import pytest

from webtest import TestApp as WebTestApp  # fix pytest import issue
from cassandra.cqlengine import management

from moisturizer import main
from moisturizer.models import UserModel


@pytest.fixture(scope='module')
def test_app():
    app = WebTestApp(main({}, **{
        'moisturizer.keyspace': 'test',
    }))
    admin = UserModel.get(id='admin')
    app.authorization = ('Basic', (admin.id, admin.api_key))
    yield app

    # Ensure recriation, but make tests REALLY slow!
    async def finish():
        management.drop_keyspace('test')

    finish()


@pytest.fixture()
def valid_object_payload():
    return {'foo': 'bar', 'number': 42}


@pytest.fixture()
def invalid_object_payload():
    return {'foo': 12, 'number': 42}


@pytest.fixture()
def valid_type_payload():
    return {
        'id': 'my_type',
        'description': 'My precious type.',
        'properties': {
            'foo': {
                'type': 'string'
            }
        }
    }


@pytest.fixture()
def invalid_type_payload():
    return {'foo': 12, 'number': 42}


@pytest.fixture()
def valid_user_payload():
    return {'role': 'user', 'password': 'my_secret'}


def test_heartbeat(test_app):
    data = test_app.get('/__heartbeat__', status=200).json
    assert data['server']
    assert data['schema']
    assert data['users']


type_collection = '/types'
type_endpoint = '/types/{}'
object_collection = '/types/my_type/objects'
object_endpoint = '/types/my_type/objects/{}'
user_collection = '/users'
user_endpoint = '/users/{}'


def assert_response(payload, reponse):
    for k, v in payload.items():
        assert reponse[k] == v


def test_object_create(test_app, valid_object_payload):
    data = test_app.post_json(object_collection,
                              valid_object_payload,
                              status=200).json
    assert_response(valid_object_payload, data)


def test_object_get(test_app, valid_object_payload):
    created = test_app.post_json(object_collection,
                                 valid_object_payload,
                                 status=200).json
    data = test_app.get(object_endpoint.format(created['id']),
                        status=200).json
    assert_response(valid_object_payload, data)


def test_object_invalid_create(test_app,
                               valid_object_payload,
                               invalid_object_payload):
    test_app.post_json(object_collection,
                       valid_object_payload,
                       status=200).json

    data = test_app.post_json(object_collection,
                              invalid_object_payload,
                              status=400).json

    assert data


def test_object_list(test_app, valid_object_payload):
    test_app.post_json(object_collection,
                       valid_object_payload,
                       status=200).json

    data = test_app.get(object_collection, status=200).json
    assert len(data) > 1
    assert_response(valid_object_payload, data[0])


def test_object_list_on_not_existing(test_app):
    object_collection = '/types/my_other_nonexisting_type/objects'
    test_app.get(object_collection, status=403).json


def test_object_list_on_deleted(test_app, valid_object_payload):
    test_app.post_json(object_collection,
                       valid_object_payload,
                       status=200)
    data = test_app.get(object_collection, status=200).json
    assert len(data) > 1
    data = test_app.delete(object_collection, status=200).json
    assert_response(valid_object_payload, data[0])
    data = test_app.get(object_collection, status=200).json
    assert len(data) == 0


def test_object_update_creates(test_app, valid_object_payload):
    data = test_app.put_json(object_endpoint.format('42'),
                             valid_object_payload,
                             status=200).json

    assert_response(valid_object_payload, data)
    assert data['id'] == '42'


def test_object_update_overwrites(test_app, valid_object_payload):
    initial_data = test_app.put_json(object_endpoint.format('42'),
                                     valid_object_payload,
                                     status=200).json

    next_payload = valid_object_payload.copy()
    next_payload['banana'] = 'apple'

    data = test_app.put_json(object_endpoint.format('42'),
                             next_payload,
                             status=200).json

    assert_response(valid_object_payload, data)
    assert data['banana'] == 'apple'
    assert data['last_modified'] > initial_data['last_modified']


def test_object_invalid_update(test_app,
                               valid_object_payload,
                               invalid_object_payload):

    test_app.put_json(object_endpoint.format('42'),
                      valid_object_payload,
                      status=200).json

    test_app.put_json(object_endpoint.format('42'),
                      invalid_object_payload,
                      status=400).json


def test_object_patch_edits(test_app, valid_object_payload):
        initial_data = test_app.put_json(object_endpoint.format('42'),
                                         valid_object_payload,
                                         status=200).json

        next_payload = valid_object_payload.copy()
        next_payload = {'banana': 'apple'}

        data = test_app.patch_json(object_endpoint.format('42'),
                                   next_payload,
                                   status=200).json

        assert_response(valid_object_payload, data)
        assert data['banana'] == 'apple'
        assert data['last_modified'] > initial_data['last_modified']


def test_object_delete(test_app, valid_object_payload):
        test_app.put_json(object_endpoint.format('42'),
                          valid_object_payload,
                          status=200)
        data = test_app.delete(object_endpoint.format('42')).json
        assert_response(valid_object_payload, data)


def test_type_create(test_app, valid_type_payload):
    data = test_app.post_json(type_collection,
                              valid_type_payload,
                              status=200).json
    assert_response(valid_type_payload, data)


def test_type_validation(test_app, valid_type_payload,
                         valid_object_payload, invalid_object_payload):
    # Create schema aware type
    test_app.post_json(type_collection,
                       valid_type_payload,
                       status=200).json

    # Try to save with invalid schema
    test_app.post_json(object_collection,
                       invalid_object_payload,
                       status=400).json

    # Try to save with valid schema
    test_app.post_json(object_collection,
                       valid_object_payload,
                       status=200).json


@pytest.mark.xfail
def test_type_migration(test_app, valid_type_payload,
                        valid_object_payload, invalid_object_payload):
    # Infer wrong schema type
    test_app.post_json(object_collection,
                       invalid_object_payload,
                       status=200).json

    # Migrate type
    test_app.put_json(type_endpoint,
                      valid_type_payload,
                      status=200).json

    # Insert with corrent schema type
    test_app.post_json(object_collection,
                       valid_object_payload,
                       status=200).json


def test_admin_get(test_app):
    data = test_app.get(user_endpoint.format('admin')).json
    assert data.get('api_key')
    assert not data.get('password')
    assert not data.get('_password')


def test_user_create(test_app, valid_user_payload):
    data = test_app.post_json(user_collection,
                              valid_user_payload,
                              status=200).json

    assert data.get('api_key')


def test_user_list(test_app, valid_user_payload):
    data = test_app.get(user_collection, status=200).json
    assert len(data) > 0


def test_user_delete(test_app, valid_user_payload):
    initial_data = test_app.post_json(user_collection,
                                      valid_user_payload,
                                      status=200).json

    data = test_app.delete(user_endpoint.format(initial_data['id'])).json
    assert data.get('api_key')
