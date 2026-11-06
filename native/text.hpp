#pragma once
// Offline-compatible text Mod engine. No Windows or IL2CPP dependencies here:
// the runtime hook layer feeds queries and managed strings into this engine.
#include "manifest.hpp"

#include <array>
#include <atomic>
#include <map>
#include <mutex>
#include <set>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace qaqr::text {

enum class Type { Story, Voice, Archive, Direct };
enum class Bucket { Story, Voice, Archive, None };

struct Entry {
    Type type = Type::Story;
    std::string group;
    // Identity fields as written to the grouped text file, e.g. cmd, bar,
    // entry, role, char_word_id. Values are UTF-8.
    std::map<std::string, std::string, std::less<>> fields;
    std::string source;
    std::string replacement;
};

struct ModConfig {
    Type type = Type::Story;
    std::string textRoot = "text";
    std::string scanRoot;
    bool autoMap = true;
    int priority = 0;
    std::map<std::string, std::string, std::less<>> map;
    ark::fs::path dir;
    ark::fs::path ini;
    std::string name;
};

struct Candidate {
    Entry entry;
    std::string file;
    std::string modDir;
    int priority = 0;
};

struct Query {
    Type type = Type::Story;
    std::string sourceText;
    std::string translatedText;
    std::string storyId;
    std::string cmdSrc;
    std::string charWordId;
    std::string voiceGroupKey;
    std::string voiceCharId;
    std::string voiceId;
    std::string archiveKey;
    std::string archiveRole;
    std::string archiveEntryTitle;
    int archiveBar = -1;
    int archiveEntry = -1;
};

struct Resolved {
    bool found = false;
    Entry entry;
    std::string file;
    std::string modDir;
};

// --- text normalization / key rules (shared with old m4a Mods) -------------
std::string sanitizeToken(std::string value);
std::string lowerAscii(std::string value);
std::string normalizeStoryCommand(std::string value);
std::string normalizeTextMatch(std::string value);
std::string storyKey(const std::string& group, const std::string& command);
std::string voiceKey(const std::string& charWordId);
std::string archiveKey(const std::string& charKey, int bar, int entry, const std::string& role);
std::string directKey(const std::string& text);

// --- Mod files -------------------------------------------------------------
bool readIni(const ark::fs::path& file,
             std::map<std::string, std::map<std::string, std::string, std::less<>>, std::less<>>& sections);
bool readModConfig(const ark::fs::path& dir, ModConfig& config);
bool parseGroupedFile(const ark::fs::path& file, std::vector<Entry>& entries);
std::string serializeGrouped(const std::string& bucket, const std::string& group,
                             const std::vector<Entry>& entries);
std::string bucketName(Bucket bucket);
std::string typeName(Type type);

class Engine {
public:
    // dataRoot is the Arknights data root (the parent of Resources / PC-Bundles).
    // modRoot defaults to dataRoot/Resources; dumpRoot is dataRoot/TextDump.
    void initialize(const ark::fs::path& dataRoot, const ark::fs::path& modRoot);
    void reload();
    bool ready() const;

    bool resolve(const Query& query, Resolved& result) const;
    // Returns true when the displayed text changed. current/out are UTF-8.
    bool replaceDirect(const std::string& current, std::string& out, std::string* sourceFile = nullptr) const;

    void setDumpEnabled(bool enabled);
    bool dumpEnabled() const;
    void dumpQuery(const Query& query);
    void dumpStory(const std::string& storyId, const std::vector<Entry>& entries);

    ark::fs::path dataRoot() const;
    ark::fs::path modRoot() const;
    ark::fs::path dumpRoot() const;
    std::string stats() const;

private:
    struct Direct {
        std::string match;
        std::string replacement;
        bool contains = false;
        int priority = 0;
        std::string file;
        std::string modDir;
    };
    struct Index {
        std::unordered_map<std::string, Candidate> story;
        std::unordered_map<std::string, Candidate> voice;
        std::unordered_map<std::string, Candidate> archive;
        std::unordered_map<std::string, Direct> directStrict;
        std::unordered_map<std::string, Direct> directContainsByMatch;
        std::vector<Direct> directContains;
    };
    struct DumpBucketState {
        bool loaded = false;
        std::map<std::string, std::vector<Entry>> groups;
        std::map<std::string, std::set<std::string>> fingerprints;
    };

    void buildIndex();
    void addCandidate(std::unordered_map<std::string, Candidate>& index, const std::string& key,
                      const Candidate& candidate);
    void addDirect(Index& index, const std::string& key, Direct direct, bool contains);
    void loadDumpGroup(Bucket bucket, const std::string& group);
    void writeDumpGroup(Bucket bucket, const std::string& group);
    ark::fs::path dumpEntityRoot(Bucket bucket, const std::string& group) const;
    ark::fs::path dumpDirectory(Bucket bucket, const std::string& group) const;
    ark::fs::path dumpFile(Bucket bucket, const std::string& group) const;

    mutable std::shared_mutex indexMutex_;
    Index index_;
    std::atomic_bool ready_{false};
    std::atomic_bool dumpEnabled_{false};
    ark::fs::path dataRoot_;
    ark::fs::path modRoot_;
    mutable std::mutex dumpMutex_;
    std::array<DumpBucketState, 3> dump_;
};

Engine& engine();

} // namespace qaqr::text
