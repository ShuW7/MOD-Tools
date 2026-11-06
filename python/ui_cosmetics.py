"""Name card styles and home themes of the Arknights PC client.

Both cosmetics are packed one bundle per style/theme:

* ``arts/ui/namecardskin/[uc]nc_<style>.ab`` holds the name card artwork used
  by the profile card: ``bg``, ``name_card_long``, ``name_card_short``, ``sdk``
  and, for animated styles, extra effect textures.
* ``arts/ui/hometheme/[uc]tm_<theme>.ab`` holds the home screen skin of one
  theme: buttons, icons, banners and effect textures (16 to 137 per theme).

Every texture is its own ``Texture2D`` with a ``Sprite``; replacing one means
rebuilding that style/theme bundle, exactly like the avatar/portrait page packs.
Unlike avatars, the bundles are addressed by the style/theme id in their own
file name, so a resource target also names the bundle directly.
"""
import re
from pathlib import Path, PurePosixPath

FAMILIES = {
    "namecard": {
        "directory": "arts/ui/namecardskin",
        "prefix": "namecardskin",
        "id_prefix": "nc_",
        "bundle": "arts/ui/namecardskin/[uc]{id}.ab",
    },
    "hometheme": {
        "directory": "arts/ui/hometheme",
        "prefix": "hometheme",
        "id_prefix": "tm_",
        "bundle": "arts/ui/hometheme/[uc]{id}.ab",
    },
}
PREFIXES = {meta["prefix"]: name for name, meta in FAMILIES.items()}
# Community Mods and older asset dumps name the same bundles differently.
ALIAS_PREFIXES = {"namecardstyle": "namecard", "namecardskin": "namecard", "hometheme": "hometheme"}
# Legacy/mobile texture names used by the game configuration fields
# (_largeBg, _shareUseLargeBg) and by imported Mods.  ``(role, group)``: a
# grouped alias applies to every texture whose role equals ``role`` or starts
# with ``<role>_`` (for example shareBg -> sdk_0..4), an exact alias only to the
# single role (largebg -> bg).
ROLE_ALIASES = {
    "namecard": {
        "largebg": ("bg", False),
        "sharebg": ("sdk", True),
        "longbg": ("name_card_long", True),
        "shortbg": ("name_card_short", True),
    },
    "hometheme": {},
}
UNITY_PREFIX = "[uc]"
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def role_prefixes(family, role):
    alias = ROLE_ALIASES.get(family, {}).get(str(role).lower())
    return (alias[0],) if alias and alias[1] else ()


def normalize_role(family, role):
    alias = ROLE_ALIASES.get(family, {}).get(str(role).lower())
    return alias[0] if alias else role


def sanitize(value):
    return INVALID.sub("_", str(value)).strip() or "unnamed"


def family_of_container(container):
    """Return the cosmetic family a bundle container path belongs to."""
    if not isinstance(container, str):
        return None
    lowered = "/" + container.replace("\\", "/").lower().lstrip("/")
    for name, meta in FAMILIES.items():
        if f"/{meta['directory']}/" in lowered:
            return name
    return None


def family_of_bundle(file):
    """Return the cosmetic family of a style/theme bundle path or file name."""
    text = str(file).replace("\\", "/").lower()
    for name, meta in FAMILIES.items():
        if f"/{meta['directory']}/" in "/" + text.lstrip("/"):
            return name
    name = PurePosixPath(text).name
    for candidate, meta in FAMILIES.items():
        if name.startswith(UNITY_PREFIX + meta["id_prefix"]) and name.endswith(".ab"):
            return candidate
    return None


def bundle_id(file):
    """Style/theme id of a bundle, without the ``[uc]`` build prefix."""
    stem = PurePosixPath(str(file).replace("\\", "/")).stem
    lowered = stem.lower()
    if lowered.startswith(UNITY_PREFIX):
        return stem[len(UNITY_PREFIX):]
    return stem


def bundle_relative(family, identifier):
    return FAMILIES[family]["bundle"].format(id=identifier)


def target_name(family, identifier, role):
    return sanitize(f"{FAMILIES[family]['prefix']}_{identifier}_{role}.png")


def role_name(container, texture_name, identifier):
    """Human readable role for one texture inside a style/theme bundle.

    The addressable ``.png`` container is used when the texture has one, so
    sub folders such as ``astc/tv/tv_frame.png`` stay distinguishable from a
    same-named root texture.  Textures only reachable through a material or
    prefab container keep their texture name.
    """
    if isinstance(container, str) and container.lower().endswith(".png"):
        parts = [part for part in container.replace("\\", "/").split("/") if part]
        lowered = [part.lower() for part in parts]
        folder = (UNITY_PREFIX + identifier).lower()
        index = lowered.index(folder) if folder in lowered else -1
        if index >= 0:
            rest = parts[index + 1:]
        else:
            # Imported packages may keep a different container root.
            rest = parts[-1:]
        if rest and rest[0].lower() == "arts" and len(rest) > 1:
            rest = rest[1:]
        stem = PurePosixPath(rest[-1]).stem if rest else texture_name
        rest = rest[:-1] + [stem]
        role = "_".join(part for part in rest if part)
        if role:
            return sanitize(role)
    return sanitize(texture_name)


def _match_identifier(family, rest, identifiers):
    matches = [identifier for identifier in identifiers.get(family, ()) if rest.lower().startswith(identifier.lower() + "_")]
    if not matches:
        return None
    identifier = max(matches, key=len)
    return identifier, rest[len(identifier) + 1:]


def split_target(value, identifiers, with_original=False):
    """Split a Mod resource name into ``(family, identifier, role)``.

    Accepted forms are the canonical ``namecardskin_nc_x_bg.png`` /
    ``hometheme_tm_x_btn.png``, the community ``namecardstyle_…`` prefix and a
    bare ``<style>_<role>.png`` / ``<theme>_<role>.png`` name.  ``identifiers``
    maps each family to the style/theme ids of the current installation; the
    longest id wins because ids contain underscores.  With ``with_original`` the
    unaliased role is returned as a fourth item so callers can expand it.
    """
    name = PurePosixPath(str(value).replace("\\", "/")).name
    if not name.lower().endswith(".png"):
        return None
    stem = name[:-4]
    lowered = stem.lower()
    parsed = None
    for prefix, family in ALIAS_PREFIXES.items():
        if lowered.startswith(prefix + "_"):
            matched = _match_identifier(family, stem[len(prefix) + 1:], identifiers)
            if not matched:
                return None
            parsed = (family,) + matched
            break
    if not parsed:
        for family in FAMILIES:
            matched = _match_identifier(family, stem, identifiers)
            if matched:
                parsed = (family,) + matched
                break
    if not parsed:
        return None
    family, identifier, original = parsed
    result = (family, identifier, normalize_role(family, original))
    return (*result, original) if with_original else result


def alias_target(roots, value):
    """Canonical target for a loose Mod file name, or ``None``."""
    parsed = split_target(value, identifiers(roots))
    if not parsed:
        return None
    family, identifier, role = parsed
    return target_name(family, identifier, role)


def target_from_source(relative):
    """Canonical target for a Mod file that keeps the bundle folder layout.

    ``arts/ui/hometheme/[uc]tm_mainline_1/btn_battle.png`` and
    ``hometheme/tm_mainline_1/btn_battle.png`` both become
    ``hometheme_tm_mainline_1_btn_battle.png``.
    """
    parts = [part for part in str(relative).replace("\\", "/").split("/") if part]
    for index, part in enumerate(parts[:-1]):
        lowered = part.lower()
        stripped = lowered[len(UNITY_PREFIX):] if lowered.startswith(UNITY_PREFIX) else lowered
        family = ALIAS_PREFIXES.get(lowered)
        if family:
            rest = parts[index + 1:]
            if not rest or not rest[-1].lower().endswith(".png") or rest[0].lower().endswith(".png"):
                return None
            identifier = rest[0]
            if identifier.lower().startswith(UNITY_PREFIX):
                identifier = identifier[len(UNITY_PREFIX):]
            role_parts = rest[1:-1]
        elif stripped.startswith("tm_"):
            # A folder copied straight from the game assets keeps the theme id
            # as its name: [uc]tm_rhodes_day/btn_battle.png.
            family, identifier, role_parts = "hometheme", stripped, parts[index + 1:-1]
            rest = parts[index + 1:]
        elif stripped.startswith("nc_"):
            family, identifier, role_parts = "namecard", stripped, parts[index + 1:-1]
            rest = parts[index + 1:]
        else:
            continue
        if not rest or not rest[-1].lower().endswith(".png"):
            return None
        if role_parts and role_parts[0].lower() == "arts":
            role_parts = role_parts[1:]
        role = sanitize("_".join(role_parts + [PurePosixPath(rest[-1]).stem]))
        return target_name(family, identifier, normalize_role(family, role))
    return None


def inventory(roots):
    """Style/theme bundles available in the selected installation."""
    found = {}
    for root_value in roots:
        root = Path(root_value)
        if not root.is_dir():
            continue
        for family, meta in FAMILIES.items():
            directory = root / meta["directory"]
            if not directory.is_dir():
                continue
            for file in directory.glob("*.ab"):
                if not file.is_file():
                    continue
                identifier = bundle_id(file)
                if not identifier.lower().startswith(meta["id_prefix"]):
                    continue
                relative = file.relative_to(root).as_posix()
                found.setdefault((family, identifier.lower()), (relative, file, identifier))
    return found


def identifiers(roots):
    result = {}
    for family, identifier in inventory(roots):
        result.setdefault(family, set()).add(identifier)
    return {family: sorted(values, key=len, reverse=True) for family, values in result.items()}


def resolve_bundles(roots, requested=()):
    """Bundles for canonical targets or folder-layout Mod paths."""
    available = inventory(roots)
    known = {family: sorted(values, key=len, reverse=True) for family, values in identifiers(roots).items()}
    selected = {}
    for value in requested:
        if not isinstance(value, str) or not value:
            continue
        parsed = split_target(value, known)
        if not parsed:
            source = target_from_source(value)
            parsed = split_target(source, known) if source else None
        if not parsed:
            continue
        family, identifier, _role = parsed
        entry = available.get((family, identifier.lower()))
        if entry:
            selected[entry[0].lower()] = (entry[0], entry[1])
    return [selected[key] for key in sorted(selected)]
