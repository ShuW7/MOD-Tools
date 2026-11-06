"""Prebuilt AssetBundle Mods described by a MODLAB-style ``mod.ini``.

Such a Mod ships complete ``.ab`` files instead of loose images, so nothing is
rebuilt: every bundle is redirected to the Mod file by ``key_root`` /
``bundle_root``. Example::

    [mod]
    type = assetbundle
    key_root = Arknights\\Arknights_Data\\StreamingAssets\\AB\\Windows\\prefabs\\shop\\shopkeeper
    bundle_root = shopkeeper
    auto_map_by_stem = true

``key_root`` is relative to the directory that contains the game folder (the
``games`` directory in this installation) and may also be relative to the game
folder itself. ``bundle_root`` is the folder inside the Mod that holds the
bundles; with ``auto_map_by_stem`` every file keeps its relative path below
``key_root``. When it is false, ``[map]`` maps a bundle key to a Mod file.
"""
import re
from pathlib import Path, PurePosixPath

MAGIC = (b"UnityFS", b"UnityWeb", b"UnityRaw")
TYPE = "assetbundle"
INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')


def read_ini(file):
    """Small INI reader: ``{section: {key: value}}`` with lowered keys."""
    sections = {}
    current = ""
    try:
        text = Path(file).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return sections
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip().lower()
            sections.setdefault(current, {})
            continue
        key, separator, value = stripped.partition("=")
        if separator:
            sections.setdefault(current, {})[key.strip().lower()] = value.strip()
    return sections


def read_config(root):
    """AssetBundle Mod configuration for a Mod folder, or ``None``."""
    file = Path(root) / "mod.ini"
    if not file.is_file():
        return None
    sections = read_ini(file)
    mod = sections.get("mod", {})
    if mod.get("type", "").lower() != TYPE:
        return None
    key_root = safe_relative(mod.get("key_root", ""))
    bundle_root = safe_relative(mod.get("bundle_root", "")) or ""
    auto = mod.get("auto_map_by_stem", "true").lower() not in ("0", "false", "no", "off")
    mapping = {}
    for key, value in sections.get("map", {}).items():
        if key and value:
            mapping[key] = value
    return {"root": Path(root), "key_root": key_root, "bundle_root": bundle_root, "auto": auto, "map": mapping}


def safe_relative(value, *, allow_empty=True):
    text = str(value or "").replace("\\", "/").strip().strip("/")
    if not text:
        return "" if allow_empty else None
    if text.startswith(("/", "./")) or ":" in text:
        raise ValueError("Invalid Mod-relative path")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("Invalid Mod-relative path")
    return "/".join(parts)


def game_root_of(game_exe):
    return Path(game_exe).resolve().parent


def key_root_relative(game_exe, key_root):
    """Game-folder-relative ``key_root`` without the game folder name."""
    game = game_root_of(game_exe)
    relative = safe_relative(key_root, allow_empty=False)
    parts = relative.split("/")
    if parts and parts[0].casefold() == game.name.casefold():
        parts = parts[1:]
    if not parts or parts[0].casefold() != "arknights_data":
        raise ValueError("key_root must point inside Arknights_Data")
    return "/".join(parts)


def overlay_source(game_exe, game_relative):
    """Downloaded PersistentData copy takes precedence over install data."""
    game = game_root_of(game_exe)
    marker = "Arknights_Data/StreamingAssets/AB/"
    if game_relative.startswith(marker):
        tail = game_relative[len(marker):]
        tail = tail.split("/", 1)[1] if "/" in tail else ""
        overlay = "Arknights_Data/PersistentData/Bundles/" + tail if tail else ""
        if overlay and (game / overlay).is_file():
            return overlay
    return game_relative if (game / game_relative).is_file() else None


def bundle_entries(config):
    """``[{'file', 'key', 'game_relative'}]`` for one Mod."""
    root = Path(config["root"]).resolve()
    bundle_root = (root / config["bundle_root"]).resolve() if config["bundle_root"] else root
    if not bundle_root.is_dir() or not bundle_root.is_relative_to(root):
        raise ValueError("bundle_root is missing or outside the Mod")
    key_root = config["key_root"]
    entries = []
    if config["auto"]:
        for file in sorted(bundle_root.rglob("*.ab")):
            if not file.is_file():
                continue
            key = safe_relative(file.relative_to(bundle_root).as_posix())
            entries.append({"file": file, "key": key, "game_relative": f"{key_root}/{key}" if key_root else key})
    else:
        for key, value in sorted(config["map"].items()):
            file = Path(value)
            target = file if file.is_absolute() else (root / safe_relative(value))
            key = safe_relative(key)
            if not key.lower().endswith(".ab"):
                key += ".ab"
            entries.append({"file": target, "key": key, "game_relative": f"{key_root}/{key}" if key_root else key})
    for entry in entries:
        if not entry["file"].is_file():
            raise ValueError("Missing AssetBundle file: " + entry["file"].name)
        entry["game_relative"] = safe_relative(entry["game_relative"])
    return entries


def valid_bundle(file):
    try:
        with Path(file).open("rb") as stream:
            return stream.read(8).startswith(MAGIC)
    except OSError:
        return False


def plan(config, game_exe):
    """Resolved bundle plans with the current source file for each bundle."""
    resolved = dict(config)
    resolved["key_root"] = key_root_relative(game_exe, config["key_root"])
    entries = bundle_entries(resolved)
    seen = {}
    for entry in entries:
        if entry["game_relative"] in seen:
            raise ValueError("Duplicate AssetBundle target: " + entry["game_relative"])
        source = overlay_source(game_exe, entry["game_relative"])
        entry["source"] = source
        entry["valid"] = valid_bundle(entry["file"])
        seen[entry["game_relative"]] = entry
    return entries
