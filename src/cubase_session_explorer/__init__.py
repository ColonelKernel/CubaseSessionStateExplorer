"""Cubase Session State Explorer.

The Cubase adapter in the cross-DAW Session State Explorer family (REAPER,
Ableton Live, Logic Pro, Cubase). Turns partially-observable Cubase evidence —
DAWproject exports, Track Archives, the binary .cpr, MIDI, presets and MIDI
Remote runtime captures — into an interpretable, provenance-tracked session
graph, and links state changes to acoustic outcomes through controlled
interventions.

Primary entry points:
    fusion.ingest(path)        -> FusionResult(session=SessionState, ...)
    graph_builder.build_graph_dict(session)
    diff.diff_sessions(a, b)
    cli.main()                 (`cubase-explorer`)
"""

__version__ = "0.1.0"
SCHEMA_VERSION = "0.1.0"
DIALECT = "cubase"
