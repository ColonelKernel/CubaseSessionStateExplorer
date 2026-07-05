"""Host-side MIDI bridge for the Cubase MIDI Remote state probe.

Listens on the virtual MIDI port that ``cubase-state-probe.js`` writes to,
decodes its CC/SysEx mirror of the selected channel + transport + capability
findings, and writes ``runtime/snapshot.json`` — the artifact consumed by
``cubase_session_explorer.extractors.runtime``.

Requires ``mido`` + a backend (``python-rtmidi``). If ``mido`` is absent, the
script prints install instructions and can also emit a hand-written *template*
snapshot so the rest of the pipeline can be exercised offline.

Usage:
    python runtime/bridge_listener.py --port "Probe Out" --out runtime/snapshot.json
    python runtime/bridge_listener.py --template            # write a demo snapshot
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys

CC_MAP = {7: "volume", 10: "pan", 20: "mute", 21: "solo", 22: "tempo"}


def _template_snapshot() -> dict:
    return {
        "project_name": "Runtime Capture (template)",
        "cubase_version": "15.0.10",
        "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
        "transport": {"tempo": 120.0, "playing": False},
        "channels": [
            {"name": "Lead Vox", "volume_db": -2.5, "pan": 0.0,
             "mute": False, "solo": False, "selected": True},
        ],
        "capability": {
            "selected_channel_name": True,
            "selected_channel_volume": True,
            "selected_channel_pan": True,
            "selected_channel_mute": True,
            "selected_channel_solo": True,
            "transport_tempo": True,
            "quick_controls": True,
            "insert_slots_enumerable": False,
            "arbitrary_vst_params": False,
            "routing_topology": False,
        },
        "note": "Template snapshot (no live Cubase). Demonstrates the runtime "
                "extractor path; capability flags reflect the documented "
                "MIDI Remote API boundary.",
    }


def _cc_to_db(value: int) -> float:
    # 0..127 -> approx -60..+6 dB (display only; observed is normalized).
    return round(-60.0 + (value / 127.0) * 66.0, 2)


def run_listener(port_name: str, out_path: str, seconds: float) -> int:
    try:
        import mido
    except ImportError:
        print("mido not installed. Install with: pip install mido python-rtmidi")
        print("Writing a template snapshot instead so the pipeline can run.")
        _write(out_path, _template_snapshot())
        return 0

    channel = {"name": "Selected", "selected": True}
    transport = {"tempo": None, "playing": False}
    caps: dict = {}
    try:
        inport = mido.open_input(port_name)
    except (OSError, IOError) as exc:
        print(f"Could not open MIDI port {port_name!r}: {exc}")
        print("Available:", mido.get_input_names())
        return 1

    import time
    end = time.time() + seconds
    print(f"Listening on {port_name!r} for {seconds}s ... (select channels in Cubase)")
    for msg in inport:
        if msg.type == "control_change":
            key = CC_MAP.get(msg.control)
            if key == "tempo":
                transport["tempo"] = round(msg.value / 127.0 * 300.0, 1)
                caps["transport_tempo"] = True
            elif key == "volume":
                channel["volume_db"] = _cc_to_db(msg.value)
                caps["selected_channel_volume"] = True
            elif key == "pan":
                channel["pan"] = round((msg.value - 64) / 64.0, 3)
                caps["selected_channel_pan"] = True
            elif key in ("mute", "solo"):
                channel[key] = msg.value >= 64
                caps[f"selected_channel_{key}"] = True
            elif 40 <= msg.control < 48:
                caps["quick_controls"] = True
        elif msg.type == "sysex" and len(msg.data) > 1 and msg.data[0] == 0x7D:
            channel["name"] = "".join(chr(b) for b in msg.data[1:])
            caps["selected_channel_name"] = True
        if time.time() > end:
            break

    snapshot = {
        "project_name": "Runtime Capture",
        "cubase_version": None,
        "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
        "transport": transport,
        "channels": [channel],
        "capability": caps,
    }
    _write(out_path, snapshot)
    return 0


def _write(path: str, snapshot: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2)
    print(f"Runtime snapshot written: {path}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cubase MIDI Remote bridge listener")
    p.add_argument("--port", default="Probe Out")
    p.add_argument("--out", default="runtime/snapshot.json")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--template", action="store_true",
                   help="Write a template snapshot without a live Cubase.")
    args = p.parse_args(argv)
    if args.template:
        _write(args.out, _template_snapshot())
        return 0
    return run_listener(args.port, args.out, args.seconds)


if __name__ == "__main__":
    sys.exit(main())
