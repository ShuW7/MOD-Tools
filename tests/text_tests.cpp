// Offline checks for the text Mod engine: key rules, old Mod compatibility,
// grouped text round-trips, conflicts, dump files and direct matching.
#include "../native/text.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>

using namespace qaqr::text;

namespace {
void check(bool condition, const char* text) {
    if (!condition) throw std::runtime_error(text);
}

void write(const ark::fs::path& file, const std::string& text) {
    std::error_code error;
    ark::fs::create_directories(file.parent_path(), error);
    std::ofstream stream(file, std::ios::binary | std::ios::trunc);
    stream << text;
    check(stream.good(), "write failed");
}

std::string read(const ark::fs::path& file) {
    std::ifstream stream(file, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(stream), {});
}

void makeMod(const ark::fs::path& dir, const std::string& type, const std::string& body) {
    write(dir / "mod.ini", "[mod]\ntype = text\ntext_type = " + type +
                               "\ntext_root = text\nauto_map_by_stem = true\npriority = 0\n");
    write(dir / "text" / type / "sample.txt", body);
}

Entry storyEntry(const std::string& group, const std::string& cmd, const std::string& source,
                 const std::string& replacement) {
    Entry entry;
    entry.type = Type::Story;
    entry.group = group;
    entry.fields["cmd"] = cmd;
    entry.source = source;
    entry.replacement = replacement;
    return entry;
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        check(argc == 2, "Supply an isolated output directory");
        auto root = ark::fs::absolute(argv[1]);
        std::error_code error;
        ark::fs::remove_all(root, error);
        ark::fs::create_directories(root / "Resources", error);

        check(sanitizeToken("\xEF\xBB\xBF  dialog@1[content]\x01  ") == "dialog@1[content]",
              "sanitize BOM/control");
        check(storyKey("Activities/A001", "dialog@109[content]") ==
                  "activities/a001|dialog@109[content]",
              "story key");
        check(storyKey("Activities/A001", "dialog@158[name]") ==
                  "activities/a001|dialog@158[name]",
              "story name key");
        check(voiceKey("Char_002_Amiya_CN_001") == "char_002_amiya_cn_001", "voice key");
        check(archiveKey("Char_002_Amiya", 0, 2, "Detail") ==
                  "char_002_amiya|bar=0|entry=2|role=detail",
              "archive key");
        check(directKey("  \xE2\x80\x8Bpublic recruit  ") == "public recruit", "direct normalize");

        auto storyMod = root / "Resources" / "story-mod";
        makeMod(storyMod, "story",
                "# bucket = story\n# group = activities/a001/level_a001_01_beg\n\n[entry]\n"
                "cmd = dialog@109[content]\nsrc <<<\nold text\n>>>\ntext <<<\nnew text\n>>>\n\n");
        std::vector<Entry> parsed;
        check(parseGroupedFile(storyMod / "text/story/sample.txt", parsed), "parse story");
        check(parsed.size() == 1 && parsed[0].fields["cmd"] == "dialog@109[content]", "story fields");
        check(parsed[0].source == "old text" && parsed[0].replacement == "new text", "story text blocks");
        auto serialized = serializeGrouped("story", parsed[0].group, parsed);
        check(serialized.find("# bucket = story") != std::string::npos, "serialize header");
        check(serialized.find("src <<<") != std::string::npos && serialized.find("text <<<") != std::string::npos,
              "serialize blocks");

        auto mapMod = root / "Resources" / "map-mod";
        write(mapMod / "mod.ini", "[mod]\ntype = text\ntext_type = story\ntext_root = text\n"
                                 "auto_map_by_stem = false\npriority = 5\n\n[map]\n"
                                 "custom = custom.txt\n");
        write(mapMod / "custom.txt", "# group = mapgroup\n\n[entry]\ncmd = dialog@1[content]\n"
                                     "src <<<\nA\n>>>\ntext <<<\nB\n>>>\n");
        auto directMod = root / "Resources" / "direct-mod";
        write(directMod / "mod.ini", "[mod]\ntype = text\ntext_type = direct_match\ntext_root = text\n"
                                     "auto_map_by_stem = true\npriority = 1\n");
        write(directMod / "text/direct.txt", "[entry]\nmatch_mode = strict\nsrc <<<\nPUBLIC RECRUIT\n>>>\n"
                                             "text <<<\nPUBLIC RECRUIT MODIFIED\n>>>\n\n[entry]\nmatch_mode = contains\n"
                                             "src <<<\nSANITY\n>>>\ntext <<<\nSanity\n>>>\n");
        auto voiceMod = root / "Resources" / "voice-mod";
        makeMod(voiceMod, "voice",
                "# bucket = voice\n# group = char_002_amiya\n\n[entry]\n"
                "char_word_id = char_002_amiya_CN_001\nvoice_id = CN_001\n"
                "src <<<\nDoctor, good work.\n>>>\ntext <<<\nDoctor, take a rest.\n>>>\n");
        auto archiveMod = root / "Resources" / "archive-mod";
        makeMod(archiveMod, "archive",
                "# bucket = archive\n# group = char_002_amiya\n\n[entry]\nbar = 0\nentry = 0\n"
                "role = title\nentry_title = Basic File\nsrc <<<\nBasic File\n>>>\ntext <<<\nBasic File (Mod)\n>>>\n");

        Engine engine;
        engine.initialize(root, root / "Resources");
        check(engine.ready(), "engine ready");

        Query story;
        story.type = Type::Story;
        story.storyId = "activities/a001/level_a001_01_beg";
        story.cmdSrc = "dialog@109[content]";
        Resolved resolved;
        check(engine.resolve(story, resolved) && resolved.entry.replacement == "new text", "story resolve");
        Query mapped;
        mapped.type = Type::Story;
        mapped.storyId = "mapgroup";
        mapped.cmdSrc = "dialog@1[content]";
        check(engine.resolve(mapped, resolved) && resolved.entry.replacement == "B", "map mode resolve");

        Query voice;
        voice.type = Type::Voice;
        voice.charWordId = "char_002_amiya_cn_001";
        voice.voiceGroupKey = "char_002_amiya";
        check(engine.resolve(voice, resolved) && resolved.entry.replacement == "Doctor, take a rest.",
              "voice resolve");
        Query archive;
        archive.type = Type::Archive;
        archive.archiveKey = "char_002_amiya";
        archive.archiveBar = 0;
        archive.archiveEntry = 0;
        archive.archiveRole = "title";
        check(engine.resolve(archive, resolved) && resolved.entry.replacement == "Basic File (Mod)",
              "archive resolve");

        std::string output;
        check(engine.replaceDirect("PUBLIC RECRUIT", output) && output == "PUBLIC RECRUIT MODIFIED",
              "direct strict");
        check(engine.replaceDirect("Current SANITY 100", output) && output == "Current Sanity 100",
              "direct contains");
        check(!engine.replaceDirect("NOPE", output), "direct miss");

        auto priorityMod = root / "Resources" / "priority-mod";
        write(priorityMod / "mod.ini", "[mod]\ntype = text\ntext_type = story\ntext_root = text\n"
                                       "auto_map_by_stem = true\npriority = 20\n");
        write(priorityMod / "text/story/p.txt",
              "# group = activities/a001/level_a001_01_beg\n\n[entry]\ncmd = dialog@109[content]\n"
              "src <<<\nold text\n>>>\ntext <<<\nhigh priority\n>>>\n");
        engine.reload();
        check(engine.resolve(story, resolved) && resolved.entry.replacement == "high priority", "priority wins");

        engine.setDumpEnabled(true);
        engine.dumpStory("activities/a001/level_a001_01_beg",
                         {storyEntry("activities/a001/level_a001_01_beg", "dialog@109[content]",
                                     "old text", "old text")});
        auto storyEntity = root / "TextDump" / "story" / "activities_a001_level_a001_01_beg";
        auto storyDump = storyEntity / "story" / "activities_a001_level_a001_01_beg.txt";
        check(ark::fs::is_regular_file(storyDump), "story dump file");
        check(ark::fs::is_regular_file(storyEntity / "mod.ini"), "dump mod.ini");
        check(read(storyEntity / "mod.ini").find("text_root = story") != std::string::npos, "dump text_root");
        std::vector<Entry> dumped;
        check(parseGroupedFile(storyDump, dumped) && dumped.size() == 1, "dump parse");
        check(dumped[0].source == "old text" && dumped[0].replacement == "old text", "dump src/text equal");
        auto dumpText = read(storyDump);
        check(dumpText.find("src <<<\nold text\n>>>") != std::string::npos, "dump format");
        check(dumpText.find("# group = activities/a001/level_a001_01_beg") != std::string::npos,
              "dump group");

        engine.dumpQuery(voice);
        check(ark::fs::is_regular_file(root / "TextDump" / "voice" / "char_002_amiya" / "voice" /
                                       "char_002_amiya.txt"),
              "voice dump");
        engine.dumpQuery(archive);
        check(ark::fs::is_regular_file(root / "TextDump" / "archive" / "char_002_amiya" / "archive" /
                                       "char_002_amiya.txt"),
              "archive dump");

        Engine dumpEngine;
        dumpEngine.initialize(root, root / "TextDump" / "story");
        Query dumpQuery;
        dumpQuery.type = Type::Story;
        dumpQuery.storyId = "activities/a001/level_a001_01_beg";
        dumpQuery.cmdSrc = "dialog@109[content]";
        check(dumpEngine.resolve(dumpQuery, resolved) && resolved.entry.replacement == "old text",
              "dump as Mod");

        std::cout << "Text engine tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Text engine test failed: " << error.what() << "\n";
        return 1;
    }
}
