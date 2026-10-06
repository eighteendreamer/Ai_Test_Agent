"""Read literal evidence paths without losing present null, false or zero."""
from copy import deepcopy
from typing import Any


def literal_value(value: Any, tokens: list[str]) -> Any:
    for token in tokens:
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list):
            try:
                value = value[int(token)]
            except (ValueError, IndexError):
                raise ValueError("执行证据的数组位置不存在") from None
        else:
            raise ValueError("执行证据或固定预期的位置不存在")
    return deepcopy(value)
