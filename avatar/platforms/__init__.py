"""Platform adapters.

Each submodule registers its platform via ``@register_platform(...)``. Submodules
are auto-imported here (resiliently — a missing optional SDK skips just that
adapter), so adding a new platform is a single new file in this package.
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
        _log.debug("skipping platform adapter %s: %s", _module.name, exc)
