import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get('QAQR_ARKNIGHTS_TOOLS', Path(__file__).resolve().parents[1])) / 'python'))
import ab_mod


class Config(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.games = self.root / 'games'
        self.game = self.games / 'Arknights'
        self.exe = self.game / 'Arknights.exe'
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b'MZ')
        self.mod = self.root / 'mod'
        (self.mod / 'shopkeeper').mkdir(parents=True)
        self.bundle = self.mod / 'shopkeeper' / 'live_007_closre.ab'
        self.bundle.write_bytes(b'UnityFS' + b'\0' * 32)

    def tearDown(self):
        self.temp.cleanup()

    def ini(self, text):
        (self.mod / 'mod.ini').write_text(text, encoding='utf-8')
        return ab_mod.read_config(self.mod)

    def game_bundle(self, relative):
        file = self.game / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'UnityFS' + b'\1' * 16)
        return file

    def test_read_config(self):
        config = self.ini('[mod]\ntype = assetbundle\nkey_root = Arknights\\Arknights_Data\\StreamingAssets\\AB\\Windows\\prefabs\\shop\\shopkeeper\n\nbundle_root = shopkeeper\nauto_map_by_stem = true\n')
        self.assertEqual(config['key_root'], 'Arknights/Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper')
        self.assertEqual(config['bundle_root'], 'shopkeeper')
        self.assertTrue(config['auto'])
        self.assertIsNone(ab_mod.read_config(self.root))

    def test_key_root_and_entries(self):
        config = self.ini('[mod]\ntype = assetbundle\nkey_root = Arknights\\Arknights_Data\\StreamingAssets\\AB\\Windows\\prefabs\\shop\\shopkeeper\nbundle_root = shopkeeper\n')
        relative = ab_mod.key_root_relative(self.exe, config['key_root'])
        self.assertEqual(relative, 'Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper')
        entries = ab_mod.plan(config, self.exe)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['game_relative'], 'Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper/live_007_closre.ab')
        self.assertTrue(entries[0]['valid'])
        self.assertIsNone(entries[0]['source'])

    def test_downloaded_overlay_wins(self):
        self.game_bundle('Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper/live_007_closre.ab')
        overlay = self.game_bundle('Arknights_Data/PersistentData/Bundles/prefabs/shop/shopkeeper/live_007_closre.ab')
        config = self.ini('[mod]\ntype = assetbundle\nkey_root = Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper\nbundle_root = shopkeeper\n')
        entries = ab_mod.plan(config, self.exe)
        self.assertEqual(entries[0]['source'], 'Arknights_Data/PersistentData/Bundles/prefabs/shop/shopkeeper/live_007_closre.ab')
        self.assertEqual(Path(self.game / entries[0]['source']), overlay)

    def test_explicit_map(self):
        (self.mod / 'custom.bin').write_bytes(b'UnityFS' + b'\0' * 8)
        config = self.ini('[mod]\ntype = assetbundle\nkey_root = Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop\nauto_map_by_stem = false\n\n[map]\nshopkeeper/live_007_closre = custom.bin\n')
        entries = ab_mod.plan(config, self.exe)
        self.assertEqual(entries[0]['game_relative'], 'Arknights_Data/StreamingAssets/AB/Windows/prefabs/shop/shopkeeper/live_007_closre.ab')
        self.assertEqual(entries[0]['key'], 'shopkeeper/live_007_closre.ab')

    def test_unsafe_paths(self):
        with self.assertRaises(ValueError):
            self.ini('[mod]\ntype = assetbundle\nkey_root = ..\\Windows\nbundle_root = shopkeeper\n')
        with self.assertRaises(ValueError):
            ab_mod.safe_relative('../escape')

    def test_invalid_bundle_file(self):
        (self.mod / 'shopkeeper' / 'live_007_closre.ab').write_bytes(b'not a bundle')
        config = ab_mod.read_config(self.mod) if (self.mod / 'mod.ini').exists() else self.ini('[mod]\ntype = assetbundle\nkey_root = Arknights_Data/StreamingAssets/AB/Windows\nbundle_root = shopkeeper\n')
        entries = ab_mod.plan(config, self.exe)
        self.assertFalse(entries[0]['valid'])


if __name__ == '__main__':
    unittest.main()
