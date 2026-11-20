#!/usr/bin/env python3
"""Inspect a UE4 Android APK without third-party Python packages."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path


def read_u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def read_u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def length8(buf: bytes, pos: int) -> tuple[int, int]:
    first = buf[pos]
    if first & 0x80:
        return ((first & 0x7F) << 8) | buf[pos + 1], pos + 2
    return first, pos + 1


def length16(buf: bytes, pos: int) -> tuple[int, int]:
    first = read_u16(buf, pos)
    if first & 0x8000:
        return ((first & 0x7FFF) << 16) | read_u16(buf, pos + 2), pos + 4
    return first, pos + 2


def parse_string_pool(data: bytes, off: int, header_size: int, chunk_size: int) -> list[str]:
    count = read_u32(data, off + 8)
    flags = read_u32(data, off + 16)
    strings_start = read_u32(data, off + 20)
    utf8 = bool(flags & 0x100)
    offsets = [read_u32(data, off + header_size + i * 4) for i in range(count)]
    pool = data[off + strings_start : off + chunk_size]
    result = []
    for string_off in offsets:
        if utf8:
            _, pos = length8(pool, string_off)
            byte_len, pos = length8(pool, pos)
            raw = pool[pos : pos + byte_len]
            result.append(raw.decode("utf-8", "replace"))
        else:
            char_len, pos = length16(pool, string_off)
            raw = pool[pos : pos + char_len * 2]
            result.append(raw.decode("utf-16le", "replace"))
    return result


def parse_manifest(data: bytes) -> dict:
    if len(data) < 8 or read_u16(data, 0) != 0x0003:
        raise ValueError("AndroidManifest.xml is not binary Android XML")
    strings: list[str] = []
    result: dict = {"components": [], "metadata": {}}
    offset = 8

    while offset + 8 <= len(data):
        chunk_type = read_u16(data, offset)
        header_size = read_u16(data, offset + 2)
        chunk_size = read_u32(data, offset + 4)
        if chunk_size < 8 or offset + chunk_size > len(data):
            raise ValueError("Invalid binary XML chunk size")

        if chunk_type == 0x0001:
            strings = parse_string_pool(data, offset, header_size, chunk_size)

        elif chunk_type == 0x0102:  # START_TAG
            ext = offset + 16
            name_idx = read_u32(data, ext + 4)
            tag = strings[name_idx] if name_idx < len(strings) else str(name_idx)
            attr_start = read_u16(data, ext + 8)
            attr_size = read_u16(data, ext + 10)
            attr_count = read_u16(data, ext + 12)
            attrs = {}
            attr_off = ext + attr_start

            for i in range(attr_count):
                pos = attr_off + i * attr_size
                name_idx = read_u32(data, pos + 4)
                raw_idx = read_u32(data, pos + 8)
                value_type = data[pos + 15]
                value_data = read_u32(data, pos + 16)
                key = strings[name_idx] if name_idx < len(strings) else str(name_idx)

                if raw_idx != 0xFFFFFFFF and raw_idx < len(strings):
                    value = strings[raw_idx]
                elif value_type == 0x03 and value_data < len(strings):
                    value = strings[value_data]
                elif value_type == 0x10:
                    value = value_data
                elif value_type == 0x12:
                    value = bool(value_data)
                else:
                    value = value_data
                attrs[key] = value

            if tag == "manifest":
                result.update({k: attrs[k] for k in ("package", "versionCode", "versionName") if k in attrs})
            elif tag == "uses-sdk":
                result["minSdkVersion"] = attrs.get("minSdkVersion")
                result["targetSdkVersion"] = attrs.get("targetSdkVersion")
            elif tag in ("activity", "activity-alias", "service", "receiver", "provider"):
                result["components"].append({"type": tag, **attrs})
            elif tag == "meta-data":
                name = attrs.get("name")
                if name:
                    result["metadata"][str(name)] = attrs.get("value", attrs.get("resource"))

        offset += chunk_size

    return result


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} /path/to/game.apk", file=sys.stderr)
        return 2

    apk = Path(sys.argv[1])
    if not apk.is_file():
        print(f"APK not found: {apk}", file=sys.stderr)
        return 2

    digest = hashlib.sha256()
    with apk.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)

    try:
        with zipfile.ZipFile(apk) as archive:
            bad_entry = archive.testzip()
            names = archive.namelist()
            if bad_entry:
                raise ValueError(f"Corrupt ZIP entry: {bad_entry}")
            manifest = parse_manifest(archive.read("AndroidManifest.xml"))
            native_abis = sorted({
                name.split("/")[1]
                for name in names
                if name.startswith("lib/") and name.count("/") >= 2 and name.endswith(".so")
            })
            ue4_abis = sorted({
                name.split("/")[1]
                for name in names
                if name.endswith("/libUE4.so") and name.startswith("lib/")
            })
            resource_packages = sorted(
                name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                for name in names
                if name.startswith("assets/res_base/package/") and name.count("/") == 3
            )
            command_line = ""
            if "assets/UE4CommandLine.txt" in names:
                command_line = archive.read("assets/UE4CommandLine.txt").decode("utf-8", "replace").strip()

    except (zipfile.BadZipFile, KeyError, ValueError, struct.error) as exc:
        print(f"APK inspection failed: {exc}", file=sys.stderr)
        return 1

    output = {
        "file": str(apk),
        "size_bytes": apk.stat().st_size,
        "sha256": digest.hexdigest(),
        "zip_entries": len(names),
        "zip_integrity": "ok",
        "package": manifest.get("package"),
        "version_name": manifest.get("versionName"),
        "version_code": manifest.get("versionCode"),
        "min_sdk": manifest.get("minSdkVersion"),
        "target_sdk": manifest.get("targetSdkVersion"),
        "native_abis": native_abis,
        "ue4_native_abis": ue4_abis,
        "ue4_engine_version": manifest.get("metadata", {}).get("com.epicgames.ue4.GameActivity.EngineVersion"),
        "ue4_project": manifest.get("metadata", {}).get("com.epicgames.ue4.GameActivity.ProjectName"),
        "ue4_cooked_flavors": manifest.get("metadata", {}).get("com.epicgames.ue4.GameActivity.CookedFlavors"),
        "launch_activity": next(
            (c.get("name") for c in manifest["components"]
             if c["type"] == "activity" and c.get("name") == "com.epicgames.ue4.SplashActivity"),
            None,
        ),
        "resource_packages": resource_packages,
        "ue4_command_line": command_line,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
