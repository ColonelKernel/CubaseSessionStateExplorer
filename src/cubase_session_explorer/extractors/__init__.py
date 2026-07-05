"""Cubase evidence extractors.

Each extractor turns one artifact type into partial, provenance-tagged state.
They never raise on bad input: they return what they can and record warnings.
The :mod:`cubase_session_explorer.fusion` layer merges their outputs.
"""
