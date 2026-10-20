"""The common ground of the call syntaxes that name their parameters: the JSON function schema a declaration renders to, and the signature table that puts the named values back in Keep's positional order.

Qwen's `<tool_call>` block and DeepSeek's DSML block both call a function by name with named
parameters, and both were trained on tools declared as JSON schemas. Keep's commands take
positional strings, so a format of this kind maps each parameter name to its position through
the signature table (`Signatures.table`, read from the declarations in the texts); a command
or a parameter the table does not know keeps the order the model wrote. The last parameter of
a variadic declaration (`load_toolkit(name: str, ...)`) is one array-typed parameter whose
value — a JSON list, or names separated by commas — is split back into the positional values.
"""
import json

from common.logging import trace

from .CommandFormat import CommandFormat
from .Signatures import Declaration

_JSON_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}


def argument_text(value) -> str:
    """A parsed argument value as the model would have written it: a string as is, a number
    bare, a boolean `true`/`false`, a list or object as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def split_variadic(text: str) -> list[str]:
    """The values of a variadic parameter: a JSON list's items, else the comma-separated names."""
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            items = json.loads(stripped)
            if isinstance(items, list):
                return [argument_text(item) for item in items]
        except ValueError:
            pass
    return [item.strip() for item in stripped.split(",") if item.strip()]


class NamedParameterFormat(CommandFormat):
    """A format whose calls name their parameters: declarations render as JSON function
    schemas, the named values of a call are ordered through the signature table."""

    def __init__(self, possible_commands: list[str], table: dict[str, list[str]] | None = None,
                 variadic: set[str] | None = None):
        """`table` is `command → [parameter names]` and `variadic` the commands whose last
        parameter takes a list; both are read from the texts (`Signatures`) when not given."""
        super().__init__(possible_commands)
        self._table = table
        self._variadic = variadic if variadic is not None or table is None else set()

    def function_schema(self, declaration: Declaration) -> dict:
        """The `{"type": "function", "function": {...}}` schema of a declaration: the four
        python types map to JSON schema types, any other type text (`YYYY-MM-DD`,
        `open|close|0-100`) is a string described by that text, a default makes the parameter
        optional, the variadic last parameter is an array."""
        properties, required = {}, []
        last = len(declaration.params) - 1
        for i, parameter in enumerate(declaration.params):
            schema = self._schema(parameter.type)
            if declaration.variadic and i == last:
                schema = {"type": "array", "items": schema}
            if parameter.default is None:
                required.append(parameter.name)
            else:
                schema["default"] = self._default(parameter.default)
            properties[parameter.name] = schema
        parameters = {"type": "object"}
        if required:
            parameters["required"] = required
        parameters["properties"] = properties
        return {"type": "function", "function": {"name": declaration.command, "parameters": parameters}}

    def render_declaration(self, declaration: Declaration) -> str:
        return "`" + json.dumps(self.function_schema(declaration), ensure_ascii=False) + "`"

    @staticmethod
    def _schema(type_text: str) -> dict:
        if type_text in _JSON_TYPES:
            return {"type": _JSON_TYPES[type_text]}
        return {"type": "string", "description": type_text}

    @staticmethod
    def _default(text: str):
        try:
            return json.loads(text)
        except ValueError:
            return text

    def _positional(self, name: str, arguments: list[tuple[str, str]]) -> list[str]:
        """The positional parameters of a call: in the declared order when the table knows
        the command and every parameter the model named, else in the model's order. A
        declared parameter left out before a given one is passed as an empty string; the
        trailing ones left out are dropped. A variadic last parameter is split into its values."""
        names = self._names_of(name)
        given = dict(arguments)
        if names is None or any(key not in names for key in given):
            how = "an undeclared command" if names is None else "parameter(s) outside the declaration"
            trace(f"{self.name} format: {name} parameters taken in the model's order ({how}): {list(given)}")
            return [value for _, value in arguments]
        present = [i for i, key in enumerate(names) if key in given]
        params = [given.get(key, "") for key in names[:present[-1] + 1]] if present else []
        if self._is_variadic(name) and params and len(params) == len(names):
            params = params[:-1] + split_variadic(params[-1])
        trace(f"{self.name} format: {name} parameters ordered by the signature table: {list(given)} → {len(params)} value(s)")
        return params

    def _names_of(self, name: str) -> list[str] | None:
        if self._table is None:
            from . import Signatures
            self._table = Signatures.table()
        return self._table.get(name)

    def _is_variadic(self, name: str) -> bool:
        if self._variadic is None:
            from . import Signatures
            self._variadic = {command for command, d in Signatures.declarations().items() if d.variadic}
        return name in self._variadic
