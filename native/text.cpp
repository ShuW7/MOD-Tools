#include "text.hpp"

#include <algorithm>
#include <cstdint>
#include <cctype>
#include <charconv>
#include <fstream>
#include <set>
#include <sstream>

namespace qaqr::text {
namespace {

constexpr const char* kDumpHeader = "# Arknight Mod grouped text dump";

bool readable(const std::string& value) {
    for (unsigned char c : value) if (c < 0x20 && c != '\t') return false;
    return true;
}

std::string trim(std::string value) {
    size_t begin = 0, end = value.size();
    while (begin < end && std::isspace(static_cast<unsigned char>(value[begin]))) ++begin;
    while (end > begin && std::isspace(static_cast<unsigned char>(value[end - 1]))) --end;
    return value.substr(begin, end - begin);
}

std::string stripBom(std::string value) {
    if (value.size() >= 3 && static_cast<unsigned char>(value[0]) == 0xEF &&
        static_cast<unsigned char>(value[1]) == 0xBB && static_cast<unsigned char>(value[2]) == 0xBF)
        value.erase(0, 3);
    return value;
}

std::vector<std::string> splitLines(const std::string& text) {
    std::vector<std::string> lines;
    size_t start = 0;
    for (size_t i = 0; i <= text.size(); ++i) {
        if (i == text.size() || text[i] == '\n') {
            std::string line = text.substr(start, i - start);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            lines.push_back(std::move(line));
            start = i + 1;
        }
    }
    return lines;
}

bool readFile(const ark::fs::path& file, std::string& out) {
    std::ifstream stream(file, std::ios::binary);
    if (!stream) return false;
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    out = buffer.str();
    return true;
}

bool writeFile(const ark::fs::path& file, const std::string& data) {
    std::error_code error;
    ark::fs::create_directories(file.parent_path(), error);
    std::ofstream stream(file, std::ios::binary | std::ios::trunc);
    if (!stream) return false;
    stream.write(data.data(), static_cast<std::streamsize>(data.size()));
    return stream.good();
}

std::string join(const std::vector<std::string>& values, const char* separator) {
    std::string out;
    for (size_t i = 0; i < values.size(); ++i) {
        if (i) out += separator;
        out += values[i];
    }
    return out;
}

bool startsWith(const std::string& value, std::string_view prefix) {
    return value.size() >= prefix.size() && value.compare(0, prefix.size(), prefix) == 0;
}

bool endsWith(const std::string& value, std::string_view suffix) {
    return value.size() >= suffix.size() &&
           value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

std::string fieldValue(const Entry& entry, const char* name) {
    auto found = entry.fields.find(name);
    return found == entry.fields.end() ? std::string{} : found->second;
}

int parseInt(const std::string& value, int fallback = -1) {
    int result = fallback;
    auto begin = value.data();
    auto end = value.data() + value.size();
    auto parsed = std::from_chars(begin, end, result);
    return parsed.ec == std::errc{} && parsed.ptr == end ? result : fallback;
}

std::string fingerprint(const Entry& entry) {
    std::string out = std::to_string(static_cast<int>(entry.type));
    out += '\x1f';
    out += entry.group;
    out += '\x1f';
    for (const auto& [key, value] : entry.fields) {
        out += key;
        out += '=';
        out += value;
        out += '\x1e';
    }
    out += '\x1f';
    out += entry.source;
    out += '\x1f';
    out += entry.replacement;
    return out;
}

std::string sanitizeFileComponent(const std::string& value) {
    std::string out;
    for (unsigned char c : value) {
        bool bad = c < 0x20 || c == '<' || c == '>' || c == ':' || c == '"' || c == '/' ||
                   c == '\\' || c == '|' || c == '?' || c == '*';
        out += bad ? '_' : static_cast<char>(c);
    }
    out = trim(out);
    while (!out.empty() && (out.back() == '.' || out.back() == ' ')) out.pop_back();
    if (out.empty()) out = "text";
    if (out.size() > 150) out.resize(150);
    return out;
}

bool isReservedDir(const std::string& name) {
    auto lower = lowerAscii(name);
    return lower == "dump" || lower == "logs" || startsWith(lower, ".qaqr");
}

std::string typeToBucket(Type type) {
    switch (type) {
        case Type::Story: return "story";
        case Type::Voice: return "voice";
        case Type::Archive: return "archive";
        default: return "direct";
    }
}

Bucket bucketOf(Type type) {
    switch (type) {
        case Type::Story: return Bucket::Story;
        case Type::Voice: return Bucket::Voice;
        case Type::Archive: return Bucket::Archive;
        default: return Bucket::None;
    }
}

bool knownField(const std::string& key) {
    return key == "cmd" || key == "char_word_id" || key == "voice_id" || key == "word_key" ||
           key == "char_id" || key == "bar" || key == "entry" || key == "role" ||
           key == "entry_title";
}

std::vector<std::string> knownOrder(Type type) {
    switch (type) {
        case Type::Story: return {"cmd"};
        case Type::Voice: return {"char_word_id", "voice_id", "word_key", "char_id"};
        case Type::Archive: return {"bar", "entry", "role", "entry_title"};
        default: return {"match_mode", "mode"};
    }
}

std::string joinPath(const ark::fs::path& base, const std::string& relative) {
    auto path = base / ark::fs::path(relative);
    return path.lexically_normal().string();
}

} // namespace

std::string lowerAscii(std::string value) {
    for (char& c : value) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return value;
}

std::string sanitizeToken(std::string value) {
    value = stripBom(std::move(value));
    auto nul = value.find('\0');
    if (nul != std::string::npos) value.resize(nul);
    value = trim(std::move(value));
    std::string out;
    out.reserve(value.size());
    for (unsigned char c : value) if (c >= 0x20) out += static_cast<char>(c);
    return trim(std::move(out));
}

std::string normalizeStoryCommand(std::string value) {
    value = sanitizeToken(std::move(value));
    if (value.empty()) return {};
    std::string base = value;
    auto bracket = base.find('[');
    if (bracket != std::string::npos) base.resize(bracket);
    base = trim(std::move(base));
    std::string suffix;
    if (value.find("[name]") != std::string::npos) suffix = "[name]";
    else if (value.find("[content]") != std::string::npos) suffix = "[content]";
    // Old dumps use [name]/[content]. For unusual [name=...] tags keep the
    // normalized whole command instead of inventing a suffix.
    auto result = suffix.empty() ? base : base + suffix;
    return lowerAscii(sanitizeToken(std::move(result)));
}

// Strip invisible UTF-8 codepoints without decoding the whole string. This
// mirrors m4a's direct-match normalization closely enough for dump formats.
std::string normalizeTextMatch(std::string value) {
    std::string out;
    out.reserve(value.size());
    for (size_t i = 0; i < value.size();) {
        unsigned char c = static_cast<unsigned char>(value[i]);
        if (c < 0x80) {
            out += static_cast<char>(c);
            ++i;
            continue;
        }
        unsigned length = 0;
        uint32_t cp = 0;
        if ((c & 0xE0) == 0xC0) { length = 2; cp = c & 0x1F; }
        else if ((c & 0xF0) == 0xE0) { length = 3; cp = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { length = 4; cp = c & 0x07; }
        if (!length || i + length > value.size()) {
            out += static_cast<char>(c);
            ++i;
            continue;
        }
        bool valid = true;
        for (unsigned k = 1; k < length; ++k) {
            unsigned char next = static_cast<unsigned char>(value[i + k]);
            if ((next & 0xC0) != 0x80) { valid = false; break; }
            cp = (cp << 6) | (next & 0x3F);
        }
        if (!valid) {
            out += static_cast<char>(c);
            ++i;
            continue;
        }
        bool invisible = (cp >= 0x00AD && cp <= 0x00AD) || (cp >= 0x200B && cp <= 0x200F) ||
                         (cp >= 0x202A && cp <= 0x202E) || (cp >= 0x2060 && cp <= 0x2064) ||
                         cp == 0xFEFF || (cp >= 0xFE00 && cp <= 0xFE0F);
        if (!invisible) out.append(value, i, length);
        i += length;
    }
    return trim(std::move(out));
}

std::string storyKey(const std::string& group, const std::string& command) {
    return lowerAscii(sanitizeToken(group)) + "|" + normalizeStoryCommand(command);
}

std::string voiceKey(const std::string& charWordId) {
    return lowerAscii(sanitizeToken(charWordId));
}

std::string archiveKey(const std::string& charKey, int bar, int entry, const std::string& role) {
    return lowerAscii(sanitizeToken(charKey)) + "|bar=" + std::to_string(bar) + "|entry=" +
           std::to_string(entry) + "|role=" + lowerAscii(sanitizeToken(role));
}

std::string directKey(const std::string& text) {
    return normalizeTextMatch(text);
}

std::string bucketName(Bucket bucket) {
    switch (bucket) {
        case Bucket::Story: return "story";
        case Bucket::Voice: return "voice";
        case Bucket::Archive: return "archive";
        default: return "none";
    }
}

std::string typeName(Type type) {
    switch (type) {
        case Type::Story: return "story";
        case Type::Voice: return "voice";
        case Type::Archive: return "archive";
        default: return "direct_match";
    }
}

bool readIni(const ark::fs::path& file,
             std::map<std::string, std::map<std::string, std::string, std::less<>>, std::less<>>& sections) {
    std::string text;
    if (!readFile(file, text)) return false;
    text = stripBom(std::move(text));
    std::string section;
    for (auto& rawLine : splitLines(text)) {
        std::string line;
        bool quoted = false;
        char quote = 0;
        for (char c : rawLine) {
            if (quoted) {
                if (c == quote) quoted = false;
                line += c;
                continue;
            }
            if (c == '"' || c == '\'') {
                quoted = true;
                quote = c;
                line += c;
                continue;
            }
            if (c == '#' || c == ';') break;
            line += c;
        }
        line = trim(std::move(line));
        if (line.empty()) continue;
        if (line.front() == '[' && line.back() == ']') {
            section = lowerAscii(trim(line.substr(1, line.size() - 2)));
            sections[section];
            continue;
        }
        auto equals = line.find('=');
        if (equals == std::string::npos) continue;
        auto key = lowerAscii(trim(line.substr(0, equals)));
        auto value = trim(line.substr(equals + 1));
        if (value.size() >= 2 && (value.front() == '"' || value.front() == '\'') &&
            value.back() == value.front())
            value = value.substr(1, value.size() - 2);
        if (!key.empty()) sections[section][key] = value;
    }
    return true;
}

bool readModConfig(const ark::fs::path& dir, ModConfig& config) {
    config = {};
    config.dir = dir;
    config.ini = dir / "mod.ini";
    config.name = dir.filename().string();
    std::map<std::string, std::map<std::string, std::string, std::less<>>, std::less<>> sections;
    if (!readIni(config.ini, sections)) return false;
    auto mod = sections.find("mod");
    if (mod == sections.end()) return false;
    auto type = mod->second.find("type");
    if (type == mod->second.end() || lowerAscii(type->second) != "text") return false;
    auto typeValue = mod->second.find("text_type");
    if (typeValue == mod->second.end()) return false;
    auto typeNameValue = lowerAscii(typeValue->second);
    if (typeNameValue == "story") config.type = Type::Story;
    else if (typeNameValue == "voice") config.type = Type::Voice;
    else if (typeNameValue == "archive") config.type = Type::Archive;
    else if (typeNameValue == "direct_match" || typeNameValue == "direct") config.type = Type::Direct;
    else return false;
    auto root = mod->second.find("text_root");
    config.textRoot = root == mod->second.end() ? std::string{} : root->second;
    auto scan = mod->second.find("scan_root");
    config.scanRoot = scan == mod->second.end() ? std::string{} : scan->second;
    if (config.textRoot.empty()) config.textRoot = config.scanRoot;
    if (config.textRoot.empty()) config.textRoot = "text";
    auto autoMap = mod->second.find("auto_map_by_stem");
    if (autoMap != mod->second.end()) {
        auto value = lowerAscii(autoMap->second);
        config.autoMap = !(value == "0" || value == "false" || value == "no" || value == "off");
    }
    auto priority = mod->second.find("priority");
    if (priority != mod->second.end()) config.priority = parseInt(priority->second, 0);
    auto map = sections.find("map");
    if (map != sections.end()) config.map = map->second;
    return true;
}

bool parseGroupedFile(const ark::fs::path& file, std::vector<Entry>& entries) {
    std::string text;
    if (!readFile(file, text)) return false;
    text = stripBom(std::move(text));
    std::string group;
    Entry current;
    bool inEntry = false;
    bool inBlock = false;
    std::string blockKey;
    std::string block;
    auto flush = [&]() {
        if (!inEntry) return;
        if (blockKey == "src") current.source = block;
        else if (blockKey == "text") current.replacement = block;
        current.group = group;
        entries.push_back(current);
        current = {};
        inEntry = false;
        inBlock = false;
    };
    for (auto& rawLine : splitLines(text)) {
        std::string line = rawLine;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (inBlock) {
            auto check = trim(line);
            if (check == ">>>") {
                if (blockKey == "src") current.source = block;
                else if (blockKey == "text") current.replacement = block;
                inBlock = false;
                block.clear();
                blockKey.clear();
            } else {
                if (!block.empty()) block += '\n';
                block += line;
            }
            continue;
        }
        auto trimmed = trim(line);
        if (trimmed.empty()) continue;
        if (!inEntry) {
            if (startsWith(trimmed, "# bucket")) {
                auto equals = trimmed.find('=');
                if (equals != std::string::npos) trimmed = trim(trimmed.substr(equals + 1));
                continue;
            }
            if (startsWith(trimmed, "# group")) {
                auto equals = trimmed.find('=');
                if (equals != std::string::npos) group = trim(trimmed.substr(equals + 1));
                continue;
            }
            if (trimmed == "[entry]") {
                inEntry = true;
                current = {};
                continue;
            }
            continue;
        }
        if (trimmed == "[entry]") {
            flush();
            inEntry = true;
            current = {};
            continue;
        }
        // Block start: "src <<<" (possibly tabs); block terminator is ">>>".
        auto marker = trimmed.find("<<<");
        if (marker != std::string::npos) {
            blockKey = lowerAscii(trim(trimmed.substr(0, marker)));
            if (blockKey != "src" && blockKey != "text") {
                blockKey.clear();
                continue;
            }
            block.clear();
            inBlock = true;
            continue;
        }
        auto equals = line.find('=');
        if (equals == std::string::npos) continue;
        auto key = lowerAscii(trim(line.substr(0, equals)));
        auto value = trim(line.substr(equals + 1));
        if (key.empty()) continue;
        current.fields[key] = value;
    }
    if (inEntry) flush();
    return true;
}

std::string serializeGrouped(const std::string& bucket, const std::string& group,
                             const std::vector<Entry>& entries) {
    std::string out;
    out += kDumpHeader;
    out += "\n# bucket = ";
    out += bucket;
    out += "\n# group = ";
    out += group;
    out += "\n\n";
    for (const auto& entry : entries) {
        out += "[entry]\n";
        std::set<std::string> written;
        for (const auto& key : knownOrder(entry.type)) {
            auto found = entry.fields.find(key);
            if (found == entry.fields.end()) continue;
            out += key;
            out += " = ";
            out += found->second;
            out += '\n';
            written.insert(key);
        }
        for (const auto& [key, value] : entry.fields) {
            if (written.contains(key) || knownField(key)) continue;
            out += key;
            out += " = ";
            out += value;
            out += '\n';
        }
        auto block = [&](const char* name, const std::string& value) {
            out += name;
            out += " <<<\n";
            out += value;
            if (value.empty() || value.back() != '\n') out += '\n';
            out += ">>>\n";
        };
        block("src", entry.source);
        block("text", entry.replacement);
        out += '\n';
    }
    return out;
}

void Engine::initialize(const ark::fs::path& dataRoot, const ark::fs::path& modRoot) {
    dataRoot_ = dataRoot;
    modRoot_ = modRoot;
    buildIndex();
    ready_.store(true);
}

void Engine::reload() {
    buildIndex();
}

bool Engine::ready() const {
    return ready_.load();
}

ark::fs::path Engine::dataRoot() const { return dataRoot_; }
ark::fs::path Engine::modRoot() const { return modRoot_; }
ark::fs::path Engine::dumpRoot() const { return dataRoot_ / "TextDump"; }

void Engine::buildIndex() {
    Index next;
    std::error_code error;
    std::vector<ModConfig> mods;
    if (ark::fs::is_directory(modRoot_, error)) {
        for (ark::fs::directory_iterator it(modRoot_, ark::fs::directory_options::skip_permission_denied, error), end;
             !error && it != end; it.increment(error)) {
            const auto& path = it->path();
            std::error_code entryError;
            if (!it->is_directory(entryError) || entryError) continue;
            if (it->is_symlink(entryError) || entryError) continue;
            auto name = path.filename().string();
            auto lower = lowerAscii(name);
            if (startsWith(lower, "disabled_") || isReservedDir(name)) continue;
            ModConfig config;
            if (readModConfig(path, config)) mods.push_back(std::move(config));
        }
    }
    // Deterministic manager order: higher priority first, then name.
    std::sort(mods.begin(), mods.end(), [](const ModConfig& a, const ModConfig& b) {
        if (a.priority != b.priority) return a.priority > b.priority;
        return a.name < b.name;
    });
    for (const auto& mod : mods) {
        auto root = mod.dir / mod.textRoot;
        if (mod.textRoot.empty() || mod.textRoot == ".") root = mod.dir;
        std::error_code textRootError;
        if (!ark::fs::is_directory(root, textRootError) && !mod.scanRoot.empty()) {
            auto fallback = mod.dir / mod.scanRoot;
            if (mod.scanRoot == ".") fallback = mod.dir;
            std::error_code fallbackError;
            if (ark::fs::is_directory(fallback, fallbackError)) root = fallback;
        }
        std::error_code rootError;
        std::vector<ark::fs::path> files;
        if (mod.autoMap) {
            if (!ark::fs::is_directory(root, rootError) || rootError) continue;
            for (ark::fs::recursive_directory_iterator it(root, ark::fs::directory_options::skip_permission_denied, rootError), end;
                 !rootError && it != end; it.increment(rootError)) {
                std::error_code fileError;
                if (!it->is_regular_file(fileError) || fileError) continue;
                if (it->is_symlink(fileError) || fileError) continue;
                auto ext = lowerAscii(it->path().extension().string());
                if (ext == ".txt") files.push_back(it->path());
            }
        } else {
            for (const auto& [key, value] : mod.map) {
                (void)key;
                std::vector<ark::fs::path> candidates{mod.dir / value, root / value, mod.dir / mod.textRoot / value};
                for (const auto& candidate : candidates) {
                    std::error_code fileError;
                    if (ark::fs::is_regular_file(candidate, fileError) && !fileError) {
                        files.push_back(candidate);
                        break;
                    }
                }
            }
        }
        std::sort(files.begin(), files.end());
        files.erase(std::unique(files.begin(), files.end()), files.end());
        for (const auto& file : files) {
            std::vector<Entry> entries;
            if (!parseGroupedFile(file, entries)) continue;
            for (auto& entry : entries) {
                entry.type = mod.type;
                auto group = entry.group;
                if (group.empty()) group = file.stem().string();
                entry.group = group;
                Candidate candidate{entry, file.string(), mod.dir.filename().string(), mod.priority};
                if (mod.type == Type::Story) {
                    auto cmd = fieldValue(entry, "cmd");
                    if (group.empty() || cmd.empty()) continue;
                    addCandidate(next.story, storyKey(group, cmd), candidate);
                } else if (mod.type == Type::Voice) {
                    auto key = fieldValue(entry, "char_word_id");
                    if (key.empty()) continue;
                    addCandidate(next.voice, voiceKey(key), candidate);
                } else if (mod.type == Type::Archive) {
                    auto bar = parseInt(fieldValue(entry, "bar"));
                    auto index = parseInt(fieldValue(entry, "entry"));
                    auto role = fieldValue(entry, "role");
                    if (group.empty() || bar < 0 || index < 0 || role.empty()) continue;
                    addCandidate(next.archive, archiveKey(group, bar, index, role), candidate);
                } else {
                    auto source = entry.source;
                    if (source.empty()) source = fieldValue(entry, "src");
                    auto replacement = entry.replacement;
                    if (replacement.empty()) replacement = fieldValue(entry, "text");
                    if (source.empty()) continue;
                    auto mode = lowerAscii(trim(fieldValue(entry, "match_mode")));
                    if (mode.empty()) mode = lowerAscii(trim(fieldValue(entry, "mode")));
                    bool contains = mode == "contains" || mode == "substring";
                    Direct direct{normalizeTextMatch(source), replacement, contains, mod.priority,
                                  file.string(), mod.dir.filename().string()};
                    addDirect(next, normalizeTextMatch(source), std::move(direct), contains);
                }
            }
        }
    }
    for (const auto& [key, direct] : next.directContainsByMatch) {
        (void)key;
        next.directContains.push_back(direct);
    }
    std::sort(next.directContains.begin(), next.directContains.end(), [](const Direct& a, const Direct& b) {
        if (a.priority != b.priority) return a.priority > b.priority;
        if (a.match.size() != b.match.size()) return a.match.size() > b.match.size();
        return a.match < b.match;
    });
    std::unique_lock lock(indexMutex_);
    index_ = std::move(next);
}

void Engine::addCandidate(std::unordered_map<std::string, Candidate>& index, const std::string& key,
                          const Candidate& candidate) {
    auto found = index.find(key);
    if (found == index.end()) {
        index.emplace(key, candidate);
        return;
    }
    auto& existing = found->second;
    if (existing.priority < candidate.priority) existing = candidate;
    else if (existing.priority == candidate.priority && existing.modDir == candidate.modDir)
        existing = candidate;
    // Otherwise the earlier Mod wins, matching the old loader.
}

void Engine::addDirect(Index& index, const std::string& key, Direct direct, bool contains) {
    auto& target = contains ? index.directContainsByMatch : index.directStrict;
    auto found = target.find(key);
    if (found == target.end()) {
        target.emplace(key, std::move(direct));
        return;
    }
    auto& existing = found->second;
    if (existing.priority < direct.priority) existing = std::move(direct);
    else if (existing.priority == direct.priority && existing.modDir == direct.modDir)
        existing = std::move(direct);
}

bool Engine::resolve(const Query& query, Resolved& result) const {
    result = {};
    if (query.type == Type::Direct) return false;
    std::shared_lock lock(indexMutex_);
    const Candidate* candidate = nullptr;
    std::string key;
    switch (query.type) {
        case Type::Story:
            if (query.storyId.empty() || query.cmdSrc.empty()) return false;
            key = storyKey(query.storyId, query.cmdSrc);
            if (auto found = index_.story.find(key); found != index_.story.end()) candidate = &found->second;
            break;
        case Type::Voice:
            if (query.charWordId.empty()) return false;
            key = voiceKey(query.charWordId);
            if (auto found = index_.voice.find(key); found != index_.voice.end()) candidate = &found->second;
            break;
        case Type::Archive:
            if (query.archiveKey.empty() || query.archiveBar < 0 || query.archiveEntry < 0 ||
                query.archiveRole.empty()) return false;
            key = archiveKey(query.archiveKey, query.archiveBar, query.archiveEntry, query.archiveRole);
            if (auto found = index_.archive.find(key); found != index_.archive.end()) candidate = &found->second;
            break;
        default: return false;
    }
    if (!candidate) return false;
    result.found = true;
    result.entry = candidate->entry;
    result.file = candidate->file;
    result.modDir = candidate->modDir;
    return true;
}

bool Engine::replaceDirect(const std::string& current, std::string& out, std::string* sourceFile) const {
    auto normalized = normalizeTextMatch(current);
    if (normalized.empty()) return false;
    std::shared_lock lock(indexMutex_);
    if (auto strict = index_.directStrict.find(normalized); strict != index_.directStrict.end()) {
        if (strict->second.replacement == normalized) return false;
        out = strict->second.replacement;
        if (sourceFile) *sourceFile = strict->second.file;
        return true;
    }
    for (const auto& candidate : index_.directContains) {
        if (candidate.match.empty()) continue;
        std::string replaced;
        size_t position = 0;
        bool changed = false;
        while (position <= normalized.size()) {
            auto hit = normalized.find(candidate.match, position);
            if (hit == std::string::npos) break;
            replaced.append(normalized, position, hit - position);
            replaced += candidate.replacement;
            position = hit + candidate.match.size();
            changed = true;
        }
        if (!changed) continue;
        replaced.append(normalized, position, std::string::npos);
        if (replaced == normalized) continue;
        out = std::move(replaced);
        if (sourceFile) *sourceFile = candidate.file;
        return true;
    }
    return false;
}

void Engine::setDumpEnabled(bool enabled) { dumpEnabled_.store(enabled); }
bool Engine::dumpEnabled() const { return dumpEnabled_.load(); }

ark::fs::path Engine::dumpEntityRoot(Bucket bucket, const std::string& group) const {
    return dumpRoot() / bucketName(bucket) / sanitizeFileComponent(group);
}

ark::fs::path Engine::dumpDirectory(Bucket bucket, const std::string& group) const {
    // One Mod per character/story: <TextDump>/<bucket>/<entity>/<bucket>/*.txt
    return dumpEntityRoot(bucket, group) / bucketName(bucket);
}

ark::fs::path Engine::dumpFile(Bucket bucket, const std::string& group) const {
    return dumpDirectory(bucket, group) / (sanitizeFileComponent(group) + ".txt");
}

void Engine::loadDumpGroup(Bucket bucket, const std::string& group) {
    if (bucket == Bucket::None) return;
    auto& state = dump_[static_cast<size_t>(bucket)];
    if (state.loaded) return;
    state.loaded = true;
    auto file = dumpFile(bucket, group);
    std::error_code error;
    if (!ark::fs::is_regular_file(file, error) || error) {
        // Read a dump produced by the previous flat layout when present.
        auto legacy = dumpRoot() / bucketName(bucket) / "text" / bucketName(bucket) /
                      (sanitizeFileComponent(group) + ".txt");
        if (ark::fs::is_regular_file(legacy, error) && !error) file = legacy;
    }
    std::vector<Entry> entries;
    if (!parseGroupedFile(file, entries)) return;
    auto& target = state.groups[group];
    for (auto& entry : entries) {
        entry.type = bucket == Bucket::Story ? Type::Story
                    : bucket == Bucket::Voice ? Type::Voice : Type::Archive;
        entry.group = group;
        auto id = fingerprint(entry);
        if (state.fingerprints[group].contains(id)) continue;
        state.fingerprints[group].insert(id);
        target.push_back(std::move(entry));
    }
}

void Engine::writeDumpGroup(Bucket bucket, const std::string& group) {
    auto& state = dump_[static_cast<size_t>(bucket)];
    auto& entries = state.groups[group];
    auto directory = dumpDirectory(bucket, group);
    std::error_code error;
    ark::fs::create_directories(directory, error);
    auto modRoot = dumpEntityRoot(bucket, group);
    auto ini = modRoot / "mod.ini";
    if (!ark::fs::is_regular_file(ini, error) || error) {
        std::string text = "[mod]\ntype = text\ntext_type = " + bucketName(bucket) +
                           "\ntext_root = " + bucketName(bucket) +
                           "\nauto_map_by_stem = true\npriority = 0\n";
        writeFile(ini, text);
    }
    writeFile(dumpFile(bucket, group), serializeGrouped(bucketName(bucket), group, entries));
}

void Engine::dumpQuery(const Query& query) {
    if (!dumpEnabled_.load() || query.type == Type::Direct || query.type == Type::Story) return;
    Bucket bucket = bucketOf(query.type);
    if (bucket == Bucket::None) return;
    Entry entry;
    entry.type = query.type;
    if (query.type == Type::Voice) {
        entry.group = !query.voiceGroupKey.empty() ? query.voiceGroupKey : query.voiceCharId;
        entry.fields["char_word_id"] = query.charWordId;
        if (!query.voiceId.empty()) entry.fields["voice_id"] = query.voiceId;
        if (!query.voiceGroupKey.empty()) entry.fields["word_key"] = query.voiceGroupKey;
        if (!query.voiceCharId.empty()) entry.fields["char_id"] = query.voiceCharId;
    } else if (query.type == Type::Archive) {
        entry.group = query.archiveKey;
        entry.fields["bar"] = std::to_string(query.archiveBar);
        entry.fields["entry"] = std::to_string(query.archiveEntry);
        entry.fields["role"] = query.archiveRole;
        if (!query.archiveEntryTitle.empty()) entry.fields["entry_title"] = query.archiveEntryTitle;
    } else {
        return;
    }
    entry.source = query.sourceText;
    entry.replacement = query.translatedText.empty() ? query.sourceText : query.translatedText;
    if (entry.group.empty()) return;
    std::lock_guard lock(dumpMutex_);
    loadDumpGroup(bucket, entry.group);
    auto& state = dump_[static_cast<size_t>(bucket)];
    auto id = fingerprint(entry);
    if (state.fingerprints[entry.group].contains(id)) return;
    state.fingerprints[entry.group].insert(id);
    state.groups[entry.group].push_back(entry);
    writeDumpGroup(bucket, entry.group);
}

void Engine::dumpStory(const std::string& storyId, const std::vector<Entry>& entries) {
    if (!dumpEnabled_.load() || storyId.empty()) return;
    std::lock_guard lock(dumpMutex_);
    loadDumpGroup(Bucket::Story, storyId);
    auto& state = dump_[static_cast<size_t>(Bucket::Story)];
    for (const auto& source : entries) {
        Entry entry = source;
        entry.type = Type::Story;
        entry.group = storyId;
        auto id = fingerprint(entry);
        if (state.fingerprints[storyId].contains(id)) continue;
        state.fingerprints[storyId].insert(id);
        state.groups[storyId].push_back(std::move(entry));
    }
    writeDumpGroup(Bucket::Story, storyId);
}

std::string Engine::stats() const {
    std::shared_lock lock(indexMutex_);
    std::ostringstream out;
    out << "story=" << index_.story.size() << " voice=" << index_.voice.size()
        << " archive=" << index_.archive.size() << " direct_strict=" << index_.directStrict.size()
        << " direct_contains=" << index_.directContains.size();
    return out.str();
}

Engine& engine() {
    static Engine value;
    return value;
}

} // namespace qaqr::text
