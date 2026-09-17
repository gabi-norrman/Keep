import pytest
from assistant.tools.WebAccess import WebAccess


@pytest.mark.unit
class TestBidiToPython:

    def test_none_input(self):
        assert WebAccess._bidi_to_python(None) is None

    def test_null_type(self):
        assert WebAccess._bidi_to_python({'type': 'null', 'value': None}) is None

    def test_undefined_type(self):
        assert WebAccess._bidi_to_python({'type': 'undefined', 'value': None}) is None

    def test_string_type(self):
        assert WebAccess._bidi_to_python({'type': 'string', 'value': 'hello'}) == 'hello'

    def test_number_type(self):
        assert WebAccess._bidi_to_python({'type': 'number', 'value': 42}) == 42

    def test_boolean_type(self):
        assert WebAccess._bidi_to_python({'type': 'boolean', 'value': True}) is True

    def test_bigint_type(self):
        assert WebAccess._bidi_to_python({'type': 'bigint', 'value': 99999999999}) == 99999999999

    def test_array_type(self):
        node = {
            'type': 'array',
            'value': [
                {'type': 'number', 'value': 1},
                {'type': 'string', 'value': 'two'},
            ]
        }
        assert WebAccess._bidi_to_python(node) == [1, 'two']

    def test_empty_array(self):
        assert WebAccess._bidi_to_python({'type': 'array', 'value': []}) == []

    def test_array_with_none_value(self):
        assert WebAccess._bidi_to_python({'type': 'array', 'value': None}) == []

    def test_object_type(self):
        node = {
            'type': 'object',
            'value': [
                ('name', {'type': 'string', 'value': 'Alice'}),
                ('age', {'type': 'number', 'value': 30}),
            ]
        }
        assert WebAccess._bidi_to_python(node) == {'name': 'Alice', 'age': 30}

    def test_empty_object(self):
        assert WebAccess._bidi_to_python({'type': 'object', 'value': []}) == {}

    def test_object_with_none_value(self):
        assert WebAccess._bidi_to_python({'type': 'object', 'value': None}) == {}

    def test_nested_object_in_array(self):
        node = {
            'type': 'array',
            'value': [
                {
                    'type': 'object',
                    'value': [('key', {'type': 'string', 'value': 'val'})]
                },
            ]
        }
        assert WebAccess._bidi_to_python(node) == [{'key': 'val'}]

    def test_dataclass_style_node(self):
        """nodriver top-level nodes are dataclass objects, not dicts."""
        class FakeNode:
            type_ = 'string'
            value = 'from dataclass'
        assert WebAccess._bidi_to_python(FakeNode()) == 'from dataclass'

    def test_dataclass_null_node(self):
        class FakeNode:
            type_ = 'null'
            value = None
        assert WebAccess._bidi_to_python(FakeNode()) is None

    def test_unknown_type_returns_value(self):
        assert WebAccess._bidi_to_python({'type': 'date', 'value': '2025-01-01'}) == '2025-01-01'

    def test_object_skips_malformed_entries(self):
        node = {
            'type': 'object',
            'value': [
                ('good', {'type': 'number', 'value': 1}),
                ('too', 'many', 'items'),
                ('ok', {'type': 'number', 'value': 2}),
            ]
        }
        result = WebAccess._bidi_to_python(node)
        assert result == {'good': 1, 'ok': 2}
