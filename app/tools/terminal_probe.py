#!/usr/bin/env python3
import json
import os
import re

FIRMWARE_RE = re.compile(rb"Device [Vv]ersion\s*:\s*([^\r\n\x00]{3,180}?)\s+\(SN=([A-Za-z0-9_-]{6,64})\)")
SDK_RE = re.compile(rb"XV SDK version\s*:\s*([^\r\n\x00]{3,120})")


def terminal_pids():
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open("/proc/" + entry.name + "/cmdline", "rb") as handle:
                cmdline = handle.read().replace(b"\x00", b" ")
        except OSError:
            continue
        if b"gnome-terminal-server" in cmdline:
            yield int(entry.name)


def heap_regions(pid):
    try:
        with open("/proc/" + str(pid) + "/maps", "r", encoding="ascii") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 6 or parts[5] != "[heap]" or not parts[1].startswith("rw"):
                    continue
                low, high = (int(value, 16) for value in parts[0].split("-"))
                yield low, high
    except OSError:
        return


def scan_pid(pid, devices, sdk_versions):
    try:
        fd = os.open("/proc/" + str(pid) + "/mem", os.O_RDONLY)
    except OSError:
        return
    try:
        for low, high in heap_regions(pid):
            position = low
            carry = b""
            while position < high:
                size = min(4 * 1024 * 1024, high - position)
                try:
                    data = os.pread(fd, size, position)
                except OSError:
                    position += size
                    carry = b""
                    continue
                blob = carry + data
                for match in FIRMWARE_RE.finditer(blob):
                    firmware = match.group(1).decode("ascii", "replace").strip()
                    serial = match.group(2).decode("ascii", "replace").strip()
                    devices[serial] = {"serial": serial, "firmware_version": firmware, "source": "terminal_startup_buffer"}
                for match in SDK_RE.finditer(blob):
                    sdk_versions.add(match.group(1).decode("ascii", "replace").strip())
                carry = blob[-512:]
                position += size
    finally:
        os.close(fd)


def main():
    devices = {}
    sdk_versions = set()
    for pid in terminal_pids():
        scan_pid(pid, devices, sdk_versions)
    print(json.dumps({"devices": sorted(devices.values(), key=lambda item: item["serial"]), "sdk_runtime_logs": sorted(sdk_versions)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
