"""Rebuild external PC bundles from current local originals, without game writes."""
import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path

import UnityPy
from PIL import Image
from UnityPy.enums import TextureFormat
import ab_mod
import appearance_assets as appearance
import ui_cosmetics as ui
import voice
from worker import BUNDLE_EXTS, bundle_records, normalized, source_files, ui_targets_for
from pc_layout import CHARACTER, character_hints, current_bundles, game_roots, read_bindings, relative_path


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def appearance_fit(image, record):
    """Scale an avatar/portrait replacement to the native PC texture size.

    The page-bundle sprites use fixed pixel rectangles, so a differently sized
    image would be cropped instead of fitted.  Scaling here keeps the Mod
    usable and produces exactly the pixels that are verified below.
    """
    native = tuple(record.get("appearanceNativeSize") or ())
    if native and image.size != native:
        if native[0] * native[1] > 64 * 1024 * 1024:
            raise ValueError("Texture is too large")
        return image.resize(native, Image.Resampling.LANCZOS)
    return image


ALPHA_SUFFIX = "[alpha].png"


def alpha_base_key(target):
    """Return the main texture key for a split-alpha resource target."""
    key = normalized(target or "")
    if not key.endswith(ALPHA_SUFFIX):
        return None
    return key[:-len(ALPHA_SUFFIX)] + ".png"


def merge_alpha_channel(image, alpha_file):
    """Apply a Mod's separate `[alpha]` map to an illustration image."""
    if not alpha_file:
        return image
    with Image.open(alpha_file) as alpha_image:
        alpha = alpha_image.convert("RGB").getchannel("R")
        if alpha.size != image.size:
            alpha = alpha.resize(image.size, Image.Resampling.LANCZOS)
    result = image.convert("RGBA")
    result.putalpha(alpha)
    return result


class RawFile:
    """Raw bundle entry replacement (the streaming ``.resource`` file)."""

    def __init__(self, data, flags=0):
        self._data = bytes(data)
        self.flags = flags

    def save(self):
        return self._data


def build_voice_bundle(env, selected, relative):
    """Replace AudioClip samples by appending PCM FSB5 banks to the resource."""
    resource_name = next((name for name in env.file.files if name.lower().endswith('.resource')), None)
    if not resource_name:
        raise ValueError('Voice bundle has no streaming resource: ' + relative)
    resource = env.file.files[resource_name].bytes
    expected = []
    for record, file in selected:
        if record['kind'] != 'audio':
            raise ValueError('Mixed resource kinds in a voice bundle')
        tree = record['object'].read_typetree()
        offset = int(tree['m_Resource']['m_Offset'])
        size = int(tree['m_Resource']['m_Size'])
        template = resource[offset:offset + 60] if 0 <= offset <= len(resource) - 60 else None
        pcm, rate, channels = voice.wav_pcm16(file)
        blob = voice.build_fsb5(pcm, rate, channels, template=template)
        new_offset = (len(resource) + 15) // 16 * 16
        resource = resource + b'\0' * (new_offset - len(resource)) + blob
        frames = len(pcm) // (2 * channels)
        tree['m_Resource']['m_Offset'] = new_offset
        tree['m_Resource']['m_Size'] = len(blob)
        tree['m_CompressionFormat'] = 0
        tree['m_LoadType'] = 0
        tree['m_Length'] = frames / rate if rate else 0.0
        tree['m_Frequency'] = rate
        tree['m_Channels'] = channels
        tree['m_BitsPerSample'] = 16
        record['object'].save_typetree(tree)
        expected.append((record, blob, offset, size, rate, channels, frames))
    env.file.files[resource_name] = RawFile(resource, flags=0)
    return resource_name, expected


def asset_bundle_entries(source, game, scoped, scope):
    """Prebuilt AssetBundle replacements and removals for the current build.

    Returns ``(entries, removals)``. A scoped build of a disabled Mod removes
    its bundles instead of replacing them.
    """
    if scoped:
        config = ab_mod.read_config(scope)
        if not config:
            return [], []
        try:
            planned = ab_mod.plan(config, game)
        except Exception as error:
            raise ValueError(f"AssetBundle Mod {scope.name}: {error}")
        for entry in planned:
            entry["root"] = Path(scope)
        disabled = bool(scope.name.upper().startswith(("DISABLED_", "DISABLE_")))
        return ([] if disabled else planned), (planned if disabled else [])
    entries = []
    seen = {}
    for root in sorted({file.parent for file in Path(source).rglob("mod.ini")}):
        if any(part.upper().startswith(("DISABLED_", "DISABLE_")) for part in Path(root).parts):
            continue
        config = ab_mod.read_config(root)
        if not config:
            continue
        try:
            planned = ab_mod.plan(config, game)
        except Exception as error:
            raise ValueError(f"AssetBundle Mod {Path(root).name}: {error}")
        for entry in planned:
            target = entry["game_relative"]
            if target in seen:
                raise ValueError("Multiple AssetBundle Mods replace the same bundle: " + target)
            seen[target] = entry["file"]
            entry["root"] = Path(root)
            if not entry.get("valid"):
                raise ValueError("Not a Unity AssetBundle file: " + entry["file"].name)
            entries.append(entry)
    return entries, []


def build_bundles(request):
    source = Path(request["source"]).resolve(strict=True)
    roots = game_roots(request.get("gamePath"))
    if not roots:
        raise ValueError("Select the current Arknights.exe before preparing PC bundles")
    game = Path(request["gamePath"]).resolve(strict=True)
    output = Path(request["output"]).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output) or output.is_relative_to(game.parent):
        raise ValueError("Output must be new and outside the game and Mod library")
    scoped = request.get('scopeSource') is not None
    scope = Path(request['scopeSource']).resolve(strict=True) if scoped else None
    ab_entries, ab_removals = asset_bundle_entries(source, game, scoped, scope)
    # Even a scoped patch must leave every other enabled AssetBundle Mod alone.
    known_entries = asset_bundle_entries(source, game, False, None)[0] if scoped else ab_entries
    ab_files = {entry['file'].resolve() for entry in known_entries + ab_entries + ab_removals}
    ab_all_sources = {str(Path(game.parent / entry['source']).resolve()) for entry in known_entries if entry.get('source')}
    inputs = source_files(source, max_files=100000)
    # A user confirmation approves exact resource pairs, including their bytes.
    # Changed files and ordinary standalone conversions still reject overlaps.
    marker = source / '.qaqr-m4a.json'
    deployment = json.loads(marker.read_text(encoding='utf-8')) if marker.is_file() else {}
    approved_pairs = set()
    if deployment.get('owner') == 'qaqr-arknights-v1':
        approved_pairs = {tuple(sorted((item['file'], item['sha256']) for item in pair)) for pair in deployment.get('approvedResourcePairs', [])}
        priority = {name: index for index, name in enumerate(deployment.get('modOrder', []))}
        inputs.sort(key=lambda file: (priority.get(file.relative_to(source).parts[0], -1), file.as_posix()))
    bindings = read_bindings(source, max_manifests=10000)
    resources = []
    by_key = {}
    alpha_by_key = {}
    alpha_inputs = set()
    voice_hints_cache = {}

    def mod_hints(file):
        parts = file.relative_to(source).parts
        root = source / parts[0] if len(parts) > 1 else source
        key = str(root)
        if key not in voice_hints_cache:
            voice_hints_cache[key] = voice.read_hints(root)
        return voice_hints_cache[key]

    for file in inputs:
        if file.suffix.lower() in BUNDLE_EXTS:
            if file.resolve() in ab_files:
                continue
            raise ValueError("Convert raw Mod bundles into resources before enabling them")
        if file.suffix.lower() == ".txt":
            # Text Mods are consumed at runtime by the text engine; they are
            # not inputs to the AssetBundle rebuild pipeline.
            continue
        if file.suffix.lower() not in (".png", ".skel", ".atlas", ".json", ".wav"):
            raise ValueError("Source loader supports character PNG/Spine/WAV resources only: " + file.name)
        binding = bindings.get(file, {})
        relative = file.relative_to(source).as_posix()
        # Loose Mods may keep the game folder layout, use the community
        # namecardstyle prefix or name the style/theme directly.
        target = binding.get('target') or ui.target_from_source(relative) or appearance.target_from_source(relative)
        if not target and file.suffix.lower() == '.wav':
            parts = file.relative_to(source).parts
            local = '/'.join(parts[1:]) if len(parts) > 1 else relative
            target = voice.target_from_source(local, mod_hints(file))
        name_key = normalized(file.name)
        if name_key.endswith(ALPHA_SUFFIX):
            alpha_key = alpha_base_key(target) if target else None
            if not alpha_key and target:
                alpha_key = normalized(target)
                if alpha_key.endswith(ALPHA_SUFFIX):
                    alpha_key = alpha_key[:-len(ALPHA_SUFFIX)] + ".png"
            if not alpha_key:
                alpha_key = name_key[:-len(ALPHA_SUFFIX)] + ".png"
            if alpha_key in alpha_by_key:
                raise ValueError("Multiple alpha channel maps target the same texture: " + file.name)
            alpha_by_key[alpha_key] = file
            alpha_inputs.add(file)
            continue
        targets = [target] if target else (ui_targets_for(roots, file.name) or [file.name])
        for item_target in targets:
            item = {'file': file, 'key': normalized(item_target), 'bundle': binding.get('bundle')}
            resources.append(item)
            by_key.setdefault(item['key'], []).append(item)
    ids = {match.lower() for file in inputs if file.suffix.lower() != '.txt' for match in CHARACTER.findall(file.name)}
    ids.update(character_hints(request))
    ids.update(match.lower() for item in bindings.values() for match in CHARACTER.findall(item['bundle']))
    if len(ids) > 1000:
        raise ValueError("Too many operators in a single preparation")
    requested = [item['bundle'] or item['key'] for item in resources]
    affected = set()
    if scoped:
        previous = request.get('affectedBundles', [])
        if not isinstance(previous, list) or len(previous) > 200:
            raise ValueError('Invalid affected bundle list')
        affected.update(relative_path(value).casefold() for value in previous)
        scope_bindings = read_bindings(scope)
        scope_files = [file for file in source_files(scope) if file.suffix.lower() in ('.png', '.skel', '.atlas', '.json', '.wav') and not normalized(file.name).endswith(ALPHA_SUFFIX)]
        scope_items = []
        scope_hints = voice.read_hints(scope)
        for file in scope_files:
            binding = scope_bindings.get(file, {})
            target = binding.get('target')
            if not target and file.suffix.lower() == '.wav':
                target = voice.target_from_source(file.name, scope_hints)
            scope_items.append((normalized(target or file.name), binding.get('bundle')))
        scope_ids = {match.lower() for file in scope_files for match in CHARACTER.findall(file.name)}
        scope_ids.update(character_hints(request))
        scope_requested = [bundle or key for key, bundle in scope_items]
        scope_matches = {item: set() for item in scope_items}
        if scope_items and all(bundle for _, bundle in scope_items):
            for item in scope_items:
                scope_matches[item].add(item[1].casefold())
        else:
            for relative, original in current_bundles(roots, scope_ids, scope_requested):
                records, _, _ = bundle_records(original)
                keys = {normalized(record['target']) for record in records}
                for item in scope_items:
                    key, bundle = item
                    if key in keys and (not bundle or bundle.casefold() == relative.casefold()):
                        scope_matches[item].add(relative.casefold())
        if any(len(matches) != 1 for matches in scope_matches.values()):
            raise ValueError('The selected Mod needs unambiguous current PC resource bindings; diagnose and convert it first')
        for matches in scope_matches.values():
            affected.update(matches)
        requested = sorted(affected)
    loaded = []
    # One Mod image can map to several textures (legacy roles such as shareBg);
    # track matches per resource entry, not per file.
    matches = {id(item): [] for item in resources if not scoped or (item['bundle'] and item['bundle'].casefold() in affected)}
    removed = []
    selected_originals = current_bundles(roots, set() if scoped or (resources and all(item['bundle'] for item in resources)) else ids, requested)
    if scoped and affected.difference(relative.casefold() for relative, _ in selected_originals):
        raise ValueError('A previously loaded game bundle is missing; restart and prepare Mods against the current game version')
    for relative, original in selected_originals:
        before_hash = digest(original)
        records, _, platforms, env = bundle_records(original, return_env=True)
        selected = []
        for record in records:
            for item in by_key.get(normalized(record['target']), []):
                if normalized(record['target']) == item['key'] and (not item['bundle'] or item['bundle'].casefold() == relative.casefold()):
                    selected.append((record['target'], item['file']))
                    matches.setdefault(id(item), []).append((relative, record['target']))
        if selected:
            loaded.append((relative, original, before_hash, selected))
        elif scoped:
            removed.append({'source': original.relative_to(game.parent).as_posix(), 'sourceSha256': before_hash, 'logical': relative})
    # A prebuilt AssetBundle replaces the whole bundle: it takes precedence and
    # the resource rebuild for the same source is dropped.
    shadowed = []
    if ab_all_sources:
        kept = []
        for item in loaded:
            if str(Path(item[1]).resolve()) in ab_all_sources:
                shadowed.append(item[0])
                continue
            kept.append(item)
        loaded = kept
    removed_sources = {item['source'].casefold() for item in removed}
    for entry in ab_removals:
        if not entry.get('source'):
            continue
        path = game.parent / entry['source']
        relative = path.relative_to(game.parent).as_posix()
        if relative.casefold() in removed_sources:
            continue
        removed_sources.add(relative.casefold())
        removed.append({'source': relative, 'sourceSha256': digest(path), 'logical': entry['game_relative']})
    missing = [item['file'].name for item in resources if id(item) in matches and not matches[id(item)]]
    ambiguous = [item['file'].name for item in resources if len(matches.get(id(item), [])) > 1]
    if missing:
        raise ValueError('No unambiguous current PC binding for: ' + ', '.join(sorted(set(missing))[:8]))
    if ambiguous:
        raise ValueError('Specify a bundle in the resource manifest for: ' + ', '.join(sorted(set(ambiguous))[:8]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.qaqr-bundles-', dir=output.parent) as temporary:
        stage = Path(temporary)
        bundles = []
        for relative, original, before_hash, targets in loaded:
            if digest(original) != before_hash:
                raise ValueError('Game resources changed during preparation')
            records, _, platforms, env = bundle_records(original, return_env=True)
            by_target = {record['target']: record for record in records}
            selected = [(by_target[target], file) for target, file in targets]
            if any(platform not in (5, 19) for platform in platforms):
                raise ValueError("The selected game has non-Windows bundle resources")
            before_ids = sorted((obj.path_id, obj.type.name) for obj in env.objects)
            identity = lambda obj: (obj.assets_file.name, obj.path_id)
            selected_ids = {identity(record['object']) for record, _ in selected}
            preserved = {identity(obj): hashlib.sha256(obj.get_raw_data()).digest() for obj in env.objects if identity(obj) not in selected_ids}
            changed = []
            written = {}
            for record, file in selected:
                oid = identity(record['object'])
                file_hash = digest(file)
                if oid in written:
                    previous_file, previous_hash = written[oid]
                    pair = tuple(sorted(((previous_file, previous_hash), (file.relative_to(source).as_posix(), file_hash))))
                    if previous_hash != file_hash and pair not in approved_pairs:
                        raise ValueError('Conflicting modifications to the same PC object: ' + relative + ' / ' + record['target'])
                    if previous_hash == file_hash:
                        continue
                written[oid] = (file.relative_to(source).as_posix(), file_hash)
                if record["kind"] == "audio":
                    # Voice samples are written into the bundle resource below.
                    continue
                data = record["object"].read()
                if record["kind"] == "texture":
                    with Image.open(file) as image:
                        if image.width * image.height > 64 * 1024 * 1024:
                            raise ValueError("Texture is too large")
                        alpha_file = alpha_by_key.get(normalized(record["target"]))
                        merged = merge_alpha_channel(image.convert("RGBA"), alpha_file)
                        data.set_image(appearance_fit(merged, record), target_format=TextureFormat.RGBA32)
                elif record["kind"] == "spine":
                    data.m_Script = file.read_bytes().decode("utf-8", "surrogateescape")
                else:
                    raise ValueError("Unsupported resource binding")
                data.save()
                changed.append(record["target"])
            audio_expected = []
            audio_resource = None
            audio_selected = [(record, file) for record, file in selected if record["kind"] == "audio"]
            if audio_selected:
                audio_resource, audio_expected = build_voice_bundle(env, audio_selected, relative)
                changed.extend(record["target"] for record, _file in audio_selected)
            if len(env.files) != 1:
                raise ValueError("Unexpected bundle root layout")
            encoded = next(iter(env.files.values())).save(packer="lz4")
            # Reparse every generated package and preserve the PC object graph.
            checked = UnityPy.load(encoded)
            if sorted((obj.path_id, obj.type.name) for obj in checked.objects) != before_ids:
                raise ValueError("Generated bundle object identity changed")
            if sorted({int(asset.target_platform) for asset in checked.assets}) != platforms:
                raise ValueError("Generated bundle platform changed")
            checked_objects = {identity(obj): obj for obj in checked.objects}
            for oid, expected in preserved.items():
                if hashlib.sha256(checked_objects[oid].get_raw_data()).digest() != expected:
                    raise ValueError("An unrelated PC object changed during serialization")
            for record, file in selected:
                if record["kind"] == "audio":
                    continue
                actual = checked_objects[identity(record["object"])].read()
                if record["kind"] == "texture":
                    with Image.open(file) as expected:
                        expected_image = merge_alpha_channel(
                            expected.convert("RGBA"),
                            alpha_by_key.get(normalized(record["target"])),
                        )
                        if actual.image.convert("RGBA").tobytes() != appearance_fit(expected_image, record).tobytes():
                            raise ValueError("Texture pixel round-trip check failed")
                elif actual.m_Script.encode("utf-8", "surrogateescape") != file.read_bytes():
                    raise ValueError("Spine byte round-trip check failed")
            if audio_expected:
                checked_resource = checked.file.files[audio_resource].bytes
                for record, blob, _original_offset, _original_size, rate, channels, frames in audio_expected:
                    tree = checked_objects[identity(record["object"])].read_typetree()
                    offset = int(tree["m_Resource"]["m_Offset"])
                    size = int(tree["m_Resource"]["m_Size"])
                    if size != len(blob) or checked_resource[offset:offset + size] != blob:
                        raise ValueError("Voice sample round-trip check failed")
                    if int(tree["m_CompressionFormat"]) != 0 or int(tree["m_Frequency"]) != rate or int(tree["m_Channels"]) != channels:
                        raise ValueError("Voice clip metadata round-trip check failed")
                    if abs(float(tree["m_Length"]) - frames / rate) > 0.01:
                        raise ValueError("Voice clip duration round-trip check failed")
            if digest(original) != before_hash:
                raise ValueError("Game resources changed during preparation")
            replacement = 'bundles/' + relative
            input_files = {file.relative_to(source).as_posix() for _, file in targets}
            for target, _ in targets:
                alpha_file = alpha_by_key.get(normalized(target))
                if alpha_file:
                    input_files.add(alpha_file.relative_to(source).as_posix())
            bundles.append({"source": original.relative_to(game.parent).as_posix(), "replacement": replacement, "sourceSha256": before_hash, "replacementSha256": hashlib.sha256(encoded).hexdigest(), "resources": changed, "logical": relative, "inputs": sorted(input_files)})
            target = stage / replacement
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)
        ab_bundles = []
        for entry in ab_entries:
            relative = entry['game_relative']
            source_file = game.parent / entry['source']
            if not source_file.is_file():
                raise ValueError('AssetBundle target is missing from the game: ' + relative)
            replacement = 'bundles/' + relative
            target = stage / replacement
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry['file'], target)
            inputs = [entry['file'].relative_to(entry['root']).as_posix()]
            if scoped:
                # Reload filters the bundle index by the deployed Mod id.
                try:
                    content = (entry['root'] / 'mod.ini').read_bytes()
                    for child in sorted(Path(source).iterdir()):
                        if child.is_dir() and (child / 'mod.ini').is_file() and (child / 'mod.ini').read_bytes() == content and (child / inputs[0]).is_file():
                            inputs = [f"{child.name}/{inputs[0]}"]
                            break
                except OSError:
                    pass
            else:
                inputs = [entry['file'].relative_to(source).as_posix()]
            bundle = {"source": source_file.relative_to(game.parent).as_posix(), "replacement": replacement, "sourceSha256": digest(source_file), "replacementSha256": digest(target), "resources": [entry['key']], "logical": relative, "inputs": sorted(set(inputs)), "assetBundle": True}
            bundles.append(bundle)
            ab_bundles.append(bundle)
        lines = ["QAQR-ARKNIGHTS-PATCH\t1" if scoped else "QAQR-ARKNIGHTS-BUNDLES\t1", "GAME\t" + str(game)]
        for bundle in bundles:
            fields = ["BUNDLE", bundle["source"], bundle["replacement"], bundle["sourceSha256"], bundle["replacementSha256"]]
            if any("\t" in value or "\n" in value or "\r" in value for value in fields):
                raise ValueError("Invalid manifest field")
            lines.append("\t".join(fields))
        for bundle in removed:
            lines.append('\t'.join(['REMOVE', bundle['source'], bundle['sourceSha256']]))
        (stage / "manifest.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        stage.rename(output)
        return {"success": True, "bundleCount": len(bundles), "resourceCount": len(matches), "bundles": bundles, "assetBundles": ab_bundles, "shadowedAssetBundles": shadowed, "removedBundles": removed, "objectAndResourceRoundTripVerified": True, "runtimeValidated": False}
