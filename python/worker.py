"""Read PC/mobile Unity bundles and export selected resources outside the game.

Bindings come from AssetBundle preloads and Spine object references, not object
order: front/back textures frequently have exactly the same m_Name.
"""
import contextlib
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import UnityPy
from UnityPy.enums import CompressionFlags
from UnityPy.helpers import CompressionHelper
from lz4ak import decompress_lz4ak
import appearance_assets as appearance
import ui_cosmetics as ui
import voice
import ab_mod
from pc_layout import CHARACTER, RESOURCE_MANIFEST, character_hints, current_bundles, game_roots, read_bindings, relative_path

CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak
MAX_BUNDLE = 512 * 1024 * 1024
BUNDLE_EXTS = {".ab", ".bundle", ".unity3d"}


def safe_name(name):
    if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")) or name in (".", ".."):
        raise ValueError("Unsafe resource name")
    if re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", name, re.I):
        raise ValueError("Reserved resource name")
    return name


def normalized(name):
    return re.sub(r"(?:\[alphe?\]|_alpha)(?=\.png$)", "[alpha]", name.lower().replace("#", "_").replace("@", "_")).removeprefix("illust_")


def source_files(root, *, max_files=20000):
    files = []
    for item in root.rglob("*"):
        parts = item.relative_to(root).parts
        if any(p.startswith(".") or p.lower().startswith(("disable_", "disabled_")) or p.lower() in ("logs", "dump") for p in parts):
            continue
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()) or not item.resolve().is_relative_to(root):
            raise ValueError("Linked source is not supported")
        if item.is_file() and not re.match(r"^(preview[._]|_cover|(?:readme|licen[cs]e|copying)(?:[.]|$)|tags[.]json$|info[.]json$|mod[.]ini$)", item.name, re.I):
            files.append(item)
        if len(files) > max_files:
            raise ValueError("Too many files")
    return sorted(files)


def bundle_records(file, return_env=False):
    if file.stat().st_size > MAX_BUNDLE:
        raise ValueError("Bundle exceeds 512 MiB")
    # Loading bytes keeps UnityPy dependency resolution within the supplied AB.
    env = UnityPy.load(file.read_bytes())
    records = []
    platforms = sorted({int(asset.target_platform) for asset in env.assets})
    skipped = []
    for asset in env.assets:
        objects = asset.objects
        trees = {}
        contexts = {}
        material_refs = {}
        alpha_refs = {}
        alpha_transforms = {}
        for oid, obj in objects.items():
            if obj.type.name == "MonoBehaviour":
                try:
                    trees[oid] = obj.read_typetree()
                except Exception:
                    pass
            elif obj.type.name == 'AssetBundle':
                container_data = obj.read()
                for container, info in container_data.m_Container:
                    if not container.endswith('.prefab'):
                        continue
                    for pointer in container_data.m_PreloadTable[info.preloadIndex:info.preloadIndex + info.preloadSize]:
                        if pointer.m_FileID == 0:
                            contexts.setdefault(pointer.m_PathID, set()).add(Path(container).stem)
            elif obj.type.name == 'Material':
                material = obj.read_typetree()
                properties = dict(material.get('m_SavedProperties', {}).get('m_TexEnvs', []))
                main_property = properties.get('_MainTex', {})
                alpha_property = properties.get('_AlphaTex', {})
                main_pointer = main_property.get('m_Texture', {})
                alpha_pointer = alpha_property.get('m_Texture', {})
                if main_pointer.get('m_FileID') == 0 and main_pointer.get('m_PathID'):
                    main_id = main_pointer['m_PathID']
                    alpha_id = alpha_pointer.get('m_PathID', 0) if alpha_pointer.get('m_FileID', 0) == 0 else None
                    alpha_refs.setdefault(main_id, set()).add(alpha_id)
                    same_transform = all(main_property.get(key) == alpha_property.get(key) for key in ('m_Scale', 'm_Offset'))
                    alpha_transforms[main_id] = alpha_transforms.get(main_id, True) and (not alpha_id or same_transform)
                for prop, texture in material.get('m_SavedProperties', {}).get('m_TexEnvs', []):
                    pointer = texture.get('m_Texture', {})
                    if pointer.get('m_FileID') == 0:
                        material_refs.setdefault(pointer.get('m_PathID'), set()).add(str(material.get('m_Name', '')) + '/' + prop)
        skeleton_names = {}
        for tree in trees.values():
            pointer = tree.get('skeletonJSON', {})
            skeleton = objects.get(pointer.get('m_PathID')) if pointer.get('m_FileID') == 0 else None
            if skeleton and skeleton.type.name == 'TextAsset':
                skeleton_names.setdefault(skeleton.read().m_Name.casefold(), set()).add(skeleton.path_id)

        def resolve(pointer):
            if not isinstance(pointer, dict) or pointer.get("m_FileID") != 0:
                return None
            return objects.get(pointer.get("m_PathID"))

        def add(obj, target, kind, **extra):
            target = safe_name(target)
            record = {"target": target, "kind": kind, "object": obj, "source": file.name, "asset": obj.read().m_Name}
            record.update(extra)
            if kind == 'texture':
                refs = alpha_refs.get(obj.path_id, set())
                record['alphaAmbiguous'] = len(refs) > 1 or None in refs
                alpha_id = next(iter(refs)) if len(refs) == 1 else None
                alpha = objects.get(alpha_id)
                if alpha and alpha.type.name == 'Texture2D':
                    record['alphaObject'] = alpha
                    record['alphaTransformCompatible'] = alpha_transforms.get(obj.path_id, False)
            records.append(record)

        # Static portraits use the prefab's dependency span to distinguish the
        # illustration texture from the same-named battle and base textures.
        for obj in objects.values():
            if obj.type.name != "AssetBundle":
                continue
            data = obj.read()
            voice_group = voice.family_of_bundle(file)
            if voice_group:
                # One bundle per character and language group; every line is an
                # AudioClip whose samples live in the bundle's .resource stream.
                character = voice.bundle_character(file)
                for clip in objects.values():
                    if clip.type.name != "AudioClip":
                        continue
                    tree = clip.read_typetree()
                    clip_name = str(tree.get("m_Name") or "")
                    if not clip_name:
                        continue
                    add(clip, voice.target_name(voice_group, character, clip_name), "audio",
                        assetKind="voice",
                        voiceGroup=voice_group,
                        voiceCharacter=character,
                        voiceClip=clip_name,
                        voicePathId=clip.path_id,
                        voiceDuration=float(tree.get("m_Length") or 0),
                        voiceFrequency=int(tree.get("m_Frequency") or 0),
                        voiceChannels=int(tree.get("m_Channels") or 0),
                        voiceCompression=int(tree.get("m_CompressionFormat") or 0))
                continue
            ui_family = ui.family_of_bundle(file)
            if ui_family:
                # Name card styles and home themes are one bundle per style or
                # theme; role names come from the addressable .png container.
                identifier = ui.bundle_id(file)
                best = {}
                for container, info in data.m_Container:
                    for ref in data.m_PreloadTable[info.preloadIndex:info.preloadIndex + info.preloadSize]:
                        texture = objects.get(ref.m_PathID) if ref.m_FileID == 0 else None
                        if not texture or texture.type.name != "Texture2D":
                            continue
                        # A texture can be listed by its sprite container, by a
                        # material and by a prefab; the .png container wins.
                        score = (0 if container.lower().endswith(".png") else 1, container.casefold())
                        if texture.path_id not in best or score < best[texture.path_id][0]:
                            best[texture.path_id] = (score, container, texture)
                used_roles = {}
                for _path_id, (_score, container, texture) in sorted(best.items(), key=lambda item: item[1][1]):
                    texture_data = texture.read()
                    role = ui.role_name(container, texture_data.m_Name, identifier)
                    seen = used_roles.get(role.casefold(), 0) + 1
                    used_roles[role.casefold()] = seen
                    if seen > 1:
                        role = f"{role}__{seen}"
                    add(texture, ui.target_name(ui_family, identifier, role), "texture",
                        assetKind=ui_family,
                        uiIdentifier=identifier,
                        uiRole=role,
                        appearanceNativeSize=(texture_data.m_Width, texture_data.m_Height))
                continue
            for container, info in data.m_Container:
                stem = Path(container).stem
                parts = appearance.container_parts(container)
                if parts:
                    # Avatars and half-body portraits are flat page packs: one
                    # texture per container, addressed by the container path.
                    family, folder, _asset = parts
                    target = appearance.target_name(container)
                    for ref in data.m_PreloadTable[info.preloadIndex:info.preloadIndex + info.preloadSize]:
                        texture = objects.get(ref.m_PathID) if ref.m_FileID == 0 else None
                        if not texture or texture.type.name != "Texture2D":
                            continue
                        texture_data = texture.read()
                        add(texture, target, "texture",
                            assetKind=family,
                            appearanceAsset=stem,
                            appearanceFolder=folder,
                            appearanceNativeSize=(texture_data.m_Width, texture_data.m_Height))
                    continue
                prefix = ""
                if stem.startswith("illust_"):
                    prefix = stem
                if not prefix:
                    continue
                for ref in data.m_PreloadTable[info.preloadIndex:info.preloadIndex + info.preloadSize]:
                    texture = objects.get(ref.m_PathID) if ref.m_FileID == 0 else None
                    if not texture or texture.type.name != "Texture2D":
                        continue
                    name = texture.read().m_Name
                    bare = prefix.removeprefix("illust_")
                    alpha = re.search(r"(\[alpha\]|\[alphe\]|_alpha)$", name, re.I)
                    base_name = name[:alpha.start()] if alpha else name
                    if base_name.casefold() != stem.casefold() and base_name.casefold() != bare.casefold():
                        continue
                    add(texture, prefix + ("[alpha]" if alpha else "") + ".png", "texture")

        # Follow SkeletonAnimation -> SkeletonData -> atlas -> material -> texture.
        for tree in trees.values():
            skeleton = resolve(tree.get("skeletonJSON"))
            if not skeleton or skeleton.type.name != "TextAsset":
                continue
            skeleton_id = next((oid for oid, value in trees.items() if value is tree), None)
            base = re.sub(r"\.(skel|json)(?:\.txt)?$", "", skeleton.read().m_Name, flags=re.I)
            directions = set()
            for component in trees.values():
                if component.get("skeletonDataAsset", {}).get("m_PathID") == skeleton_id:
                    go = resolve(component.get("m_GameObject"))
                    if go and go.type.name == "GameObject":
                        direction = go.read().m_Name.lower()
                        if direction in ("front", "back"):
                            directions.add(direction)
            if len(directions) == 1:
                key = next(iter(directions)) + "_" + base
            elif not directions and base.lower().startswith(("build_", "dyn_illust_", "sp_dyn_illust_", "entry_dyn_illust_", "dyn_portrait_", "dyn_entrance_", "token_", "trap_")):
                key = base
                if len(skeleton_names.get(skeleton.read().m_Name.casefold(), set())) > 1:
                    owners = contexts.get(skeleton.path_id, set())
                    if len(owners) == 1:
                        key = next(iter(owners))
            else:
                skipped.append({"code": "ambiguousSpine", "file": file.name, "asset": base})
                continue
            group = [(skeleton, key + (".json" if skeleton.read().m_Name.endswith(".json") else ".skel"), "spine")]
            atlas_refs = tree.get("atlasAssets", [])
            complete = bool(atlas_refs)
            for atlas_index, atlas_ref in enumerate(atlas_refs):
                atlas_tree = trees.get(atlas_ref.get("m_PathID"), {}) if atlas_ref.get('m_FileID') == 0 else {}
                atlas = resolve(atlas_tree.get("atlasFile"))
                materials = atlas_tree.get("materials", [])
                if not atlas or not materials:
                    complete = False
                    break
                atlas_key = key + (f"__atlas{atlas_index + 1}" if len(atlas_refs) > 1 else "")
                group.append((atlas, atlas_key + ".atlas", "spine"))
                for page_index, material_ref in enumerate(materials):
                    material = resolve(material_ref)
                    if not material:
                        complete = False
                        break
                    props = material.read_typetree().get("m_SavedProperties", {}).get("m_TexEnvs", [])
                    textures = dict(props)
                    page_key = atlas_key + (f"__page{page_index + 1}" if len(materials) > 1 else "")
                    for prop, suffix in (("_MainTex", ""), ("_AlphaTex", "[alpha]")):
                        texture = resolve(textures.get(prop, {}).get("m_Texture"))
                        if texture and texture.type.name == "Texture2D":
                            group.append((texture, page_key + suffix + ".png", "texture"))
            if not complete:
                skipped.append({"code": "missingDependency", "file": file.name, "asset": base})
                continue
            if not any(item[2] == "texture" for item in group):
                skipped.append({"code": "missingDependency", "file": file.name, "asset": base})
                continue
            for item in group:
                add(*item)
        # Dynamic packages also contain independent effect textures. Keep them
        # explicitly scoped to their bundle, rather than matching global names.
        used = {id(record['object']) for record in records}
        textures_by_name = {}
        for obj in objects.values():
            if obj.type.name == 'Texture2D' and id(obj) not in used:
                textures_by_name.setdefault(obj.read().m_Name.casefold(), []).append(obj)
        for same_name in textures_by_name.values():
            signatures = {}
            for obj in same_name:
                references = sorted(normalized(value) for value in material_refs.get(obj.path_id, set()) | contexts.get(obj.path_id, set()))
                signature = hashlib.sha256('\n'.join(references).encode()).hexdigest()[:12] if references else ''
                signatures.setdefault(signature, []).append(obj)
            for signature, objects_with_signature in signatures.items():
                if len(objects_with_signature) != 1:
                    skipped.append({'code': 'ambiguousAsset', 'file': file.name, 'asset': objects_with_signature[0].read().m_Name})
                    continue
                obj = objects_with_signature[0]
                # Reference names distinguish same-named effect textures without
                # depending on object order or version-specific numeric IDs.
                label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', obj.read().m_Name).rstrip('. ')
                suffix = '__binding_' + signature if len(same_name) > 1 else ''
                add(obj, file.stem + '__texture_' + (label or 'unnamed') + suffix + '.png', 'texture')
    unique = {}
    ambiguous = set()
    for record in records:
        key = normalized(record["target"])
        if key in unique and unique[key]["object"] is not record["object"]:
            ambiguous.add(key)
        else:
            unique[key] = record
    for key in ambiguous:
        skipped.append({"code": "ambiguousAsset", "file": file.name, "asset": unique[key]["target"]})
        del unique[key]
    result = (list(unique.values()), skipped, platforms)
    return (*result, env) if return_env else result


def current_records(roots, ids, requested=()):
    found = []
    warnings = []
    digest = hashlib.sha256()
    for relative, file in current_bundles(roots, ids, requested):
        try:
            digest.update(relative.encode('utf-8'))
            digest.update(str(roots.index(next(root for root in roots if file.is_relative_to(root)))).encode())
            with file.open('rb') as stream:
                digest.update(hashlib.file_digest(stream, 'sha256').digest())
            records, _, _ = bundle_records(file)
            for record in records:
                record['bundle'] = relative
            found.extend(records)
        except Exception as error:
            warnings.append({'code': 'baselineUnreadable', 'file': relative, 'detail': str(error)[:240]})
    return found, warnings, digest.hexdigest()


def texture_image(record, merge_alpha=False):
    image = record['object'].read().image.convert('RGBA')
    if merge_alpha:
        if record.get('alphaAmbiguous') or not record.get('alphaTransformCompatible', True):
            raise ValueError('Separate alpha uses ambiguous references or different UV transforms')
        alpha_data = record['alphaObject'].read()
        alpha = alpha_data.image.convert('RGB').getchannel('R')
        if alpha.size != image.size:
            from PIL import Image
            mode = getattr(getattr(alpha_data, 'm_TextureSettings', None), 'm_FilterMode', None)
            if mode not in (0, 1, 2):
                raise ValueError('Separate alpha dimensions differ and its sampler mode is unknown')
            record['alphaResampling'] = {'from': list(alpha.size), 'to': list(image.size), 'filter': 'nearest' if mode == 0 else 'bilinear'}
            alpha = alpha.resize(image.size, Image.Resampling.NEAREST if mode == 0 else Image.Resampling.BILINEAR)
        # Unity's split-alpha representation stores alpha in the red channel.
        # Follow the material reference, using the same merge as SpriteHelper.
        image.putalpha(alpha)
    return image


def export_selected(request, plans, payloads, requires_selection=False):
    """Write an explicitly selected, already analyzed immutable payload set."""
    root = Path(request['source']).resolve(strict=True)
    output = Path(request['output']).resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('Output must be a new, separate directory')
    if request.get('gamePath') and output.is_relative_to(Path(request['gamePath']).resolve().parent):
        raise ValueError('Game directory is read-only')
    selection = request.get('selectedIds')
    if requires_selection and selection is None:
        raise ValueError('Select the resources to include from this bundle')
    if selection is not None and (not isinstance(selection, list) or any(not isinstance(value, str) for value in selection)):
        raise ValueError('Invalid resource selection')
    selected_ids = set(selection) if selection is not None else {plan['id'] for plan in plans}
    if not selected_ids or not selected_ids.issubset({plan['id'] for plan in plans}):
        raise ValueError('Resource selection is empty or no longer matches the analysis')
    output.mkdir(parents=True)
    for relative, data, plan_id in payloads.values():
        if plan_id not in selected_ids:
            continue
        target = output / relative
        if not target.resolve().is_relative_to(output):
            raise ValueError("Invalid output path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    scoped = [{'file': plan['target'], 'bundle': plan['bundle'], 'target': plan['resource']} for plan in plans if plan['id'] in selected_ids and plan['bundle']]
    if scoped:
        (output / RESOURCE_MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'resources': scoped}, ensure_ascii=False, indent=2), encoding='utf-8')
    return selected_ids


def ui_targets_for(roots, value):
    """Canonical name card / home theme targets for one loose Mod file.

    Legacy roles such as ``shareBg`` map onto several textures of the style
    (``sdk_0`` .. ``sdk_4``); every returned target is a separate replacement.
    """
    parsed = ui.split_target(value, ui.identifiers(roots), with_original=True)
    if not parsed:
        return []
    family, identifier, role, original = parsed
    prefixes = ui.role_prefixes(family, original)
    if not prefixes:
        return [ui.target_name(family, identifier, role)]
    entry = ui.inventory(roots).get((family, identifier.lower()))
    if not entry:
        return [ui.target_name(family, identifier, role)]
    try:
        records, _skipped, _platforms = bundle_records(Path(entry[1]))
    except Exception:
        return [ui.target_name(family, identifier, role)]
    matches = []
    for record in records:
        candidate = record.get('uiRole')
        if record.get('assetKind') != family or not candidate:
            continue
        if any(candidate == prefix or candidate.casefold().startswith(prefix.casefold() + '_') for prefix in prefixes):
            matches.append(record['target'])
    return sorted(set(matches)) or [ui.target_name(family, identifier, role)]


def ab_report(root, config, request, output):
    """Analysis of a prebuilt AssetBundle Mod: no conversion, only redirection."""
    if output:
        raise ValueError("AssetBundle Mods are redirected by the loader; they do not need a converted copy")
    game = request.get("gamePath")
    game_exe = Path(game).resolve(strict=True) if game else None
    entries = ab_mod.plan(config, game_exe) if game_exe else ab_mod.bundle_entries(config)
    if not entries:
        raise ValueError("This AssetBundle Mod contains no .ab files")
    warnings = []
    plans = []
    matched = 0
    fingerprint = hashlib.sha256()
    for entry in entries:
        target = entry["game_relative"]
        with entry["file"].open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        fingerprint.update(target.encode("utf-8"))
        fingerprint.update(bytes.fromhex(digest))
        source = ab_mod.overlay_source(game_exe, target) if game_exe else None
        valid = ab_mod.valid_bundle(entry["file"])
        if not valid:
            warnings.append({"code": "abInvalidBundle", "file": entry["file"].name, "asset": target})
        if game_exe and not source:
            warnings.append({"code": "abMissingTarget", "file": entry["file"].name, "asset": target})
        matched += 1 if source else 0
        plans.append({
            "id": hashlib.sha256(target.encode("utf-8")).hexdigest()[:24],
            "source": entry["file"].relative_to(root).as_posix(),
            "asset": entry["file"].name,
            "target": target,
            "resource": target,
            "bundle": target,
            "matched": bool(source),
            "changed": True,
            "comparison": "unknown",
            "bytes": entry["file"].stat().st_size,
            "sha256": digest,
            "assetKind": "assetbundle",
            "abKeyRoot": config["key_root"],
        })
    return {"success": True, "kind": "assetbundle", "plans": plans, "warnings": warnings, "matched": matched,
            "renamed": 0, "platforms": [], "baselineCount": len(entries), "baselineFingerprint": fingerprint.hexdigest()}


def run(request, *, payload_sink=None):
    if request.get("action") == "build-bundles":
        from bundle_builder import build_bundles
        return build_bundles(request)
    root = Path(request["source"]).resolve(strict=True)
    roots = game_roots(request.get("gamePath"))
    output = Path(request["output"]).resolve() if request.get("output") else None
    if output:
        if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("Output must be a new, separate directory")
        if request.get("gamePath") and output.is_relative_to(Path(request["gamePath"]).resolve().parent):
            raise ValueError("Game directory is read-only")
    ab_config = ab_mod.read_config(root)
    if ab_config is not None:
        return ab_report(root, ab_config, request, output)
    inputs = source_files(root)
    bindings = read_bindings(root)
    voice_hints = voice.read_hints(root)
    hint_character = next(iter(character_hints(request)), "") or voice_hints.get("character", "")
    bundles = [file for file in inputs if file.suffix.lower() in BUNDLE_EXTS]
    ids = {match.lower() for file in inputs for match in CHARACTER.findall(file.name)}
    ids.update(character_hints(request))
    ids.update(match.lower() for binding in bindings.values() for match in CHARACTER.findall(binding['bundle']))
    if (len(ids) > 40 and request.get('baselineBundles') is None) or len(bundles) > 100:
        raise ValueError("Process one character Mod at a time")
    requested = [file.relative_to(root).as_posix() for file in inputs] + [item['bundle'] for item in bindings.values()]
    exact_bundles = request.get('baselineBundles')
    if exact_bundles is not None:
        if not isinstance(exact_bundles, list) or not exact_bundles or len(exact_bundles) > 100 or any(not isinstance(value, str) or not value.lower().endswith('.ab') for value in exact_bundles):
            raise ValueError('Invalid current PC bundle selection')
        exact_bundles = [relative_path(value) for value in exact_bundles]
    baseline, warnings, baseline_fingerprint = current_records(roots, set() if exact_bundles else ids, exact_bundles or requested)
    reference = {}
    for record in baseline:
        reference.setdefault(normalized(record['target']), []).append(record)
    candidates = []
    platforms = set()
    if bundles:
        for bundle in bundles:
            records, skipped, targets = bundle_records(bundle)
            warnings.extend(skipped)
            for record in records:
                record['source'] = bundle.relative_to(root).as_posix()
            candidates.extend(records)
            platforms.update(targets)
        warnings.append({"code": "resourceOnly"})
        for file in inputs:
            if file.suffix.lower() not in BUNDLE_EXTS:
                warnings.append({"code": "bundleCompanion", "file": file.relative_to(root).as_posix()})
    else:
        for file in inputs:
            if file.suffix.lower() not in (".png", ".atlas", ".skel", ".json", ".wav", ".txt", ".ini"):
                warnings.append({"code": "unsupportedFile", "file": file.relative_to(root).as_posix()})
                continue
            binding = bindings.get(file, {})
            source = file.relative_to(root).as_posix()
            # A Mod that keeps the game folder layout (charavatars/elite/…)
            # already names its target; loose files keep their bare name and
            # are resolved by the appearance / cosmetic alias rules below.
            target = binding.get('target') or ui.target_from_source(source) or appearance.target_from_source(source)
            if not target and file.suffix.lower() == '.wav':
                target = voice.target_from_source(source, voice_hints, hint_character)
            if not target:
                expanded = ui_targets_for(roots, file.name)
                if len(expanded) > 1:
                    # A legacy role such as shareBg applies to a group of
                    # textures (sdk_0..4): one candidate per target.
                    for item_target in expanded:
                        candidates.append({'target': item_target, 'bundle': binding.get('bundle'), 'kind': 'loose', 'file': file, 'source': source, 'asset': file.name})
                    continue
                target = expanded[0] if expanded else file.name
            candidates.append({'target': target, 'bundle': binding.get('bundle'), 'kind': 'loose', 'file': file, 'source': source, 'asset': file.name})
    plans = []
    payloads = {}
    # Appearance assets are addressed by asset name in mobile Mods; index the
    # current PC records by that name so a bare char_002_amiya.png can still be
    # repaired to charavatars_char_002_amiya.png.
    appearance_reference = {}
    for record in baseline:
        if record.get('appearanceAsset'):
            appearance_reference.setdefault(record['appearanceAsset'].casefold(), []).append(record)

    def appearance_choices(item):
        identity = appearance.parse_target(item['target']) or appearance.identity(item['source'])
        asset = identity[2] if identity else None
        if not asset:
            return []
        choices = appearance_reference.get(asset.casefold(), [])
        if identity[0]:
            choices = [choice for choice in choices if choice.get('assetKind') == identity[0]]
        if len(choices) > 1 and item.get('file') and item['file'].suffix.lower() == '.png':
            try:
                from PIL import Image
                with Image.open(item['file']) as image:
                    size = tuple(image.size)
            except Exception:
                size = None
            # Avatar and portrait assets can share a name; the native size of
            # the PC texture separates them without guessing.
            sized = [choice for choice in choices if tuple(choice.get('appearanceNativeSize') or ()) == size] if size else []
            if len(sized) == 1:
                choices = sized
        return choices

    def candidate_choices(item):
        choices = reference.get(normalized(item['target']), [])
        if item.get('bundle'):
            choices = [choice for choice in choices if choice['bundle'].casefold() == item['bundle'].casefold()]
        elif len(choices) > 1 and item['kind'] != 'loose':
            # A preserved skinpack/chararts path can disambiguate an imported AB.
            scoped = [choice for choice in choices if item['source'].casefold().endswith(choice['bundle'].casefold())]
            if len(scoped) == 1:
                choices = scoped
        return choices

    # Voice lines are addressed by language group and clip name; legacy Mods
    # keep the Unity path id in the file name as a fallback.
    voice_by_clip = {}
    voice_by_id = {}
    voice_known = {}
    for record in baseline:
        if record.get('assetKind') != 'voice':
            continue
        clip = str(record.get('voiceClip') or '').casefold()
        if clip:
            voice_by_clip.setdefault((record.get('voiceGroup'), clip), []).append(record)
        voice_by_id[record.get('voicePathId')] = record
        if record.get('voiceCharacter'):
            voice_known.setdefault(record['voiceGroup'], set()).add(record['voiceCharacter'])
    voice_known = {group: sorted(values, key=len, reverse=True) for group, values in voice_known.items()}

    def voice_choices(item):
        parsed = voice.split_target(item['target'], voice_known)
        if parsed:
            group, character, clip = parsed
            choices = [record for record in voice_by_clip.get((group, clip.casefold()), []) if record.get('voiceCharacter', '').casefold() == character.casefold()]
            if len(choices) == 1:
                return choices
        path_id = voice.clip_path_id(item['source'])
        if path_id is not None and path_id in voice_by_id:
            return [voice_by_id[path_id]]
        return []

    merged_alphas = set()
    for candidate in candidates:
        if not candidate.get('alphaObject'):
            continue
        choices = candidate_choices(candidate)
        if len(choices) == 1 and not choices[0].get('alphaObject'):
            if choices[0].get('alphaAmbiguous'):
                raise ValueError('Current PC texture has ambiguous alpha references')
            merged_alphas.add(id(candidate['alphaObject']))
    for item in candidates:
        choices = candidate_choices(item)
        if not choices:
            choices = appearance_choices(item)
        if not choices and item.get('file') and item['file'].suffix.lower() == '.wav':
            choices = voice_choices(item)
        matched = len(choices) == 1
        current = choices[0] if matched else None
        if not current and item.get('object') and id(item['object']) in merged_alphas:
            warnings.append({'code': 'alphaMerged', 'file': item['source'], 'asset': item['asset']})
            continue
        merge_alpha = bool(current and item.get('alphaObject') and not current.get('alphaObject'))
        name = current['target'] if current else item['target']
        # Only an exact, unique current resource identity may repair old naming.
        if len(choices) > 1:
            warnings.append({"code": "ambiguousAsset", "file": item["source"], "asset": name})
            continue
        bundle = current['bundle'] if current else item.get('bundle')
        native = tuple(current.get('appearanceNativeSize') or ()) if current else tuple(item.get('appearanceNativeSize') or ())
        kind = (current.get('assetKind') if current else item.get('assetKind')) or None
        relative = ('resources/' + hashlib.sha256(bundle.lower().encode()).hexdigest()[:16] + '/' if bundle else '') + safe_name(name)
        comparison = 'unknown'
        loose_size = None
        audio_duration = None
        if item["kind"] == "loose":
            data = item["file"].read_bytes()
            if item["file"].suffix.lower() == ".png":
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    loose_size = image.size
                    image.verify()
        else:
            obj = item["object"].read()
            if item["kind"] == "texture":
                image = texture_image(item, merge_alpha)
                if item.get('alphaResampling'):
                    warnings.append({'code': 'alphaResampled', 'file': item['source'], 'asset': item['asset'], **item['alphaResampling']})
                if image.width * image.height > 64 * 1024 * 1024:
                    raise ValueError("Texture is too large")
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                data = stream.getvalue()
            else:
                script = obj.m_Script
                data = script.encode("utf-8", "surrogateescape") if isinstance(script, str) else bytes(script)
        if current:
            baseline_object = current['object'].read()
            if current['kind'] == 'texture':
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    if image.width * image.height > 64 * 1024 * 1024:
                        raise ValueError('Texture is too large')
                    baseline_size = (baseline_object.m_Width, baseline_object.m_Height)
                    if image.size != baseline_size and not native:
                        warnings.append({'code': 'dimensionsChanged', 'file': name, 'asset': f'{image.width}×{image.height} → {baseline_size[0]}×{baseline_size[1]}'})
                    if native and tuple(image.size) != native:
                        warnings.append({'code': 'appearanceNativeSize', 'file': name, 'asset': f'{image.width}×{image.height} → {native[0]}×{native[1]}'})
                    comparison = 'identical' if image.size == baseline_size and image.convert('RGBA').tobytes() == baseline_object.image.convert('RGBA').tobytes() else 'different'
            elif current['kind'] == 'audio':
                # Voice replacement is validated now so an unsupported WAV is
                # reported before the user tries to launch the game.
                try:
                    pcm, rate, channels = voice.wav_pcm16(item['file'])
                    audio_duration = len(pcm) / (2 * channels * rate)
                except Exception as error:
                    warnings.append({'code': 'unsupportedAudio', 'file': name, 'asset': str(error)[:160]})
                    continue
                if current.get('voiceDuration') and abs(audio_duration - current['voiceDuration']) > 3600:
                    warnings.append({'code': 'voiceDurationChanged', 'file': name, 'asset': f"{current['voiceDuration']:.1f}s → {audio_duration:.1f}s"})
                comparison = 'unknown'
            else:
                original = baseline_object.m_Script
                original = original.encode('utf-8', 'surrogateescape') if isinstance(original, str) else bytes(original)
                comparison = 'identical' if original == data else 'different'
        # Avatar and portrait textures are addressed by fixed sprite rects, so a
        # differently sized image is scaled to the native size during the build.
        if native and loose_size and not current and tuple(loose_size) != native:
            warnings.append({'code': 'appearanceNativeSize', 'file': name, 'asset': f'{loose_size[0]}×{loose_size[1]} → {native[0]}×{native[1]}'})
        # A bare illustration name can also be a portrait file name.  Keep the
        # established illustration binding but point at the appearance folder.
        if loose_size and not kind:
            identity = appearance.identity(item['source'])
            hints = [record for record in appearance_reference.get(identity[2].casefold(), []) if tuple(record.get('appearanceNativeSize') or ()) == tuple(loose_size)] if identity and identity[2] else []
            if len(hints) == 1:
                warnings.append({'code': 'appearanceNameConflict', 'file': name, 'asset': hints[0]['target']})
        key = relative.casefold()
        if key in payloads:
            if payloads[key][1] != data:
                raise ValueError("Conflicting output resource: " + relative)
            continue
        plan_id = hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]
        payloads[key] = (relative, data, plan_id)
        plans.append({'id': plan_id, 'source': item['source'], 'asset': item['asset'], 'target': relative, 'resource': name, 'bundle': bundle, 'matched': matched, 'changed': name != item['target'], 'comparison': comparison, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'extraTexture': '__texture_' in name, 'assetKind': kind, 'nativeSize': list(native) if native else None, **({'voiceClip': current.get('voiceClip'), 'voiceGroup': current.get('voiceGroup'), 'voiceDuration': round(audio_duration, 2)} if kind == 'voice' and current and audio_duration else {}), **({'alphaMergedFrom': item['alphaObject'].read().m_Name} if merge_alpha else {}), **({'alphaResampling': item['alphaResampling']} if item.get('alphaResampling') else {})})
    if not plans:
        raise ValueError("No supported resource bindings found")
    if not roots:
        warnings.append({"code": "noBaseline"})
    elif any(not plan["matched"] and CHARACTER.search(plan["target"]) for plan in plans):
        warnings.append({"code": "unmatched"})
    if payload_sink is not None:
        payload_sink.update(payloads)
    selected_ids = export_selected(request, plans, payloads, bool(bundles)) if output else set()
    return {'success': True, 'kind': 'bundle' if bundles else 'm4a', 'platforms': sorted(platforms), 'plans': plans, 'warnings': warnings, 'matched': sum(p['matched'] for p in plans), 'renamed': sum(p['changed'] for p in plans), 'written': len(selected_ids), 'baselineCount': len(baseline), 'baselineFingerprint': baseline_fingerprint}


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.buffer.read(1024 * 1024).decode("utf-8"))
        with contextlib.redirect_stdout(sys.stderr):
            result = run(request)
    except Exception as error:
        result = {"success": False, "error": str(error)[:1000]}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
