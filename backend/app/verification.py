"""Access to the verification stack that lives in ``tools/``.

The database verification tools (``tools/verified_db.py``, ``tools/history_archive.py``)
are deliberately *not* part of the shipped application package: they are the
independent layer that checks whether a database or a backup still matches its
recorded baseline, and they must stay usable on their own. The application therefore
loads them by file path instead of importing them, which also keeps ``tools/`` free to
depend on ``app`` in the other direction if it ever needs to.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"


class MissingToolError(RuntimeError):
    """A verification tool the application depends on is not where it should be."""


def load_tool(name: str) -> ModuleType:
    """Load ``tools/<name>.py`` as a module, or raise ``MissingToolError``.

    Loaded under its own name so repeated calls share one module object, exactly as
    the pytest suite loads them.
    """
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__file__", "").startswith(str(TOOLS_DIR)):
        return existing
    path = TOOLS_DIR / f"{name}.py"
    if not path.is_file():
        raise MissingToolError(f"verification tool not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise MissingToolError(f"verification tool cannot be loaded: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[name] = module
    return module
