"""A small JSON-schema subset validator for AI outputs (standard library only).

Supported: type (incl. lists), required, enum, const, properties, additionalProperties (bool or schema), items,
minItems/maxItems, minLength/maxLength, minimum/maximum, pattern. Unknown keywords are ignored.
validate() returns a list of "path: problem" strings ([] = valid).
"""
import re

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(instance, schema, path="$"):
    errors = []
    _check(instance, schema or {}, path, errors)
    return errors


def _check(v, s, path, errors):
    if not isinstance(s, dict):
        return
    t = s.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(x, lambda _: False)(v) for x in types):
            errors.append("%s: expected %s" % (path, "|".join(types)))
            return
    if "enum" in s and v not in s["enum"]:
        errors.append("%s: %r not in enum" % (path, v))
    if "const" in s and v != s["const"]:
        errors.append("%s: must equal %r" % (path, s["const"]))
    if isinstance(v, str):
        if len(v) < s.get("minLength", 0):
            errors.append("%s: shorter than %d" % (path, s["minLength"]))
        if "maxLength" in s and len(v) > s["maxLength"]:
            errors.append("%s: longer than %d" % (path, s["maxLength"]))
        if "pattern" in s and not re.search(s["pattern"], v):
            errors.append("%s: does not match %s" % (path, s["pattern"]))
    if _TYPES["number"](v):
        if "minimum" in s and v < s["minimum"]:
            errors.append("%s: below minimum %s" % (path, s["minimum"]))
        if "maximum" in s and v > s["maximum"]:
            errors.append("%s: above maximum %s" % (path, s["maximum"]))
    if isinstance(v, dict):
        props = s.get("properties", {})
        for key in s.get("required", []):
            if key not in v:
                errors.append("%s: missing required %r" % (path, key))
        extra = s.get("additionalProperties", True)
        for key, val in v.items():
            sub = "%s.%s" % (path, key)
            if key in props:
                _check(val, props[key], sub, errors)
            elif extra is False:
                errors.append("%s: unexpected property" % sub)
            elif isinstance(extra, dict):
                _check(val, extra, sub, errors)
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            errors.append("%s: fewer than %d items" % (path, s["minItems"]))
        if "maxItems" in s and len(v) > s["maxItems"]:
            errors.append("%s: more than %d items" % (path, s["maxItems"]))
        items = s.get("items")
        if isinstance(items, dict):
            for i, val in enumerate(v):
                _check(val, items, "%s[%d]" % (path, i), errors)


# Building blocks for the role schemas.
def string(min_len=0, max_len=None, pattern=None):
    s = {"type": "string"}
    if min_len:
        s["minLength"] = min_len
    if max_len:
        s["maxLength"] = max_len
    if pattern:
        s["pattern"] = pattern
    return s


def array(items, max_items=None, min_items=0):
    s = {"type": "array", "items": items}
    if max_items is not None:
        s["maxItems"] = max_items
    if min_items:
        s["minItems"] = min_items
    return s


def obj(props, required=None, extra=False):
    return {"type": "object", "additionalProperties": extra, "required": list(required if required is not None else props),
            "properties": props}
