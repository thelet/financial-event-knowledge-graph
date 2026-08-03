"""§15.3's portable schema subset: what may be asked for, and what an answer must satisfy.

Two functions and one keyword list, and they are the same rule seen from two sides.
`validate_portable_schema` refuses a schema **before** a request is built;
`schema_violations` checks the answer **after** one comes back. They live in their own module
rather than beside the transport because a stage that declares a schema must be able to check
it without importing an HTTP client — and because a build-time refusal is the more important
half, which would be easy to miss inside two hundred lines of retry loop.

**Why the refusal exists at all.** llama.cpp converts the JSON Schema to a GBNF grammar and
**skips unsupported keywords silently**, and the checker below ignores anything outside the
six keywords for the same reason a check that means something other than the grammar is worse
than no check. So a schema using `minimum`, `pattern`, `anyOf`, `$ref`, `prefixItems` or
`minItems` is neither enforced by the server nor caught here: it simply does not apply, and
nothing says so. Failing at build time is the only place that fact can be made visible.

**`description` and `title` are refused too**, and that is a judgment worth recording: they
constrain nothing, so ignoring them would lose nothing — but permitting keywords one at a
time because they seem harmless is exactly how the list stops being a list. Guidance to the
model belongs in the system prompt, where `prompt_version` covers it and the request digest
records it.

Three structural rules beyond the keyword list, all from §15.3:

* every object declares `additionalProperties: false` — an object that accepts extras is
  unconstrained in the one way that matters, since the grammar then permits any key;
* every property of an object is `required` — "an optional property is one the model silently
  omits on the hard cases";
* `enum` is a non-empty list — an empty one admits nothing and would make every answer a
  violation.
"""

from __future__ import annotations

from typing import Any, Mapping

from story.providers.public import StoryProviderConfigurationError

#: The six keywords llama.cpp's GBNF builder honours *and* `schema_violations` enforces. The
#: intersection is the contract: a keyword in one but not the other is a constraint that holds
#: in one place and not the other, which is the ambiguity this list exists to remove.
PORTABLE_KEYWORDS = frozenset({
    "type", "required", "properties", "additionalProperties", "enum", "items"})

#: Types the grammar can express. `null` is absent deliberately: §4.2 records that Neo4j holds
#: no null property, so a story schema offering the model a null to return would invite an
#: answer no fact in the package can support.
PORTABLE_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean"})

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
}


def validate_portable_schema(schema: Mapping[str, Any], *, path: str = "$") -> None:
    """Refuse a schema the local runtime would silently fail to enforce. Recursive.

    Raises `StoryProviderConfigurationError` — the request was never built, so this is neither
    a transport fault nor the model's answer. Returns `None` for a schema that is entirely
    within the subset.
    """
    if not isinstance(schema, Mapping):
        raise StoryProviderConfigurationError(
            f"{path}: schema must be a mapping, got {type(schema).__name__}")

    unsupported = sorted(set(schema) - PORTABLE_KEYWORDS)
    if unsupported:
        raise StoryProviderConfigurationError(
            f"{path}: schema uses {unsupported}, outside the portable subset "
            f"{sorted(PORTABLE_KEYWORDS)} (plan §15.3). llama.cpp's grammar builder drops "
            "those keywords silently and the local checker ignores them, so the request would "
            "be unconstrained without saying so")

    declared = schema.get("type")
    if declared is not None:
        if not isinstance(declared, str) or declared not in PORTABLE_TYPES:
            raise StoryProviderConfigurationError(
                f"{path}: type {declared!r} is not one of {sorted(PORTABLE_TYPES)}; a list of "
                "types is a union the grammar cannot build")

    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise StoryProviderConfigurationError(
            f"{path}: enum must be a non-empty list, got {enum!r}")

    if declared == "object" or "properties" in schema:
        _validate_object(schema, path)

    items = schema.get("items")
    if items is not None:
        validate_portable_schema(items, path=f"{path}[]")
    elif declared == "array":
        raise StoryProviderConfigurationError(
            f"{path}: an array must declare items, or the grammar admits any element")


def _validate_object(schema: Mapping[str, Any], path: str) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        raise StoryProviderConfigurationError(
            f"{path}: an object must declare a non-empty properties mapping")
    if schema.get("additionalProperties") is not False:
        raise StoryProviderConfigurationError(
            f"{path}: an object must declare additionalProperties: false, or the grammar "
            "admits keys nothing in the package can support")

    required = schema.get("required")
    if not isinstance(required, (list, tuple)):
        raise StoryProviderConfigurationError(
            f"{path}: an object must declare required, listing every property")
    if sorted(required) != sorted(properties):
        missing = sorted(set(properties) - set(required))
        unknown = sorted(set(required) - set(properties))
        raise StoryProviderConfigurationError(
            f"{path}: every property must be required (§15.3) — optional: {missing}, "
            f"required but not declared: {unknown}")

    for name, subschema in properties.items():
        validate_portable_schema(subschema, path=f"{path}.{name}")


def schema_violations(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """What an answer failed, as a list of readable findings. Empty means conformant.

    Re-stated from `extraction/providers/local_openai_compatible.py:294` rather than imported
    (§15.2, WORKSTREAM_BOUNDARY §4). Deliberately no JSON Schema library: the grammar already
    enforces the shape, and this exists so that a violation is a *typed* result rather than a
    claim assembled from a missing field.
    """
    findings: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, str):
        allowed = _JSON_TYPES.get(expected)
        if allowed is not None:
            # `bool` subclasses `int`; a boolean is not a number here.
            wrong_type = not isinstance(value, allowed) or (
                isinstance(value, bool) and expected in ("number", "integer"))
            if wrong_type:
                return [f"{path}: expected {expected}, got {type(value).__name__}"]

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        findings.append(f"{path}: {value!r} is not one of {enum}")

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or ():
            if name not in value:
                findings.append(f"{path}: required property {name!r} is missing")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    findings.append(f"{path}: unexpected property {name!r}")
        for name, subschema in properties.items():
            if name in value and isinstance(subschema, Mapping):
                findings.extend(schema_violations(value[name], subschema, f"{path}.{name}"))

    items = schema.get("items")
    if isinstance(value, (list, tuple)) and isinstance(items, Mapping):
        for index, element in enumerate(value):
            findings.extend(schema_violations(element, items, f"{path}[{index}]"))

    return findings
