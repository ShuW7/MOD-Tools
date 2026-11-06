import os, sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(os.environ.get('QAQR_ARKNIGHTS_TOOLS', Path(__file__).resolve().parents[1])) / 'python'))
import appearance_assets


class Naming(unittest.TestCase):
    def test_container_family(self):
        self.assertEqual(appearance_assets.family_of_container('dyn/arts/charavatars/char_002_amiya.png'), 'avatar')
        self.assertEqual(appearance_assets.family_of_container('dyn/arts/charportraits/skins/char_002_amiya_epoque#4.png'), 'portrait')
        self.assertIsNone(appearance_assets.family_of_container('dyn/arts/chararts/illust_char_002_amiya_1.png'))
        self.assertIsNone(appearance_assets.family_of_container(''))

    def test_target_names_round_trip(self):
        cases = {
            'dyn/arts/charavatars/char_002_amiya.png': ('avatar', '', 'char_002_amiya', 'charavatars_char_002_amiya.png'),
            'dyn/arts/charavatars/elite/char_002_amiya_2.png': ('avatar', 'elite', 'char_002_amiya_2', 'charavatars_elite_char_002_amiya_2.png'),
            'dyn/arts/charavatars/skins/char_2025_shu_nlc#1.png': ('avatar', 'skins', 'char_2025_shu_nlc#1', 'charavatars_skins_char_2025_shu_nlc#1.png'),
            'dyn/arts/charportraits/char_002_amiya_1.png': ('portrait', '', 'char_002_amiya_1', 'charportraits_char_002_amiya_1.png'),
        }
        for container, (family, folder, asset, target) in cases.items():
            self.assertEqual(appearance_assets.container_parts(container), (family, folder, asset))
            self.assertEqual(appearance_assets.target_name(container), target)
            self.assertEqual(appearance_assets.parse_target(target), (family, folder, asset))

    def test_parse_target_rejects_foreign_names(self):
        for value in ('char_002_amiya.png', 'illust_char_002_amiya_1.png', 'charavatars_char_002_amiya.skel', '', None):
            self.assertIsNone(appearance_assets.parse_target(value))

    def test_folder_layout_inference(self):
        self.assertEqual(appearance_assets.target_from_source('charavatars/elite/char_002_amiya_2.png'), 'charavatars_elite_char_002_amiya_2.png')
        self.assertEqual(appearance_assets.target_from_source(r'charportraits\skins\char_2025_shu_nlc#1.png'), 'charportraits_skins_char_2025_shu_nlc#1.png')
        self.assertIsNone(appearance_assets.target_from_source('chararts/char_002_amiya_1.png'))
        self.assertIsNone(appearance_assets.target_from_source('char_002_amiya.png'))

    def test_identity_of_bare_name(self):
        self.assertEqual(appearance_assets.identity('char_002_amiya.png'), (None, '', 'char_002_amiya'))
        self.assertEqual(appearance_assets.identity('charavatars_elite_char_002_amiya_2.png'), ('avatar', 'elite', 'char_002_amiya_2'))

    def test_operator_matching_includes_summons(self):
        self.assertTrue(appearance_assets.id_matches('char_002_amiya_2', {'char_002_amiya'}))
        self.assertTrue(appearance_assets.id_matches('char_1033_swire2_nlc#1', {'char_1033_swire2'}))
        self.assertTrue(appearance_assets.id_matches('token_10031_swire2_gdtrap', {'char_1033_swire2'}))
        self.assertFalse(appearance_assets.id_matches('char_002_amiya', {'char_002_am'}))
        self.assertFalse(appearance_assets.id_matches('trap_100_swi', {'char_1033_swire2'}))

    def test_page_order(self):
        pages = ['spritepack/ui_char_avatar_10.ab', 'spritepack/ui_char_avatar_2.ab', 'spritepack/ui_char_avatar_0.ab']
        self.assertEqual(sorted(pages, key=appearance_assets.page_sort_key), [pages[2], pages[1], pages[0]])


class Resolution(unittest.TestCase):
    def setUp(self):
        appearance_assets.clear_cache()
        self.roots = ['C:/game/StreamingAssets', 'C:/game/PersistentData']
        self.entries = [
            {'bundle': 'spritepack/ui_char_avatar_0.ab', 'container': 'dyn/arts/charavatars/elite/char_002_amiya_2.png', 'family': 'avatar', 'folder': 'elite', 'asset': 'char_002_amiya_2', 'target': 'charavatars_elite_char_002_amiya_2.png', 'pathId': 1},
            {'bundle': 'spritepack/ui_char_avatar_8.ab', 'container': 'dyn/arts/charavatars/char_002_amiya.png', 'family': 'avatar', 'folder': '', 'asset': 'char_002_amiya', 'target': 'charavatars_char_002_amiya.png', 'pathId': 2},
            {'bundle': 'spritepack/char_portrait_4.ab', 'container': 'dyn/arts/charportraits/char_002_amiya_1.png', 'family': 'portrait', 'folder': '', 'asset': 'char_002_amiya_1', 'target': 'charportraits_char_002_amiya_1.png', 'pathId': 3},
        ]
        self.pages = [(entry['bundle'], Path('C:/game/StreamingAssets') / entry['bundle']) for entry in self.entries]

    def resolve(self, identifiers=(), targets=(), source_items=()):
        with mock.patch.object(appearance_assets, 'inventory', return_value=tuple((relative, file, 1, 1) for relative, file in self.pages)), \
             mock.patch.object(appearance_assets, 'entries', return_value=self.entries):
            return sorted(relative for relative, _file in appearance_assets.resolve_bundles(self.roots, identifiers, targets, source_items))

    def test_operator_identifiers_select_pages(self):
        self.assertEqual(self.resolve(['char_002_amiya']), ['spritepack/char_portrait_4.ab', 'spritepack/ui_char_avatar_0.ab', 'spritepack/ui_char_avatar_8.ab'])
        self.assertEqual(self.resolve(['char_999_none']), [])

    def test_canonical_targets_select_pages(self):
        self.assertEqual(self.resolve(targets=['charavatars_char_002_amiya.png']), ['spritepack/ui_char_avatar_8.ab'])
        self.assertEqual(self.resolve(targets=['charportraits_char_002_amiya_1.png']), ['spritepack/char_portrait_4.ab'])

    def test_folder_layout_source_selects_pages(self):
        self.assertEqual(self.resolve(source_items=['charavatars/elite/char_002_amiya_2.png']), ['spritepack/ui_char_avatar_0.ab'])

    def test_empty_request_scans_nothing(self):
        with mock.patch.object(appearance_assets, 'inventory', side_effect=AssertionError('scan')):
            self.assertEqual(appearance_assets.resolve_bundles(self.roots), [])


if __name__ == '__main__':
    unittest.main()
