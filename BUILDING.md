# Build and verification

Initialize the submodules and update their tracked branches. Windows x64,
Visual Studio C++ build tools, Git LFS, Node.js 24+, and Python 3.12 are required.
Run `git -C vendor/UnityPy lfs pull` to materialize its type-definition data.

Run `node tools/build.cjs --test`. Set `QAQR_PYTHON` to a Python executable if it
is not on PATH. `QAQR_PY_PACKAGES` may point to a prepared directory containing
the packages pinned in `python/requirements.txt`.

The loader and runtime DLL are compiled from `native/` plus the MinHook submodule.
UnityPy is imported unchanged from the QAQ-Revival fork, where the eager
AudioClipConverter import is already excluded. No build-time source patch is
applied. AudioClipConverter, fmod_toolkit and pyfmodex are excluded
from the frozen worker, and the output inventory rejects FMOD and legacy M4A
binaries. This build supports image, Spine and voice conversion; AudioClip
decoding remains excluded.

The Python packages are open-source libraries with pinned versions; their license
notices are copied into the distribution. The Windows platform and its normal
compiler/runtime components are external platform dependencies.

Voice lines are converted without decoding AudioClips or shipping the FMOD SDK:
a replacement WAV becomes an uncompressed PCM16 FSB5 bank appended to the
bundle's `.resource` stream (`python/voice.py`). This build therefore supports
image, Spine and voice conversion; AudioClip decoding remains excluded.

`dist/component.json` records the dependency commits and hashes of distribution
files. Source changes require rebuilding; do not edit the built runtime manually.
No private credentials, game data or game art belong in this repository.

Each submodule tracks its organization fork branch declared in `.gitmodules` and `sources.json`.
Use `git submodule update --init --remote --recursive` to select each branch's
latest commit, then rebuild and run verification. The superproject gitlinks and
build manifest record the checkout used for that build; no tag is enforced.

## Runtime boundary

The worker rebuilds separate PC bundles using the current local game bundles as
templates. It preserves the Windows platform and object identities. Only known
texture/Spine object bindings are replaced. Original game files are read only.

PC discovery overlays `PersistentData/Bundles` on `StreamingAssets/AB/Windows`
by complete relative path. It covers `chararts`, `skinpack`, `charpack`,
`arts/dynchars` (including outfit suffixes), operator texture dependencies, and
the avatar/portrait page bundles. Dynamic portraits, entrances, special
illustrations and multiple atlas pages follow their actual prefab, skeleton and
material references. Same-named assets are distinguished by references, never
by their iteration order.

Avatars and half-body portraits are packed per page, not per operator:
`spritepack/ui_char_avatar_N.ab` holds 180×180 avatar textures and
`spritepack/char_portrait_N.ab` holds 180×360 portrait textures, each exposed
by a container such as `dyn/arts/charavatars/elite/char_002_amiya_2.png`. The
hub assets (`arts/charavatars/avatar_hub.ab`, `arts/charportraits/portraits_hub.ab`)
only record editor packing settings, and page numbers do not follow their key
order, so the owning page is resolved from the container tables of the installed
game and only that page bundle is rebuilt. Mod resources are named
`charavatars_<folder>_<asset>.png` and `charportraits_<folder>_<asset>.png`;
files that keep the game folder layout (`charavatars/elite/…`) are named
automatically. Sprites use fixed pixel rectangles, so a replacement with other
dimensions is scaled to the native texture size during preparation and reported
as `appearanceNativeSize`. A bare `<asset>.png` keeps the established
illustration binding; portraits must use the folder layout or the canonical
`charportraits_…` name.

Name card styles (`arts/ui/namecardskin/[uc]nc_<style>.ab`) and home themes
(`arts/ui/hometheme/[uc]tm_<theme>.ab`) are packed the same way, one bundle per
style or theme, so a Mod replaces textures inside that single bundle. Resources
are named `namecardskin_<style>_<role>.png` and `hometheme_<theme>_<role>.png`.
The role comes from the addressable `…/<role>.png` container; sub folders are
joined with `_` (`astc/tv/tv_frame.png` becomes `astc_tv_tv_frame`) and textures
that are only referenced by a material or prefab keep their texture name.
Both are reported as shared, account-wide resources rather than operator
outfits, and replacements are scaled to the native sprite size like avatars.

Voice lines are packed per character and language group:
`audio/sound_beta_2/voice/<character>.ab` (jp) and
`audio/sound_beta_2/voice_cn/<character>.ab` (cn). Every line is an `AudioClip`
whose samples are FSB5 banks inside one shared `.resource` stream. A replacement
WAV is written as an uncompressed PCM16 FSB5 bank appended to that stream, and
the clip is pointed at it, so every other bank stays byte-identical and no
AudioClip decoding or FMOD SDK is required. Resources are named
`voice_<group>_<character>_<clip>.wav`
(`voice_cn_char_003_kalts_CN_002.wav`); loose WAVs take the character and
language group from `mod.ini` (`character`, `voice_group`) or the folder name,
and a Unity path id in the file name is used as a fallback. Replacement voice
bundles grow with the PCM data.

Prebuilt AssetBundle Mods are redirected without repacking. Their `mod.ini`
declares `[mod] type = assetbundle`, a `key_root` (relative to the directory
that contains the game folder, or to the game folder itself) and a
`bundle_root`; `auto_map_by_stem` maps every `.ab` below `bundle_root` to
`key_root` by its relative path, while `[map]` names bundle keys explicitly when
it is false. The loader manifest points at a copy of the Mod bundle, the
PersistentData overlay is preferred when the game ships a downloaded copy, and a
scoped reload of a disabled Mod emits `REMOVE` entries instead. A bundle replaced
this way takes precedence over a rebuilt resource bundle for the same source.

Text Mods are runtime hooks, not resource rebuilds. `ArkRuntime.dll` waits for
IL2CPP, resolves the story/voice/archive/display classes and fields by name, and
installs MinHook targets for `AVGTextManager.Translate`, `UITextAspects.SetText`,
the AVG/Dialog/Subtitle context methods, home and battle-finish voice text, and
the HandBook archive view. A text Mod directory under `Resources` declares
`[mod] type = text`, `text_type = story|voice|archive|direct_match`,
`text_root`/`scan_root`, `auto_map_by_stem` and `priority`; grouped `.txt` files
use `[entry]`, identity fields, and `src <<<`/`text <<<` blocks. Story keys are
`story|command`, voice keys are `char_word_id`, archive keys are
`character|bar=N|entry=N|role=role`, and direct matches use strict/contains
against the displayed string. The configured dump hotkey toggles dump; dump writes
one Mod-ready folder per character/story:
`TextDump/<bucket>/<entity>/mod.ini` plus
`TextDump/<bucket>/<entity>/<bucket>/*.txt` under the Arknights data root. The
manager writes `TextDump/dump.ini` (`hotkey`, `enabled`) for persistence and sends
an immediate `@dump:` control message over the runtime's local pipe; the runtime
applies it without polling the file. Toggling shows an in-game top-center overlay
notification. F10 rebuilds the text index from `Resources`. Text files are not inputs to the AssetBundle rebuild
pipeline.

The ARM deployment keeps large immutable resources as NTFS hard links to the Mod
library, so enabling a Mod does not duplicate its `.ab`/`.png`/`.wav` payload in
`Resources`; small metadata is copied. After a successful swap the old deployment
and old `PC-Bundles` trees are deleted, and the manager exposes a storage cleanup
action for stale stages, superseded bundles, old version backups and retired
random-name executables. Batch imports remove their private extraction cache.

Conversion accepts `selectedIds` from its analysis report. Whole bundles require
an explicit selection; identical/different comparisons describe the current PC
baseline and do not prove which changes were authored in an old mobile bundle.
Copies include `.qaqr-resources.json`, binding each resource to a logical bundle
and target name. Preserve this file when moving a converted Mod. Preparation
combines all enabled resource edits for each original bundle, checks for edits
to the same object, and verifies all unselected objects remain unchanged.

`tests/pc_resources_check.py --game <Arknights.exe> --output <test-directory>`
performs optional offline integration checks against a local installation. Its
output must be outside the game directory. It never executes the game.

`ArkLoader --check --manifest <manifest.tsv>` verifies paths and both original and
replacement SHA-256 values without starting the game. `--launch` is a separate,
explicit operation. It starts the selected executable and loads the supplied
runtime DLL through the normal Windows process APIs. It does not alter protection
services, patch the game executable, or fall back to another injector.

The runtime resolves documented Unity AssetBundle file-loading interfaces through
exported IL2CPP APIs and redirects only exact manifest paths. Packed offsets and
memory-based loading are unsupported. Missing interfaces disable the runtime and
produce a status report. The runtime also exposes a local, PID-checked scoped
reload endpoint. `ArkLoader --reload --manifest <patch.tsv> --pid <pid>` accepts
only partial manifests under the running session's `PC-Bundles/reloads` tree.
Only affected bundles are rebuilt, including other enabled edits sharing those
bundles. Cached bundle wrappers follow their replacement on later asset reads;
pending asynchronous reads defer the switch. Unloading uses `Unload(false)` so
objects already displayed in a scene are preserved. Re-enter the operator view
or scene to request new resources; game-level prefab caches may retain an old
appearance. Memory/stream loads are still unsupported. A game build missing the
reload interfaces retains ordinary file redirection and reports reload as
unsupported. No game compatibility or visual validation is implied by a reload
acknowledgement.

Compilation, manifest tests and round-trip bundle checks do not establish game
compatibility. The current implementation is experimental until exercised in the
target game. `gameRuntimeValidated` remains false in generated component metadata.
