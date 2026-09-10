#!/usr/bin/env python3
"""An OBS CLI fixture with versioned sources and a deliberately moving HEAD."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

path = Path(os.environ["PROMOTION_FIXTURE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append(args)
STAGING = "home:rodrigosbrito:lyra:staging"
PRODUCTION = "home:rodrigosbrito:lyra"
SOURCES = {"1": ("a" * 32, "6.7.1"), "2": ("b" * 32, "6.9"), "3": ("c" * 32, "6.10")}


def save():
    path.write_text(json.dumps(state))


def selected(value):
    if value in (None, "latest"):
        value = state["head"]
    if value in SOURCES:
        return value, SOURCES[value]
    for revision, source in SOURCES.items():
        if source[0] == value:
            return revision, source
    raise SystemExit("unknown source revision")


def revision_option():
    return args[args.index("-r") + 1] if "-r" in args else None


save()
if args[0] == "api":
    if state.get("error") == "metadata":
        raise SystemExit("metadata unavailable")
    url = urlsplit(args[1])
    if url.path == f"/source/{STAGING}/linuxtoys":
        number, (checksum, _) = selected(parse_qs(url.query)["rev"][0])
        assert parse_qs(url.query)["expand"] == ["1"]
        if state["advance_after_metadata"]:
            state["head"] = "3"
            save()
    else:
        assert url.path == f"/source/{PRODUCTION}/linuxtoys"
        number, checksum = "7", "d" * 32
    attrs = {"name": "linuxtoys", "rev": number, "srcmd5": checksum}
    if state.get("error") == "missing_hash":
        del attrs["srcmd5"]
    if state.get("error") == "invalid_hash":
        attrs["srcmd5"] = "latest"
    if state.get("error") == "xml":
        print("<directory")
    else:
        print(ET.tostring(ET.Element("directory", attrs), encoding="unicode"))
elif args[0] == "log":
    # Compatibility with the baseline script's human-readable log parser.
    number, (checksum, _) = selected(None)
    print(f"r{number} | 2026-09-10 | {checksum} | fixture")
    state["head"] = "3"
    save()
elif args[0] == "cat":
    if state.get("error") == "spec":
        raise SystemExit("selected spec unavailable")
    _, (_, version) = selected(revision_option())
    if state.get("error") == "missing_version":
        print("Name: linuxtoys")
    elif state.get("error") == "duplicate_version":
        print("Version: 6.9\nVersion: 6.10")
    else:
        print(f"Name: linuxtoys\nVersion: {version}")
elif args[0] == "rdiff":
    pin = revision_option()
    assert args[-4:] == [PRODUCTION, "linuxtoys", STAGING, "linuxtoys"]
    old, new = pin.split(":")
    assert old == "d" * 32
    _, (_, version) = selected(new)
    print(f"-Version: 6.6.6\n+Version: {version}")
elif args[0] == "submitrequest":
    if state.get("error") == "submit":
        raise SystemExit("submit failed")
    assert args[-4:] == [STAGING, "linuxtoys", PRODUCTION, "linuxtoys"]
    number, (checksum, version) = selected(revision_option())
    state["submitted"] = {"revision": number, "hash": checksum, "version": version,
                          "message": args[args.index("-m") + 1]}
    save()
    print("Warning: unrelated revision 99 was superseded")
    if state.get("error") != "missing_request_id":
        print("created request id 12345")
    if state.get("error") == "multiple_request_ids":
        print("created request id 67890")
elif args[:2] == ["request", "accept"]:
    assert args[-1] == "12345", "wrong request accepted"
    state["accepted"] = args[-1]
    save()
else:
    raise SystemExit(f"unsupported osc fixture command: {args}")
