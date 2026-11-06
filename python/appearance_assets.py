"""Avatar and portrait appearance assets of the Arknights PC client.

The PC client stores avatars in ``spritepack/ui_char_avatar_<page>.ab`` and
half-body portraits in ``spritepack/char_portrait_<page>.ab``.  A page bundle is
a flat pack: every asset is its own ``Texture2D`` plus a ``Sprite``, exposed by
a container path such as ``dyn/arts/charavatars/elite/char_002_amiya_2.png``.
Replacing one character therefore means rebuilding the page bundle that owns
the asset, never compositing an atlas.

The published hub assets (``arts/charavatars/avatar_hub.ab``) list packing
settings, but the actual pack order does not follow their key order and hot
updates append to existing pages.  The owning page is therefore resolved from
the container tables themselves; that lookup only has to decompress the block
data, not decode any texture.
"""
import re
from pathlib import Path, PurePosixPath

FAMILIES = {
    "avatar": {
        "directory": "charavatars",
        "prefix": "charavatars",
        "bundle": "spritepack/ui_char_avatar_{page}.ab",
    },
    "portrait": {
        "directory": "charportraits",
        "prefix": "charportraits",
        "bundle": "spritepack/char_portrait_{page}.ab",
    },
}
DIRECTORIES = {meta["directory"]: name for name, meta in FAMILIES.items()}
PREFIXES = {meta["prefix"]: name for name, meta in FAMILIES.items()}
# Character, summon and NPC asset names always start with one of these markers.
# The optional folder prefix in front of it (elite/, skins/, ...) is version
# metadata, so it is parsed out instead of enumerated.
ASSET_START = re.compile(r"(?:sp_char_|char_|token_|trap_|npc_|avg_)", re.I)
PAGE_INDEX = re.compile(r"_(\d+)\.ab$", re.I)
_CACHE = {}


def family_of_container(container):
    """Return the appearance family a bundle container path belongs to."""
    if not isinstance(container, str):
        return None
    lowered = "/" + container.replace("\\", "/").lower().lstrip("/")
    for directory, family in DIRECTORIES.items():
        if f"/{directory}/" in lowered:
            return family
    return None


def container_parts(container):
    """Split a container path into ``(family, folder, asset)``.

    ``folder`` is the sub directory below the family root and may be empty;
    ``asset`` is the file stem, for example ``char_002_amiya_2``.
    """
    family = family_of_container(container)
    if not family:
        return None
    parts = [part for part in container.replace("\\", "/").split("/") if part]
    lowered = [part.lower() for part in parts]
    index = lowered.index(FAMILIES[family]["directory"])
    return family, "_".join(parts[index + 1:-1]), PurePosixPath(parts[-1]).stem


def target_name(container):
    """Canonical Mod resource name for one appearance container."""
    parsed = container_parts(container)
    if not parsed:
        return None
    family, folder, asset = parsed
    return FAMILIES[family]["prefix"] + ("_" + folder if folder else "") + "_" + asset + ".png"


def parse_target(value):
    """Inverse of :func:`target_name`; returns ``(family, folder, asset)``."""
    if not isinstance(value, str):
        return None
    name = PurePosixPath(value.replace("\\", "/")).name
    if not name.lower().endswith(".png"):
        return None
    stem = name[:-4]
    lowered = stem.lower()
    family = next((family for prefix, family in PREFIXES.items() if lowered.startswith(prefix + "_")), None)
    if not family:
        return None
    rest = stem[len(FAMILIES[family]["prefix"]) + 1:]
    match = ASSET_START.search(rest)
    if not match:
        return None
    return family, rest[: match.start()].strip("_"), rest[match.start():]


def target_from_source(relative):
    """Canonical target for a Mod file that keeps the game folder layout.

    ``charavatars/elite/char_002_amiya_2.png`` becomes
    ``charavatars_elite_char_002_amiya_2.png``.  Files outside an appearance
    folder are not claimed here.
    """
    parts = [part for part in str(relative).replace("\\", "/").split("/") if part]
    for index, part in enumerate(parts[:-1]):
        family = DIRECTORIES.get(part.lower())
        if not family:
            continue
        folder = "_".join(parts[index + 1:-1])
        stem = PurePosixPath(parts[-1]).stem
        return FAMILIES[family]["prefix"] + ("_" + folder if folder else "") + "_" + stem + ".png"
    return None


def identity(value):
    """Best effort ``(family, folder, asset)`` for a target or bare name."""
    parsed = parse_target(value)
    if parsed:
        return parsed
    name = PurePosixPath(str(value).replace("\\", "/")).name
    match = ASSET_START.search(name)
    if not match:
        return None
    return None, "", PurePosixPath(name[match.start():]).stem


def id_matches(asset, identifiers):
    """Whether an appearance asset belongs to one of the operator identities."""
    low = asset.lower()
    for identifier in identifiers:
        identifier = identifier.lower()
        if low == identifier or low.startswith(identifier + "_") or low.startswith(identifier + "#"):
            return True
        # Summons keep their owner's short code inside a token_/trap_ name.
        code = identifier.split("_", 2)[-1]
        if code and low.startswith(("token_", "trap_")) and f"_{code}" in low:
            return True
    return False


def page_sort_key(relative):
    match = PAGE_INDEX.search(PurePosixPath(relative).name)
    return (0, int(match.group(1))) if match else (1, 0)


def _register_lzham():
    """Teach UnityPy the block format used by every Arknights PC bundle."""
    import UnityPy
    from UnityPy.enums import CompressionFlags
    from UnityPy.helpers import CompressionHelper
    from lz4ak import decompress_lz4ak

    registered = CompressionHelper.DECOMPRESSION_MAP.get(CompressionFlags.LZHAM)
    if getattr(registered, "__name__", "") != "decompress_lz4ak":
        CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak
    return UnityPy


def read_page(file):
    """Read one page bundle's container table without decoding textures.

    Returns ``[(container, family, folder, asset, target, path_id)]``.
    """
    UnityPy = _register_lzham()
    env = UnityPy.load(str(file))
    bundle = None
    textures = {}
    for asset in env.assets:
        for path_id, obj in asset.objects.items():
            if obj.type.name == "AssetBundle" and bundle is None:
                bundle = obj.read()
            elif obj.type.name == "Texture2D":
                textures[path_id] = obj
    if bundle is None:
        return []
    records = []
    seen = set()
    for container, info in bundle.m_Container:
        parsed = container_parts(container)
        if not parsed:
            continue
        path_id = None
        for pointer in bundle.m_PreloadTable[info.preloadIndex:info.preloadIndex + info.preloadSize]:
            if pointer.m_FileID == 0 and pointer.m_PathID in textures:
                path_id = pointer.m_PathID
                break
        if path_id is None:
            continue
        family, folder, asset = parsed
        # A container is listed once per preloaded object (Sprite and texture).
        if (container, path_id) in seen:
            continue
        seen.add((container, path_id))
        records.append((container, family, folder, asset, target_name(container), path_id))
    return records


def inventory(roots):
    """List appearance page bundles; downloaded data takes precedence."""
    found = {}
    for root_value in roots:
        root = Path(root_value)
        if not root.is_dir():
            continue
        for meta in FAMILIES.values():
            for file in root.glob(meta["bundle"].format(page="*")):
                if not file.is_file():
                    continue
                relative = file.relative_to(root).as_posix()
                stat = file.stat()
                found.setdefault(relative.lower(), (relative, file, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(found.values(), key=lambda item: (page_sort_key(item[0]), item[0].lower())))


def entries(roots):
    """Every appearance asset reachable from the selected game installation."""
    pages = inventory(roots)
    signature = tuple((item[0], item[2], item[3]) for item in pages)
    cached = _CACHE.get(signature)
    if cached is not None:
        return cached
    result = []
    for relative, file, _size, _mtime in pages:
        try:
            records = read_page(file)
        except Exception:
            # One damaged hot update must not disable the other pages.
            continue
        for container, family, folder, asset, target, path_id in records:
            result.append({
                "bundle": relative,
                "container": container,
                "family": family,
                "folder": folder,
                "asset": asset,
                "target": target,
                "pathId": path_id,
            })
    if len(_CACHE) >= 4:
        _CACHE.clear()
    _CACHE[signature] = result
    return result


def clear_cache():
    _CACHE.clear()


def resolve_bundles(roots, identifiers=(), targets=(), source_items=()):
    """Page bundles that hold the requested appearance assets.

    ``targets`` are canonical resource names, ``identifiers`` are operator ids
    (``char_002_amiya``) and ``source_items`` are Mod relative paths that may
    keep the game folder layout.
    """
    identifiers = [value for value in identifiers if isinstance(value, str) and value]
    wanted = set()
    for value in list(targets) + [target_from_source(value) for value in source_items]:
        parsed = parse_target(value) if value else None
        if parsed:
            wanted.add((parsed[0], parsed[2].lower()))
    if not identifiers and not wanted:
        return []
    pages = {item[0].lower(): (item[0], item[1]) for item in inventory(roots)}
    selected = {}
    for entry in entries(roots):
        if (entry["family"], entry["asset"].lower()) in wanted or (identifiers and id_matches(entry["asset"], identifiers)):
            key = entry["bundle"].lower()
            if key not in selected and key in pages:
                selected[key] = pages[key]
    return [selected[key] for key in sorted(selected, key=lambda value: (page_sort_key(value), value))]


def appearance_bundles(roots, identifiers=(), requested=()):
    """Adapter used by :mod:`pc_layout` for the current resource request."""
    return resolve_bundles(roots, identifiers, requested, ())
