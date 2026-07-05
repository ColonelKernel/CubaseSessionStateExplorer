"""Canonical-export adapter: native Cubase evidence → v0.2 snapshot bundles.

Relocated from the analyzer's ``session_explorer.drivers.cubase`` (origin
commit ``SessionStateExplorer@041f529``) per the pivot plan: the four DAW
explorers stay independent observation instruments; each grows an
``export-canonical`` CLI verb emitting the shared 5-file bundle
(``adapter_descriptor.json``, ``capabilities.json``, ``native.json``,
``canonical.snapshot.json``, ``validation.json``) that the Session State
Analyzer consumes. No analysis code lives here; no acquisition code lives
there.
"""

from .exporter import export_bundle
from .mapper import session_state_to_canonical, to_canonical, to_native

__all__ = [
    "export_bundle",
    "session_state_to_canonical",
    "to_canonical",
    "to_native",
]
