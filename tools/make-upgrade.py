#!/usr/bin/env python3
# Prospero BlackBox - Make an upgrade file: what changed between two clean copies of a game.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the file that brings a game's base copy from one release to the next.

    make-upgrade.py OLD NEW OUT --game NFSMW --from 1.2 --to 1.3 [--skip speed.exe ...]

OLD and NEW are clean installs of the two releases (names are matched without regard
to case). OUT is a zip in the format src/library/upgrade.hpp reads: upgrade.json and,
for each file that differs or is new, ops that build it from the old file's bytes and
new bytes. Files only in OLD are listed to be removed. --skip leaves files out (the
installer replaces speed.exe anyway). Before OUT is written, applying it to OLD is
checked to give NEW, file by file.

The result carries the publisher's changed bytes: keep it with the other files you put
in mods-cache by hand, never in a public repository. The SHA-256 and size it prints go
in the install plan's sources.
"""
import argparse, hashlib, json, os, struct, sys, zipfile

BLOCK = 32


def files_in(root):
    """{lower-case relative path: (path as spelled, full path)}"""
    found = {}
    for folder, _, names in os.walk(root):
        for name in names:
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found[rel.lower()] = (rel, full)
    return found


def match_length(new, i, old, start):
    """How far new[i:] and old[start:] agree: big steps first, then byte by byte."""
    length, step = 0, 1 << 16
    limit = min(len(new) - i, len(old) - start)
    while step:
        while length + step <= limit and new[i + length:i + length + step] == old[start + length:start + length + step]:
            length += step
        step //= 16
    return length


def ops_for(old, new):
    """Records that build new from old: ('C', offset, length) and ('A', bytes)."""
    if not old:
        return [("A", new)] if new else []
    index = {}
    for at in range(0, len(old) - BLOCK + 1, BLOCK):
        index.setdefault(old[at:at + BLOCK], at)
    ops, literal, i = [], bytearray(), 0
    expected = 0  # where a copy would carry on in the old file: tried first

    def flush():
        if literal:
            ops.append(("A", bytes(literal)))
            literal.clear()

    while i < len(new):
        start = None
        if expected + BLOCK <= len(old) and new[i:i + BLOCK] == old[expected:expected + BLOCK]:
            start = expected
        elif i + BLOCK <= len(new):
            start = index.get(new[i:i + BLOCK])
        if start is None:
            literal.append(new[i])
            i += 1
            continue
        length = match_length(new, i, old, start)
        # Grow back into the literal bytes just before it.
        while literal and start > 0 and literal[-1] == old[start - 1]:
            literal.pop()
            i -= 1
            start -= 1
            length += 1
        flush()
        if ops and ops[-1][0] == "C" and ops[-1][1] + ops[-1][2] == start:
            ops[-1] = ("C", ops[-1][1], ops[-1][2] + length)
        else:
            ops.append(("C", start, length))
        i += length
        expected = start + length
    flush()
    return ops


def encode(ops):
    out = bytearray()
    for op in ops:
        if op[0] == "C":
            out += b"C" + struct.pack("<QQ", op[1], op[2])
        else:
            out += b"A" + struct.pack("<Q", len(op[1])) + op[1]
    return bytes(out)


def apply(old, data):
    out, at = bytearray(), 0
    while at < len(data):
        if data[at:at + 1] == b"C":
            offset, length = struct.unpack_from("<QQ", data, at + 1)
            out += old[offset:offset + length]
            at += 17
        else:
            (length,) = struct.unpack_from("<Q", data, at + 1)
            out += data[at + 9:at + 9 + length]
            at += 9 + length
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("out")
    parser.add_argument("--game", required=True)
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--to", dest="target", required=True)
    parser.add_argument("--skip", action="append", default=[])
    args = parser.parse_args()
    skip = {s.lower() for s in args.skip}
    old_files, new_files = files_in(args.old), files_in(args.new)
    manifest = {"schema": 1, "game": args.game, "from": args.source, "to": args.target,
                "files": [], "remove": []}
    members = []
    for key in sorted(new_files):
        if key in skip:
            continue
        rel, full = new_files[key]
        new = open(full, "rb").read()
        old = open(old_files[key][1], "rb").read() if key in old_files else b""
        if key in old_files and old == new:
            continue
        ops = ops_for(old, new)
        data = encode(ops)
        assert apply(old, data) == new, rel
        name = "ops/%d" % len(members)
        members.append((name, data))
        entry = {"path": old_files[key][0] if key in old_files else rel,
                 "sha256": hashlib.sha256(new).hexdigest(), "size": len(new), "ops": name}
        if key in old_files:
            entry["from_sha256"] = hashlib.sha256(old).hexdigest()
        manifest["files"].append(entry)
        copied = sum(op[2] for op in ops if op[0] == "C")
        print("  %-40s %10d bytes, %5.1f%% from the old file" % (rel, len(new), 100.0 * copied / max(len(new), 1)))
    manifest["remove"] = [old_files[k][0] for k in sorted(old_files) if k not in new_files and k not in skip]
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("upgrade.json", json.dumps(manifest, indent=1) + "\n")
        for name, data in members:
            out.writestr(name, data)
    blob = open(args.out, "rb").read()
    print("%s: %d files changed or new, %d removed" % (args.out, len(manifest["files"]), len(manifest["remove"])))
    print("sha256 %s  size %d" % (hashlib.sha256(blob).hexdigest(), len(blob)))


if __name__ == "__main__":
    sys.exit(main())
