"""Model provider adapters.

Each submodule registers its provider via ``@register_model(...)``. Submodules
are auto-imported here so dropping a new file into this package is enough to
make the provider available — no edits to this file. Imports are resilient: a
provider whose optional SDK is missing (or that fails to import) is skipped
rather than breaking discovery of the others.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

_log = logging.getLogger(__name__)

for _module in pkgutil.iter_modules(__path__):
    if _module.name.startswith("_"):
        continue
    try:
        importlib.import_module(f"{__name__}.{_module.name}")
    except Exception as exc:
        _log.debug("skipping model adapter %s: %s", _module.name, exc)
