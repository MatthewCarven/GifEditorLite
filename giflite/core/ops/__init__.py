"""Operations. Importing this package registers the built-in ops.

`import giflite.core.ops` is enough to populate the registry -- `frames` is
imported for its side effect of registering the five frame ops.
"""

from giflite.core.ops import canvas  # noqa: F401  (registers ops on import)
from giflite.core.ops import frames  # noqa: F401  (registers ops on import)
from giflite.core.ops import paint   # noqa: F401  (registers ops on import)
from giflite.core.ops import timing  # noqa: F401  (registers ops on import)
from giflite.core.ops.registry import (
    Operation,
    OpResult,
    all_ops,
    get_op,
    menu_groups,
    op_defaults,
    op_label,
    op_params,
    register_op,
)

__all__ = [
    "Operation",
    "OpResult",
    "all_ops",
    "get_op",
    "menu_groups",
    "op_defaults",
    "op_label",
    "op_params",
    "register_op",
]
