"""Offline PC integration checks. Reads a user-selected game; never launches it."""
import argparse
import collections
import hashlib
import json
import wave
from pathlib import Path
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from worker import bundle_records, run
from bundle_builder import build_bundles, digest
from pc_layout import RESOURCE_MANIFEST, current_bundles, game_roots, read_bindings
from ui_cosmetics import resolve_bundles as cosmetic_bundles
import voice as voice_assets


def verify(game, output, audit_dynamic=True):
    roots = game_roots(str(game))
    output = output.resolve()
    if output.is_relative_to(game.parent):
        raise ValueError('Test output must be outside the game')
    output.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='pc-layout-', dir=output))
    originals = dict(current_bundles(roots, {'char_2025_shu', 'char_1035_wisdel', 'char_003_kalts', 'char_124_kroos', 'char_2014_nian'}))
    before = {name: digest(file) for name, file in originals.items()}
    # Downloaded bundles win; an updated installation may also ship the bundle
    # directly under StreamingAssets.
    shu_candidates = [root / 'skinpack/char_2025_shu.ab' for root in roots if (root / 'skinpack/char_2025_shu.ab').is_file()]
    assert shu_candidates and originals['skinpack/char_2025_shu.ab'] == shu_candidates[0]
    assert 'arts/dynchars/char_2025_shu_nlc#1.ab' in originals
    assert 'arts/dynchars/char_2025_shu_2.ab' in originals
    changes = [
        ('spring', 'skinpack/char_2025_shu.ab', 'illust_char_2025_shu_nian#11.png'),
        ('spring', 'arts/dynchars/char_2025_shu_nian#11.ab', 'dyn_illust_char_2025_shu_nian#11.png'),
        ('spring', 'arts/dynchars/char_2025_shu_nian#11.ab', 'dyn_portrait_char_2025_shu_nian#11.png'),
        ('river', 'skinpack/char_2025_shu.ab', 'illust_char_2025_shu_nlc#1.png'),
        ('river', 'arts/dynchars/char_2025_shu_nlc#1.ab', 'dyn_illust_char_2025_shu_nlc#1.png'),
        ('default', 'arts/dynchars/char_2025_shu_2.ab', 'dyn_illust_char_2025_shu.png'),
        ('entrance', 'arts/dynchars/char_1035_wisdel_sale#14.ab', 'dyn_illust_char_1035_wisdel_sale#14_Start.png'),
        ('same-name-main', 'arts/dynchars/char_003_kalts_boc#6.ab', 'dyn_illust_char_003_kalts_boc#6.png'),
        ('same-name-entrance', 'arts/dynchars/char_003_kalts_boc#6.ab', 'dyn_entrance_char_003_kalts_boc#6.png'),
        ('special', 'arts/dynchars/char_124_kroos_sale#14.ab', 'sp_dyn_illust_char_124_kroos_sale#14.png'),
        ('multi-page', 'arts/dynchars/char_2014_nian_2.ab', 'dyn_illust_char_2014_nian2__page1.png'),
        ('multi-page', 'arts/dynchars/char_2014_nian_2.ab', 'dyn_illust_char_2014_nian2__page2.png'),
    ]
    manifests = collections.defaultdict(list)
    cache = {}
    for mod, bundle, target in changes:
        if bundle not in cache:
            cache[bundle] = bundle_records(originals[bundle])[0]
        record = next(record for record in cache[bundle] if record['target'].lower() == target.lower())
        image = record['object'].read().image.convert('RGBA')
        pixel = image.getpixel((0, 0))
        image.putpixel((0, 0), ((pixel[0] + 1) % 256, *pixel[1:]))
        folder = scratch / 'enabled' / mod
        folder.mkdir(parents=True, exist_ok=True)
        image.save(folder / target)
        manifests[folder].append({'file': target, 'target': target, 'bundle': bundle})
    for folder, resources in manifests.items():
        (folder / RESOURCE_MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'resources': resources}), encoding='utf-8')
    result = build_bundles({'source': str(scratch / 'enabled'), 'gamePath': str(game), 'output': str(scratch / 'rebuilt')})
    assert result['bundleCount'] == 8, result
    assert result['resourceCount'] == 12, result
    skin = next(item for item in result['bundles'] if item['replacement'] == 'bundles/skinpack/char_2025_shu.ab')
    assert len(skin['resources']) == 2, skin

    # Feed the merged whole bundle back through conversion and select one skin.
    whole = scratch / 'whole-bundle' / 'skinpack'
    whole.mkdir(parents=True)
    shutil.copyfile(scratch / 'rebuilt' / skin['replacement'], whole / 'char_2025_shu.ab')
    request = {'source': str(whole.parent), 'gamePath': str(game)}
    analysis = run(request)
    assert sum(plan['comparison'] == 'different' for plan in analysis['plans']) == 2
    selected = [plan['id'] for plan in analysis['plans'] if plan['resource'] == 'illust_char_2025_shu_nian#11.png']
    assert len(selected) == 1
    converted = run({**request, 'output': str(scratch / 'selected'), 'selectedIds': selected})
    assert converted['written'] == 1
    assert len(read_bindings(scratch / 'selected')) == 1
    result_selected = build_bundles({'source': str(scratch / 'selected'), 'gamePath': str(game), 'output': str(scratch / 'selected-rebuilt')})
    assert result_selected['bundleCount'] == 1 and result_selected['resourceCount'] == 1
    # Streaming failure must leave no output or temporary package tree.
    broken = scratch / 'broken-resources'
    shutil.copytree(scratch / 'enabled/spring', broken)
    (broken / 'illust_char_2025_shu_nian#11.png').write_bytes(b'invalid PNG')
    failed_output = scratch / 'failed-bundles'
    try:
        build_bundles({'source': str(broken), 'gamePath': str(game), 'output': str(failed_output)})
        raise AssertionError('Corrupt image was accepted')
    except (ValueError, OSError):
        pass
    assert not failed_output.exists() and not list(scratch.glob('.qaqr-bundles-*'))

    loose = scratch / 'legacy-multipage'
    loose.mkdir()
    shutil.copyfile(scratch / 'enabled/multi-page/dyn_illust_char_2014_nian2__page2.png', loose / 'dyn_illust_char_2014_nian2__page2.png')
    hints = {'source': str(loose), 'gamePath': str(game), 'characterIds': ['char_2014_nian']}
    assert run(hints)['matched'] == 1
    assert build_bundles({**hints, 'output': str(scratch / 'loose-rebuilt')})['bundleCount'] == 1

    # Avatars and half-body portraits are packed into shared page bundles.
    # A Mod keeps the game folder layout; the owning page is resolved from the
    # container tables and the bundle is rebuilt as a whole.
    from PIL import Image
    appearance = scratch / 'appearance' / 'enabled' / 'loose'
    (appearance / 'charavatars' / 'elite').mkdir(parents=True)
    (appearance / 'charportraits').mkdir(parents=True)
    Image.new('RGBA', (180, 180), (11, 22, 33, 255)).save(appearance / 'charavatars' / 'char_002_amiya.png')
    # A differently sized avatar is scaled to the native 180x180 texture.
    Image.new('RGBA', (512, 512), (44, 55, 66, 255)).save(appearance / 'charavatars' / 'elite' / 'char_002_amiya_2.png')
    Image.new('RGBA', (180, 360), (77, 88, 99, 255)).save(appearance / 'charportraits' / 'char_002_amiya_2.png')
    appearance_request = {'source': str(appearance), 'gamePath': str(game), 'characterIds': ['char_002_amiya']}
    appearance_analysis = run(appearance_request)
    appearance_plans = {plan['resource']: plan for plan in appearance_analysis['plans']}
    assert appearance_analysis['matched'] == 3, appearance_analysis
    assert appearance_plans['charavatars_char_002_amiya.png']['assetKind'] == 'avatar'
    assert appearance_plans['charavatars_elite_char_002_amiya_2.png']['nativeSize'] == [180, 180]
    assert appearance_plans['charportraits_char_002_amiya_2.png']['assetKind'] == 'portrait'
    assert any(warning['code'] == 'appearanceNativeSize' for warning in appearance_analysis['warnings'])
    appearance_copy = run({**appearance_request, 'output': str(scratch / 'appearance' / 'converted'), 'selectedIds': [plan['id'] for plan in appearance_analysis['plans']]})
    assert appearance_copy['written'] == 3, appearance_copy
    assert len(read_bindings(scratch / 'appearance' / 'converted')) == 3
    appearance_build = build_bundles({'source': str(scratch / 'appearance' / 'converted'), 'gamePath': str(game), 'output': str(scratch / 'appearance' / 'rebuilt')})
    assert appearance_build['bundleCount'] == 3, appearance_build
    pages = dict(current_bundles(roots, {'char_002_amiya'}))
    pages_before = {relative: digest(file) for relative, file in pages.items()}
    import UnityPy
    from appearance_assets import read_page
    expectations = [
        ('spritepack/ui_char_avatar_8.ab', 'dyn/arts/charavatars/char_002_amiya.png', (180, 180), (11, 22, 33)),
        ('spritepack/ui_char_avatar_0.ab', 'dyn/arts/charavatars/elite/char_002_amiya_2.png', (180, 180), (44, 55, 66)),
        ('spritepack/char_portrait_4.ab', 'dyn/arts/charportraits/char_002_amiya_2.png', (180, 360), (77, 88, 99)),
    ]
    for relative, container, size, color in expectations:
        rebuilt = scratch / 'appearance' / 'rebuilt' / next(item for item in appearance_build['bundles'] if item['logical'] == relative)['replacement']
        rebuilt_containers = {record[0] for record in read_page(rebuilt)}
        assert container in rebuilt_containers, (relative, container)
        # Only replacement bundles are rewritten; the container table is intact.
        assert rebuilt_containers == {record[0] for record in read_page(pages[relative])}, relative
        env = UnityPy.load(str(rebuilt))
        path_id = next(record[5] for record in read_page(rebuilt) if record[0] == container)
        texture = next(obj for obj in env.objects if obj.path_id == path_id and obj.type.name == 'Texture2D').read()
        image = texture.image.convert('RGBA')
        assert image.size == size, (relative, image.size)
        assert image.getpixel((size[0] // 2, size[1] // 2))[:3] == color, (relative, image.getpixel((size[0] // 2, size[1] // 2)))
        assert digest(pages[relative]) == pages_before[relative]
    appearance_audit = {'bundleCount': appearance_build['bundleCount'], 'resourceCount': appearance_build['resourceCount'], 'pages': sorted({item['logical'] for item in appearance_build['bundles']})}

    # Name card styles and home themes are one bundle per style/theme. The Mod
    # keeps the bundle folder layout; the target also names the bundle.
    def page_textures(path):
        env = UnityPy.load(str(path))
        bundle = next(obj.read() for obj in env.objects if obj.type.name == 'AssetBundle')
        objects = {obj.path_id: obj for obj in env.objects}
        mapping = {}
        for container, info in bundle.m_Container:
            for pointer in bundle.m_PreloadTable[info.preloadIndex:info.preloadIndex + info.preloadSize]:
                if pointer.m_FileID == 0 and pointer.m_PathID in objects and objects[pointer.m_PathID].type.name == 'Texture2D':
                    mapping.setdefault(container, objects[pointer.m_PathID])
        return bundle, mapping

    cosmetics = scratch / 'cosmetics' / 'enabled' / 'loose'
    (cosmetics / 'namecardskin' / 'nc_arc_1').mkdir(parents=True)
    (cosmetics / 'hometheme' / 'tm_mainline_1').mkdir(parents=True)
    Image.new('RGBA', (1280, 720), (10, 200, 180, 255)).save(cosmetics / 'namecardskin' / 'nc_arc_1' / 'bg.png')
    # A differently sized home theme button is scaled to the 828x232 sprite.
    Image.new('RGBA', (512, 512), (200, 10, 60, 255)).save(cosmetics / 'hometheme' / 'tm_mainline_1' / 'btn_battle.png')
    cosmetic_request = {'source': str(cosmetics), 'gamePath': str(game)}
    cosmetic_analysis = run(cosmetic_request)
    cosmetic_plans = {plan['resource']: plan for plan in cosmetic_analysis['plans']}
    assert set(cosmetic_plans) == {'namecardskin_nc_arc_1_bg.png', 'hometheme_tm_mainline_1_btn_battle.png'}, cosmetic_plans
    assert cosmetic_plans['namecardskin_nc_arc_1_bg.png']['assetKind'] == 'namecard'
    assert cosmetic_plans['hometheme_tm_mainline_1_btn_battle.png']['assetKind'] == 'hometheme'
    assert any(warning['code'] == 'appearanceNativeSize' for warning in cosmetic_analysis['warnings'])
    cosmetic_files = dict(cosmetic_bundles(roots, ['namecardskin_nc_arc_1_bg.png', 'hometheme_tm_mainline_1_btn_battle.png']))
    cosmetic_before = {relative: digest(file) for relative, file in cosmetic_files.items()}
    cosmetic_copy = run({**cosmetic_request, 'output': str(scratch / 'cosmetics' / 'converted'), 'selectedIds': [plan['id'] for plan in cosmetic_analysis['plans']]})
    assert cosmetic_copy['written'] == 2, cosmetic_copy
    cosmetic_build = build_bundles({'source': str(scratch / 'cosmetics' / 'converted'), 'gamePath': str(game), 'output': str(scratch / 'cosmetics' / 'rebuilt')})
    assert cosmetic_build['bundleCount'] == 2, cosmetic_build
    for relative, container, size, color in [
        ('arts/ui/namecardskin/[uc]nc_arc_1.ab', 'dyn/arts/ui/namecardskin/[uc]nc_arc_1/arts/bg.png', (1280, 720), (10, 200, 180)),
        ('arts/ui/hometheme/[uc]tm_mainline_1.ab', 'dyn/arts/ui/hometheme/[uc]tm_mainline_1/btn_battle.png', (828, 232), (200, 10, 60)),
    ]:
        rebuilt = scratch / 'cosmetics' / 'rebuilt' / next(item for item in cosmetic_build['bundles'] if item['logical'] == relative)['replacement']
        bundle, textures = page_textures(rebuilt)
        original_bundle, _original_textures = page_textures(cosmetic_files[relative])
        assert container in textures, (relative, container)
        assert sorted(c for c, _ in bundle.m_Container) == sorted(c for c, _ in original_bundle.m_Container), relative
        assert all(platform in (5, 19) for platform in {int(asset.target_platform) for asset in UnityPy.load(str(rebuilt)).assets})
        image = textures[container].read().image.convert('RGBA')
        assert image.size == size, (relative, image.size)
        assert image.getpixel((size[0] // 2, size[1] // 2))[:3] == color, (relative, image.getpixel((size[0] // 2, size[1] // 2)))
        assert digest(cosmetic_files[relative]) == cosmetic_before[relative]
    cosmetic_audit = {'bundleCount': cosmetic_build['bundleCount'], 'resourceCount': cosmetic_build['resourceCount'], 'bundles': sorted({item['logical'] for item in cosmetic_build['bundles']})}

    # Voice lines replace AudioClip samples. The rebuilt resource must keep a
    # valid PCM FSB5 bank and leave every other clip byte-identical.
    voice_dir = scratch / 'voice' / 'enabled' / 'loose'
    (voice_dir / 'wav').mkdir(parents=True)
    (voice_dir / 'mod.ini').write_text('type = audio\nvoice_group = Voice_CN\ncharacter = char_003_kalts\n', encoding='utf-8')
    wav_file = voice_dir / 'wav' / 'CN_001_1234567890123456789.wav'
    with wave.open(str(wav_file), 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(32000)
        target.writeframes(b'\x01\x02' * 32000)
    voice_request = {'source': str(voice_dir), 'gamePath': str(game), 'characterIds': ['char_003_kalts']}
    voice_analysis = run(voice_request)
    assert len(voice_analysis['plans']) == 1 and voice_analysis['matched'] == 1, voice_analysis
    assert voice_analysis['plans'][0]['assetKind'] == 'voice', voice_analysis['plans'][0]
    voice_files = dict(voice_assets.resolve_bundles(roots, ['char_003_kalts'], ['voice_cn_char_003_kalts_CN_001.wav']))
    voice_relative = 'audio/sound_beta_2/voice_cn/char_003_kalts.ab'
    voice_before = digest(voice_files[voice_relative])
    voice_copy = run({**voice_request, 'output': str(scratch / 'voice' / 'converted'), 'selectedIds': [plan['id'] for plan in voice_analysis['plans']]})
    assert voice_copy['written'] == 1, voice_copy
    voice_build = build_bundles({'source': str(scratch / 'voice' / 'converted'), 'gamePath': str(game), 'output': str(scratch / 'voice' / 'rebuilt')})
    assert voice_build['bundleCount'] == 1 and voice_build['resourceCount'] == 1, voice_build
    voice_rebuilt = scratch / 'voice' / 'rebuilt' / voice_build['bundles'][0]['replacement']
    original_env = UnityPy.load(str(voice_files[voice_relative]))
    original_resource_name = next(name for name in original_env.file.files if name.lower().endswith('.resource'))
    original_resource = original_env.file.files[original_resource_name].bytes
    checked_env = UnityPy.load(str(voice_rebuilt))
    checked_resource_name = next(name for name in checked_env.file.files if name.lower().endswith('.resource'))
    checked_resource = checked_env.file.files[checked_resource_name].bytes
    changed_clip = next(obj for obj in checked_env.objects if obj.type.name == 'AudioClip' and obj.read_typetree()['m_Name'] == 'CN_001')
    tree = changed_clip.read_typetree()
    assert int(tree['m_CompressionFormat']) == 0 and int(tree['m_Frequency']) == 32000 and int(tree['m_Channels']) == 1, tree
    offset, size = int(tree['m_Resource']['m_Offset']), int(tree['m_Resource']['m_Size'])
    assert checked_resource[offset:offset + 4] == b'FSB5', (offset, size)
    assert size > 64000, size
    untouched = next(obj for obj in checked_env.objects if obj.type.name == 'AudioClip' and obj.read_typetree()['m_Name'] == 'CN_002')
    untouched_tree = untouched.read_typetree()
    untouched_offset, untouched_size = int(untouched_tree['m_Resource']['m_Offset']), int(untouched_tree['m_Resource']['m_Size'])
    assert checked_resource[untouched_offset:untouched_offset + untouched_size] == original_resource[untouched_offset:untouched_offset + untouched_size]
    assert digest(voice_files[voice_relative]) == voice_before
    voice_audit = {'bundleCount': voice_build['bundleCount'], 'resourceCount': voice_build['resourceCount'], 'clip': 'CN_001', 'bundle': voice_relative, 'replacementBytes': size}

    # Prebuilt AssetBundle Mods are redirected by the loader and never repacked.
    source_bundle = game.parent / 'Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper/live_007_closre.ab'
    ab_audit = None
    if source_bundle.is_file():
        ab_dir = scratch / 'assetbundle' / 'enabled' / 'loose'
        (ab_dir / 'shopkeeper').mkdir(parents=True)
        shutil.copyfile(source_bundle, ab_dir / 'shopkeeper' / 'live_007_closre.ab')
        (ab_dir / 'mod.ini').write_text(
            '[mod]\ntype = assetbundle\n'
            'key_root = Arknights\\Arknights_Data\\StreamingAssets\\AB\\Windows\\prefabs\\shop\\shopkeeper\n\n'
            'bundle_root = shopkeeper\nauto_map_by_stem = true\n', encoding='utf-8')
        ab_analysis = run({'source': str(ab_dir), 'gamePath': str(game)})
        assert ab_analysis['kind'] == 'assetbundle' and ab_analysis['matched'] == 1, ab_analysis
        assert ab_analysis['plans'][0]['assetKind'] == 'assetbundle'
        ab_destination = scratch / 'assetbundle' / 'dest'
        shutil.copytree(ab_dir, ab_destination / 'modid')
        ab_build = build_bundles({'source': str(ab_destination), 'gamePath': str(game), 'output': str(scratch / 'assetbundle' / 'rebuilt')})
        assert ab_build['bundleCount'] == 1 and len(ab_build['assetBundles']) == 1, ab_build
        entry = ab_build['assetBundles'][0]
        assert entry['source'] == 'Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper/live_007_closre.ab', entry
        copied = scratch / 'assetbundle' / 'rebuilt' / entry['replacement']
        assert copied.read_bytes() == (ab_dir / 'shopkeeper' / 'live_007_closre.ab').read_bytes()
        assert digest(copied) == digest(source_bundle)
        manifest = (scratch / 'assetbundle' / 'rebuilt' / 'manifest.tsv').read_text(encoding='utf-8')
        assert 'BUNDLE\t' + entry['source'] in manifest
        patch = build_bundles({'source': str(ab_destination), 'gamePath': str(game), 'output': str(scratch / 'assetbundle' / 'patch'), 'scopeSource': str(ab_destination / 'modid'), 'affectedBundles': []})
        assert patch['bundleCount'] == 1 and patch['assetBundles'], patch
        (ab_destination / 'modid').rename(ab_destination / 'DISABLED_modid')
        removed = build_bundles({'source': str(ab_destination), 'gamePath': str(game), 'output': str(scratch / 'assetbundle' / 'removed'), 'scopeSource': str(ab_destination / 'DISABLED_modid'), 'affectedBundles': []})
        assert removed['bundleCount'] == 0 and removed['removedBundles'], removed
        ab_audit = {'bundleCount': ab_build['bundleCount'], 'bundle': entry['source'], 'replacement': entry['replacement'], 'patchCount': patch['bundleCount'], 'removedCount': len(removed['removedBundles'])}

    # Every installed/downloaded dynamic bundle is parsed, without decoding or
    # exporting full game assets. Record unsupported graph shapes for review.
    dynamic = {}
    for root in reversed(roots) if audit_dynamic else []:
        for file in (root / 'arts/dynchars').glob('*.ab'):
            dynamic[file.name.lower()] = file
    audit = []
    for name, file in sorted(dynamic.items()):
        checksum = digest(file)
        records, skipped, platforms = bundle_records(file)
        assert digest(file) == checksum
        assert all(platform in (5, 19) for platform in platforms)
        audit.append({'bundle': name, 'resources': len(records), 'multiPageResources': sum('__page' in record['target'] or '__atlas' in record['target'] for record in records), 'skipped': skipped})
        if len(audit) % 25 == 0:
            print(json.dumps({'dynamicBundlesRead': len(audit)}), flush=True)
    assert all(digest(originals[name]) == expected for name, expected in before.items())
    report = {'success': True, 'gameStarted': False, 'originalFilesUnchanged': True, 'bundleCount': result['bundleCount'], 'resourceCount': result['resourceCount'], 'selectedCopyCount': converted['written'], 'appearance': appearance_audit, 'cosmetics': cosmetic_audit, 'voice': voice_audit, 'assetBundles': ab_audit, 'dynamicBundlesRead': len(audit), 'dynamicAudit': audit}
    (scratch / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**{key: value for key, value in report.items() if key != 'dynamicAudit'}, 'report': str(scratch / 'report.json')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--game', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--skip-dynamic-audit', action='store_true')
    args = parser.parse_args()
    verify(args.game.resolve(), args.output, not args.skip_dynamic_audit)
