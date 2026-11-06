import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(os.environ.get('QAQR_ARKNIGHTS_TOOLS', Path(__file__).resolve().parents[1])) / 'python'))
import ui_cosmetics


class Naming(unittest.TestCase):
    def test_family_detection(self):
        self.assertEqual(ui_cosmetics.family_of_container('dyn/arts/ui/namecardskin/[uc]nc_arc_1/arts/bg.png'), 'namecard')
        self.assertEqual(ui_cosmetics.family_of_container('dyn/arts/ui/hometheme/[uc]tm_mainline_1/btn_battle.png'), 'hometheme')
        self.assertIsNone(ui_cosmetics.family_of_container('dyn/arts/charavatars/char_002_amiya.png'))
        self.assertEqual(ui_cosmetics.family_of_bundle(r'D:\game\arts\ui\namecardskin\[uc]nc_arc_1.ab'), 'namecard')
        self.assertEqual(ui_cosmetics.family_of_bundle('[uc]tm_mainline_1.ab'), 'hometheme')
        self.assertIsNone(ui_cosmetics.family_of_bundle('ui/[uc]namecardv2.ab'))

    def test_bundle_ids(self):
        self.assertEqual(ui_cosmetics.bundle_id('[uc]nc_as2024_dreamer.ab'), 'nc_as2024_dreamer')
        self.assertEqual(ui_cosmetics.bundle_id(r'C:\x\arts\ui\hometheme\[uc]tm_mainline_1.ab'), 'tm_mainline_1')

    def test_roles(self):
        self.assertEqual(ui_cosmetics.role_name('dyn/arts/ui/namecardskin/[uc]nc_arc_1/arts/bg.png', 'bg', 'nc_arc_1'), 'bg')
        self.assertEqual(ui_cosmetics.role_name('dyn/arts/ui/hometheme/[uc]tm_mainline_1/btn_battle.png', 'btn_battle', 'tm_mainline_1'), 'btn_battle')
        self.assertEqual(ui_cosmetics.role_name('dyn/arts/ui/hometheme/[uc]tm_redvelvet_1/astc/tv/tv_frame.png', 'tv_frame', 'tm_redvelvet_1'), 'astc_tv_tv_frame')
        self.assertEqual(ui_cosmetics.role_name('dyn/arts/ui/hometheme/[uc]tm_redvelvet_1/materials/mat.mat', 'btn_bg_01', 'tm_redvelvet_1'), 'btn_bg_01')

    def test_legacy_role_aliases(self):
        self.assertEqual(ui_cosmetics.normalize_role('namecard', 'largebg'), 'bg')
        self.assertEqual(ui_cosmetics.normalize_role('namecard', 'shareBg'), 'sdk')
        self.assertEqual(ui_cosmetics.normalize_role('namecard', 'btn_battle'), 'btn_battle')
        self.assertEqual(ui_cosmetics.role_prefixes('namecard', 'largebg'), ())
        self.assertEqual(ui_cosmetics.role_prefixes('namecard', 'shareBg'), ('sdk',))
        self.assertEqual(ui_cosmetics.role_prefixes('namecard', 'longbg'), ('name_card_long',))

    def test_target_names(self):
        self.assertEqual(ui_cosmetics.target_name('namecard', 'nc_arc_1', 'bg'), 'namecardskin_nc_arc_1_bg.png')
        self.assertEqual(ui_cosmetics.target_name('hometheme', 'tm_mainline_1', 'btn_battle'), 'hometheme_tm_mainline_1_btn_battle.png')

    def test_split_target_uses_longest_identifier(self):
        identifiers = {'namecard': ['nc_arc_1', 'nc_as2024_dreamer'], 'hometheme': ['tm_mainline_1']}
        self.assertEqual(ui_cosmetics.split_target('namecardskin_nc_as2024_dreamer_name_card_long_0.png', identifiers), ('namecard', 'nc_as2024_dreamer', 'name_card_long_0'))
        self.assertEqual(ui_cosmetics.split_target('hometheme_tm_mainline_1_btn_battle.png', identifiers), ('hometheme', 'tm_mainline_1', 'btn_battle'))
        self.assertIsNone(ui_cosmetics.split_target('charavatars_char_002_amiya.png', identifiers))
        self.assertIsNone(ui_cosmetics.split_target('namecardskin_nc_unknown_bg.png', identifiers))

    def test_community_mod_names_are_accepted(self):
        identifiers = {'namecard': ['nc_as2025_bloomsoftime'], 'hometheme': ['tm_rhodes_day']}
        self.assertEqual(ui_cosmetics.split_target('namecardstyle_nc_as2025_bloomsoftime_largebg.png', identifiers), ('namecard', 'nc_as2025_bloomsoftime', 'bg'))
        self.assertEqual(ui_cosmetics.split_target('namecardstyle_nc_as2025_bloomsoftime_shareBg.png', identifiers), ('namecard', 'nc_as2025_bloomsoftime', 'sdk'))
        self.assertEqual(ui_cosmetics.split_target('tm_rhodes_day_btn_battle.png', identifiers), ('hometheme', 'tm_rhodes_day', 'btn_battle'))
        self.assertEqual(ui_cosmetics.split_target('tm_rhodes_day_btn_battle.png', identifiers, with_original=True), ('hometheme', 'tm_rhodes_day', 'btn_battle', 'btn_battle'))
        self.assertEqual(ui_cosmetics.split_target('namecardstyle_nc_as2025_bloomsoftime_shareBg.png', identifiers, with_original=True), ('namecard', 'nc_as2025_bloomsoftime', 'sdk', 'shareBg'))

    def test_source_layout(self):
        self.assertEqual(ui_cosmetics.target_from_source('namecardskin/nc_arc_1/bg.png'), 'namecardskin_nc_arc_1_bg.png')
        self.assertEqual(ui_cosmetics.target_from_source('namecardstyle/nc_arc_1/largebg.png'), 'namecardskin_nc_arc_1_bg.png')
        self.assertEqual(ui_cosmetics.target_from_source('arts/ui/namecardskin/[uc]nc_arc_1/arts/bg.png'), 'namecardskin_nc_arc_1_bg.png')
        self.assertEqual(ui_cosmetics.target_from_source('hometheme/tm_mainline_1/btn_battle.png'), 'hometheme_tm_mainline_1_btn_battle.png')
        self.assertEqual(ui_cosmetics.target_from_source('arts/ui/hometheme/[uc]tm_redvelvet_1/astc/tv/tv_frame.png'), 'hometheme_tm_redvelvet_1_astc_tv_tv_frame.png')
        # Folders copied straight from the game assets use the id as folder.
        self.assertEqual(ui_cosmetics.target_from_source('[uc]tm_rhodes_day/btn_battle.png'), 'hometheme_tm_rhodes_day_btn_battle.png')
        self.assertEqual(ui_cosmetics.target_from_source('nc_arc_1/arts/bg.png'), 'namecardskin_nc_arc_1_bg.png')
        self.assertIsNone(ui_cosmetics.target_from_source('charavatars/char_002_amiya.png'))
        self.assertIsNone(ui_cosmetics.target_from_source('hometheme/common.png'))


class Resolution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.roots = [self.root / 'downloaded', self.root / 'installed']
        self.installed = self.roots[1]

    def tearDown(self):
        self.temp.cleanup()

    def file(self, root, relative):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'test')
        return file

    def test_resolve_targets_and_preserve_download_precedence(self):
        downloaded = self.file(self.roots[0], 'arts/ui/namecardskin/[uc]nc_arc_1.ab')
        self.file(self.installed, 'arts/ui/namecardskin/[uc]nc_arc_1.ab')
        theme = self.file(self.installed, 'arts/ui/hometheme/[uc]tm_mainline_1.ab')
        self.file(self.installed, 'arts/ui/hometheme/common.ab')
        resolved = dict(ui_cosmetics.resolve_bundles(self.roots, ['namecardskin_nc_arc_1_bg.png', 'hometheme/tm_mainline_1/btn_battle.png']))
        self.assertEqual(resolved['arts/ui/namecardskin/[uc]nc_arc_1.ab'], downloaded)
        self.assertEqual(resolved['arts/ui/hometheme/[uc]tm_mainline_1.ab'], theme)

    def test_unknown_targets_are_ignored(self):
        self.file(self.installed, 'arts/ui/hometheme/[uc]tm_mainline_1.ab')
        self.assertEqual(ui_cosmetics.resolve_bundles(self.roots, ['charavatars_char_002_amiya.png', '', None, 'hometheme_tm_missing_btn.png']), [])


if __name__ == '__main__':
    unittest.main()
