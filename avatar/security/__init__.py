"""Content security scanners.

Each submodule registers its scanner via ``@register_scanner(...)``. Submodules
are auto-imported here resiliently, so a new scanner is a single new file.
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
    except Exception as exc:  # noqa: BLE001 - optional adapters may fail to import
        _log.debug("skipping scanner adapter %s: %s", _module.name, exc)
