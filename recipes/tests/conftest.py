"""Test bootstrap: stub the optional third-party SDKs that backend.py imports
at module load (google.genai, bs4) so the module imports cleanly in CI/dev
environments that don't have those packages installed. The matcher logic
under test never actually calls into these stubs.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _install_stub_module(name: str, attrs: dict[str, object] | None = None) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# google.genai — backend.py does `from google import genai`
google_pkg = _install_stub_module("google")
genai_stub = _install_stub_module("google.genai")
google_pkg.genai = genai_stub  # type: ignore[attr-defined]
genai_stub.Client = type("Client", (), {})  # placeholder
genai_stub.types = types.SimpleNamespace(GenerateContentConfig=lambda **_kw: None)

# bs4 — backend.py does `from bs4 import BeautifulSoup`
bs4_stub = _install_stub_module("bs4")
bs4_stub.BeautifulSoup = type("BeautifulSoup", (), {})

# Make `import backend` work from tests/ subdirectory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
