import os, struct, sys, tempfile, unittest, wave
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get('QAQR_ARKNIGHTS_TOOLS', Path(__file__).resolve().parents[1])) / 'python'))
import voice


class Naming(unittest.TestCase):
    def test_bundle_paths(self):
        self.assertEqual(voice.bundle_relative('cn', 'char_003_kalts'), 'audio/sound_beta_2/voice_cn/char_003_kalts.ab')
        self.assertEqual(voice.bundle_relative('jp', 'char_003_kalts'), 'audio/sound_beta_2/voice/char_003_kalts.ab')
        self.assertEqual(voice.family_of_bundle(r'D:\game\audio\sound_beta_2\voice_cn\char_003_kalts.ab'), 'cn')
        self.assertEqual(voice.family_of_bundle('audio/sound_beta_2/voice/char_002_amiya.ab'), 'jp')
        self.assertIsNone(voice.family_of_bundle('audio/sound_beta_2/music/x.ab'))

    def test_target_names(self):
        self.assertEqual(voice.target_name('cn', 'char_003_kalts', 'CN_002'), 'voice_cn_char_003_kalts_CN_002.wav')
        known = {'cn': ['char_003_kalts', 'char_003_kalts_boc#6'], 'jp': []}
        self.assertEqual(voice.split_target('voice_cn_char_003_kalts_boc#6_CN_001.wav', known), ('cn', 'char_003_kalts_boc#6', 'CN_001'))
        self.assertEqual(voice.split_target('voice_cn_char_003_kalts_CN_002.wav', known), ('cn', 'char_003_kalts', 'CN_002'))
        self.assertIsNone(voice.split_target('CN_002.wav', known))

    def test_clip_path_id(self):
        self.assertEqual(voice.clip_path_id('CN_002_-4488200830803703160.wav'), -4488200830803703160)
        self.assertIsNone(voice.clip_path_id('CN_002.wav'))


class Hints(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_mod_ini_hints(self):
        (self.root / 'mod.ini').write_text('[mod]\ntype = audio\nvoice_group = Voice_CN\ncharacter = char_003_kalts\n', encoding='utf-8')
        hints = voice.read_hints(self.root)
        self.assertEqual(hints, {'character': 'char_003_kalts', 'group': 'cn'})

    def test_target_from_source(self):
        (self.root / 'mod.ini').write_text('character = char_003_kalts\nvoice_group = Voice_CN\n', encoding='utf-8')
        hints = voice.read_hints(self.root)
        self.assertEqual(voice.target_from_source('wav/CN_002_-4488200830803703160.wav', hints), 'voice_cn_char_003_kalts_CN_002.wav')
        self.assertEqual(voice.target_from_source('wav/CN_002.wav', hints), 'voice_cn_char_003_kalts_CN_002.wav')
        # Without mod.ini the character can still come from the folder.
        self.assertEqual(voice.target_from_source('char_003_kalts_boc#6/wav/CN_003_12345678.wav', {}), 'voice_cn_char_003_kalts_boc#6_CN_003.wav')
        self.assertIsNone(voice.target_from_source('wav/CN_002.wav', {}))


class Audio(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def wav(self, name='voice.wav', rate=32000, channels=1, frames=100):
        path = self.root / name
        with wave.open(str(path), 'wb') as target:
            target.setnchannels(channels)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(b'\x00\x01' * frames * channels)
        return path

    def test_wav_pcm16(self):
        pcm, rate, channels = voice.wav_pcm16(self.wav(channels=2, frames=50))
        self.assertEqual((rate, channels, len(pcm)), (32000, 2, 200))

    def test_fsb5_layout(self):
        pcm, rate, channels = voice.wav_pcm16(self.wav(frames=320))
        blob = voice.build_fsb5(pcm, rate, channels)
        self.assertEqual(blob[:4], b'FSB5')
        version, count, headers, names, data_size, mode = struct.unpack('<IIIIII', blob[4:28])
        self.assertEqual((version, count, headers, names, data_size, mode), (1, 1, 8, 0, len(pcm), 2))
        packed = struct.unpack('<Q', blob[60:68])[0]
        self.assertEqual((packed >> 1) & 0xF, 7)  # 32000 Hz table index
        self.assertEqual((packed >> 5) & 1, 0)     # one channel
        self.assertEqual(packed >> 34, 320)        # frame count
        self.assertEqual(blob[68:], pcm)

    def test_unsupported_rate_and_width(self):
        pcm, rate, channels = voice.wav_pcm16(self.wav(frames=10))
        with self.assertRaises(ValueError):
            voice.build_fsb5(pcm, 37000, channels)
        with wave.open(str(self.root / 'bad.wav'), 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(4)
            target.setframerate(32000)
            target.writeframes(b'\x00' * 40)
        with self.assertRaises(ValueError):
            voice.wav_pcm16(self.root / 'bad.wav')


class Resolution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.installed = self.root / 'installed'
        self.roots = [self.root / 'downloaded', self.installed]

    def tearDown(self):
        self.temp.cleanup()

    def file(self, root, relative):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'test')
        return file

    def test_resolve_target_and_character(self):
        cn = self.file(self.installed, 'audio/sound_beta_2/voice_cn/char_003_kalts.ab')
        jp = self.file(self.installed, 'audio/sound_beta_2/voice/char_003_kalts.ab')
        self.file(self.installed, 'audio/sound_beta_2/voice_cn/char_002_amiya.ab')
        resolved = dict(voice.resolve_bundles(self.roots, ['char_003_kalts'], ['voice_cn_char_003_kalts_CN_002.wav']))
        self.assertEqual(resolved['audio/sound_beta_2/voice_cn/char_003_kalts.ab'], cn)
        self.assertNotIn('audio/sound_beta_2/voice/char_003_kalts.ab', resolved)
        # A loose WAV without a language hint offers both groups.
        resolved = dict(voice.resolve_bundles(self.roots, ['char_003_kalts'], ['wav/CN_002.wav']))
        self.assertEqual(resolved['audio/sound_beta_2/voice_cn/char_003_kalts.ab'], cn)
        self.assertEqual(resolved['audio/sound_beta_2/voice/char_003_kalts.ab'], jp)

    def test_ignores_unrelated_requests(self):
        self.file(self.installed, 'audio/sound_beta_2/voice_cn/char_003_kalts.ab')
        self.assertEqual(voice.resolve_bundles(self.roots, [], ['charavatars_char_002_amiya.png']), [])


if __name__ == '__main__':
    unittest.main()
