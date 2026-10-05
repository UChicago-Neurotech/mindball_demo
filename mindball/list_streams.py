"""Print every LSL stream visible from this machine — the first thing to run when debugging.

    python -m mindball.list_streams
"""

from __future__ import annotations

import pylsl

from .streams import channel_labels


def main() -> None:
    print("Looking for LSL streams (3 s)...")
    infos = pylsl.resolve_streams(wait_time=3.0)
    if not infos:
        print(
            "No streams found.\n"
            "  - Is the X.on app running with signal monitoring/streaming started?\n"
            "  - If streaming from a phone: same network as this PC? Campus WiFi often blocks\n"
            "    LSL discovery; use a hotspot/dedicated router (see README)."
        )
        return
    for info in sorted(infos, key=lambda i: i.name()):
        print(
            f"\n{info.name()}\n"
            f"  type={info.type()}  channels={info.channel_count()}  rate={info.nominal_srate():g} Hz\n"
            f"  host={info.hostname()}  source_id={info.source_id()}"
        )
        if info.type() == "EEG":
            try:
                full = pylsl.StreamInlet(info).info(timeout=2.0)
                print(f"  labels: {' '.join(channel_labels(full))}")
            except Exception:  # timeout / lost
                print("  labels: (timed out reading stream header)")


if __name__ == "__main__":
    main()
