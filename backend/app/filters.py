"""Validates user-supplied MongoDB filters: only plain query operators are allowed.

Blocked: $where, $function, $accumulator, $expr, $jsonSchema and anything else not on the list,
so a filter can never run JavaScript or aggregation expressions on your server."""

ALLOWED_OPERATORS = {
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$exists", "$type",
    "$and", "$or", "$nor", "$not", "$all", "$elemMatch", "$size", "$regex", "$options", "$mod",
}
MAX_DEPTH = 8
MAX_LIST = 1000
MAX_REGEX = 200


class FilterError(ValueError):
    pass


def validate_filter(value, depth: int = 0) -> dict:
    if depth == 0 and not isinstance(value, dict):
        raise FilterError("filter must be a JSON object")
    if depth > MAX_DEPTH:
        raise FilterError("filter is nested too deeply")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or not k or "\x00" in k:
                raise FilterError("invalid field name in filter")
            if k.startswith("$"):
                if k not in ALLOWED_OPERATORS:
                    raise FilterError(f"operator {k} is not allowed in filters")
                if k == "$regex" and (not isinstance(v, str) or len(v) > MAX_REGEX):
                    raise FilterError(f"$regex must be a string of at most {MAX_REGEX} characters")
            validate_filter(v, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_LIST:
            raise FilterError(f"lists in filters may hold at most {MAX_LIST} values")
        for v in value:
            validate_filter(v, depth + 1)
    elif not (value is None or isinstance(value, (str, int, float, bool))):
        raise FilterError("unsupported value in filter")
    return value
