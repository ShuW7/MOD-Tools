"""Text Mods are runtime-only: preparing bundles must not reject their .txt files."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import bundle_builder


class TextModBuild(unittest.TestCase):
    def test_text_files_are_not_bundle_inputs(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('QAQR_TEST_TMP')) as directory:
            root = Path(directory)
            game = root / 'game' / 'Arknights.exe'
            game.parent.mkdir()
            game.write_bytes(b'never executed')
            source = root / 'destination'
            mod = source / 'modid'
            (mod / 'text' / 'story').mkdir(parents=True)
            (mod / 'mod.ini').write_text('[mod]\ntype = text\ntext_type = story\ntext_root = text\nauto_map_by_stem = true\n')
            (mod / 'text' / 'story' / 'a.txt').write_text('# group = g\n\n[entry]\ncmd = dialog@1[content]\nsrc <<<\nA\n>>>\ntext <<<\nB\n>>>\n')
            output = root / 'output'
            with patch('bundle_builder.game_roots', lambda _: [game.parent]), \
                 patch('bundle_builder.current_bundles', lambda *_: []):
                result = bundle_builder.build_bundles({
                    'source': str(source),
                    'gamePath': str(game),
                    'output': str(output),
                })
            self.assertTrue(result['success'])
            self.assertEqual(result['bundleCount'], 0)
            self.assertEqual(result['resourceCount'], 0)
            manifest = (output / 'manifest.tsv').read_text(encoding='utf-8')
            self.assertNotIn('BUNDLE\t', manifest)
            self.assertFalse(any(output.rglob('*.txt')))


if __name__ == '__main__':
    unittest.main()
