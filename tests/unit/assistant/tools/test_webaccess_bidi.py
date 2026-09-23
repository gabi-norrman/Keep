import pytest
from assistant.tools.WebAccess import WebAccess


@pytest.mark.unit
class TestBidiToPython:

    def test_string_type(self):
        assert WebAccess._bidi_to_python({'type': 'string', 'value': 'hello'}) == 'hello'

    def test_array_type(self):
        node = {
            'type': 'array',
            'value': [
                {'type': 'number', 'value': 1},
                {'type': 'string', 'value': 'two'},
            ]
        }
        assert WebAccess._bidi_to_python(node) == [1, 'two']

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
