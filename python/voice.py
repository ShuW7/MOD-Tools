"""Voice lines of the PC client (``audio/sound_beta_2``).

Every character has one bundle per language group::

    audio/sound_beta_2/voice/char_003_kalts.ab     (jp)
    audio/sound_beta_2/voice_cn/char_003_kalts.ab  (cn)

A bundle holds one ``AudioClip`` per line (``CN_001`` ...), and all clips share
one ``.resource`` stream made of independent FSB5 banks. Replacing a line means
appending a new FSB5 bank (uncompressed PCM16) to that resource and pointing the
clip at it, so every untouched bank stays byte-identical.
"""
import re
import struct
import wave
from pathlib import Path, PurePosixPath

AUDIO_ROOT = "audio/sound_beta_2"
GROUP_DIRS = {"jp": "voice", "cn": "voice_cn"}
GROUP_OF_DIR = {value: key for key, value in GROUP_DIRS.items()}
UNITY_PREFIX = "[uc]"
FREQUENCIES = {8000: 1, 11000: 2, 11025: 3, 16000: 4, 22050: 5, 24000: 6, 32000: 7, 44100: 8, 48000: 9}
CLIP = re.compile(r"^(?P<clip>.+?)_(?P<path>-?\d{6,})$")
CHARACTER = re.compile(r"char_\d+_[a-z0-9]+(?:_[a-z0-9]+)*(?:#[0-9a-z]+)?", re.I)


def bundle_relative(group, character):
    return f"{AUDIO_ROOT}/{GROUP_DIRS[group]}/{character}.ab"


def family_of_bundle(file):
    """Language group of a voice bundle path, or ``None``."""
    text = "/" + str(file).replace("\\", "/").lower().lstrip("/")
    for group, directory in GROUP_DIRS.items():
        if f"/{AUDIO_ROOT.lower()}/{directory}/" in text:
            return group
    return None


def bundle_character(file):
    return PurePosixPath(str(file).replace("\\", "/")).stem


def target_name(group, character, clip):
    return f"voice_{group}_{character}_{clip}.wav"


def split_target(value, known):
    """Split ``voice_<group>_<character>_<clip>.wav`` using known characters."""
    name = PurePosixPath(str(value).replace("\\", "/")).name
    if not name.lower().endswith(".wav"):
        return None
    stem = name[:-4]
    for group in GROUP_DIRS:
        prefix = f"voice_{group}_"
        if not stem.lower().startswith(prefix):
            continue
        rest = stem[len(prefix):]
        matches = [character for character in known.get(group, ()) if rest.lower().startswith(character.lower() + "_")]
        if not matches:
            return None
        character = max(matches, key=len)
        return group, character, rest[len(character) + 1:]
    return None


def read_hints(root):
    """Character/group hints from the legacy ``mod.ini`` next to the Mod."""
    hints = {"character": "", "group": ""}
    file = Path(root) / "mod.ini"
    if file.is_file():
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return hints
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if not separator:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "character":
                match = CHARACTER.search(value)
                if match:
                    hints["character"] = match.group(0)
            elif key == "voice_group":
                hints["group"] = "cn" if "cn" in value.lower() else "jp" if value else ""
    return hints


def normalize(path_value, clip, hints, character_hint=""):
    """Character and group for a Mod file, from hints or its folder layout."""
    group = hints.get("group") or ""
    character = character_hint or hints.get("character") or ""
    parts = [part for part in str(path_value).replace("\\", "/").split("/") if part]
    for part in parts[:-1]:
        if not character:
            match = CHARACTER.search(part)
            if match:
                character = match.group(0)
        lowered = part.lower()
        if not group and lowered in ("voice_cn", "cn"):
            group = "cn"
        elif not group and lowered in ("voice", "jp", "voice_jp"):
            group = "jp"
    return group or "cn", character


def target_from_source(relative, hints, character_hint=""):
    """Canonical target for a loose ``.wav`` Mod file."""
    name = PurePosixPath(str(relative).replace("\\", "/")).name
    if not name.lower().endswith(".wav"):
        return None
    stem = name[:-4]
    match = CLIP.match(stem)
    clip = match.group("clip") if match else stem
    if not clip or not re.fullmatch(r"[A-Za-z0-9_#-]+", clip):
        return None
    group, character = normalize(relative, clip, hints, character_hint)
    if not character:
        return None
    return target_name(group, character, clip)


def clip_path_id(name):
    match = CLIP.match(PurePosixPath(str(name).replace("\\", "/")).stem)
    if not match:
        return None
    try:
        return int(match.group("path"))
    except ValueError:
        return None


def inventory(roots):
    """Voice bundles of both groups available in the selected installation."""
    found = {}
    for root_value in roots:
        root = Path(root_value)
        if not root.is_dir():
            continue
        for group, directory in GROUP_DIRS.items():
            base = root / AUDIO_ROOT / directory
            if not base.is_dir():
                continue
            for file in base.glob("*.ab"):
                if file.is_file():
                    relative = file.relative_to(root).as_posix()
                    found.setdefault((group, file.stem), (relative, file))
    return found


def known(roots):
    result = {}
    for group, character in inventory(roots):
        result.setdefault(group, []).append(character)
    return {group: sorted(values, key=len, reverse=True) for group, values in result.items()}


def resolve_bundles(roots, identifiers=(), requested=()):
    """Voice bundles for canonical targets or character ids with WAV edits."""
    available = inventory(roots)
    names = known(roots)
    selected = {}
    wanted = False
    hinted = False
    for value in requested:
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        if lowered.endswith(".ab") and lowered.startswith(AUDIO_ROOT):
            entry = next((item for (group, character), item in available.items() if item[0].lower() == lowered), None)
            if entry:
                selected[entry[0].lower()] = entry
            continue
        if not lowered.endswith(".wav"):
            continue
        wanted = True
        parsed = split_target(value, names)
        if parsed:
            hinted = True
            entry = available.get((parsed[0], parsed[1]))
            if entry:
                selected[entry[0].lower()] = entry
    if identifiers and wanted and not hinted:
        # Without a language hint both groups are offered; the canonical target
        # names the group, so the matching bundle is selected at the end.
        for identifier in identifiers:
            for group in GROUP_DIRS:
                entry = available.get((group, identifier))
                if entry:
                    selected[entry[0].lower()] = entry
    return [selected[key] for key in sorted(selected)]


def wav_pcm16(file):
    """Read a WAV file as 16-bit PCM samples."""
    with wave.open(str(file), "rb") as source:
        channels, width, rate, frames = source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()
        if channels not in (1, 2):
            raise ValueError("Only mono or stereo voice files are supported")
        raw = source.readframes(frames)
    if width == 2:
        return raw, rate, channels
    if width == 1:
        return b"".join(struct.pack("<h", (sample - 128) << 8) for sample in raw), rate, channels
    if width == 3:
        data = bytearray()
        for index in range(0, len(raw) - 2, 3):
            value = int.from_bytes(raw[index:index + 3], "little", signed=True) >> 8
            data += struct.pack("<h", value)
        return bytes(data), rate, channels
    raise ValueError("Voice files must be 8/16/24-bit PCM WAV")


def build_fsb5(pcm, frequency, channels, template=None):
    """Single-sample FSB5 bank with uncompressed PCM16 data."""
    index = FREQUENCIES.get(frequency)
    if index is None:
        raise ValueError(f"Unsupported voice sample rate: {frequency}")
    if channels not in (1, 2):
        raise ValueError("Only mono or stereo voice files are supported")
    frames = len(pcm) // (2 * channels)
    header = b"FSB5" + struct.pack("<IIIIII", 1, 1, 8, 0, len(pcm), 2)
    zero = template[28:36] if template else b"\0" * 8
    hash_ = template[36:52] if template else b"\0" * 16
    dummy = template[52:60] if template else b"\0" * 8
    packed = ((index & 0xF) << 1) | (((channels - 1) & 1) << 5) | ((frames & 0x3FFFFFFF) << 34)
    return header + zero + hash_ + dummy + struct.pack("<Q", packed) + pcm
