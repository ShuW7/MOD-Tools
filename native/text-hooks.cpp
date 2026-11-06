#include "text-hooks.hpp"

#include <MinHook.h>
#include <windows.h>

#include <algorithm>
#include <atomic>
#include <cstring>
#include <cwctype>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace qaqr::text::hooks {
namespace {

struct CommandContext {
    std::string name;
    std::string content;
    std::string speaker;
};

struct VoiceContext {
    std::string charWordId;
    std::string wordKey;
    std::string charId;
    std::string voiceId;
    std::string voiceText;
    std::string voiceTitle;
    std::string tag;
};

struct ArchiveContext {
    std::string charKey;
    std::string infoName;
    int bar = -1;
};

struct RecentStory {
    std::string text;
    std::string cmd;
    std::string storyId;
};

struct ThreadState {
    std::vector<CommandContext> commands;
    std::vector<VoiceContext> voices;
    std::vector<ArchiveContext> archives;
    std::vector<VoiceContext> recentVoices;
    std::vector<RecentStory> recentStory;
    std::string storyRaw;
    std::string storyResolved;
    // Sequential archive hint: title then detail for each entry.
    std::string archiveChar;
    std::string archiveInfo;
    int archiveBar = -1;
    int archiveEntry = -1;
    bool archiveDetail = false;
    std::string archiveTitle;
};

thread_local ThreadState tls;

Api api;
bool ready = false;
bool hooksInstalled = false;
ark::fs::path statusDirectory;
std::mutex logMutex;

// Dump hotkey/config is shared with the manager through TextDump/dump.ini.
std::mutex dumpConfigMutex;
std::wstring dumpHotkey = L"Ctrl+T";
UINT dumpHotkeyModifiers = MOD_CONTROL | MOD_NOREPEAT;
UINT dumpHotkeyKey = 'T';
std::atomic_bool dumpHotkeyDirty{false};
std::atomic_bool hotkeyStop{false};
std::atomic<HWND> toastWindow{nullptr};

void* storyClass = nullptr;
void* commandClass = nullptr;
void* charWordClass = nullptr;
void* cardClass = nullptr;
size_t offStoryId = 0, offStoryCommands = 0;
size_t offCommandName = 0, offCommandContent = 0, offCommandParam = 0, offCommandLine = 0;
size_t offCharWordId = 0, offWordKey = 0, offCharId = 0, offVoiceId = 0, offVoiceText = 0,
       offVoiceTitle = 0, offPlaceType = 0;
size_t offCardViewModel = 0, offInfoName = 0, offBarId = 0;
const void* cardCharacterKeyMethod = nullptr;

void logLine(const std::string& message) {
    if (statusDirectory.empty()) return;
    std::lock_guard lock(logMutex);
    std::error_code error;
    ark::fs::create_directories(statusDirectory, error);
    auto file = statusDirectory / "text.log";
    std::ofstream stream(file, std::ios::binary | std::ios::app);
    if (!stream) return;
    SYSTEMTIME time{};
    GetLocalTime(&time);
    char prefix[64]{};
    snprintf(prefix, sizeof(prefix), "%04u-%02u-%02u %02u:%02u:%02u ", time.wYear, time.wMonth,
             time.wDay, time.wHour, time.wMinute, time.wSecond);
    stream << prefix << message << "\n";
}

std::string toUtf8(const std::wstring& value) { return ark::utf8(value); }
std::wstring fromUtf8(const std::string& value) { return ark::wide(value); }

ark::fs::path dumpConfigPath() { return statusDirectory / "dump.ini"; }

bool parseHotkey(std::wstring value, UINT& modifiers, UINT& key, std::wstring& canonical) {
    for (auto& c : value) c = static_cast<wchar_t>(towupper(c));
    modifiers = MOD_NOREPEAT;
    key = 0;
    std::wstring label;
    std::wstring token;
    std::vector<std::wstring> tokens;
    for (size_t i = 0; i <= value.size(); ++i) {
        if (i == value.size() || value[i] == L'+') {
            size_t begin = 0, end = token.size();
            while (begin < end && iswspace(token[begin])) ++begin;
            while (end > begin && iswspace(token[end - 1])) --end;
            if (end > begin) tokens.push_back(token.substr(begin, end - begin));
            token.clear();
        } else {
            token += value[i];
        }
    }
    if (tokens.empty()) return false;
    for (size_t i = 0; i + 1 < tokens.size(); ++i) {
        const auto& part = tokens[i];
        if (part == L"CTRL" || part == L"CONTROL") { modifiers |= MOD_CONTROL; label += L"Ctrl+"; }
        else if (part == L"ALT") { modifiers |= MOD_ALT; label += L"Alt+"; }
        else if (part == L"SHIFT") { modifiers |= MOD_SHIFT; label += L"Shift+"; }
        else if (part == L"WIN" || part == L"WINDOWS") { modifiers |= MOD_WIN; label += L"Win+"; }
        else return false;
    }
    const auto& last = tokens.back();
    if (last.size() == 1) {
        wchar_t c = last[0];
        if ((c >= L'A' && c <= L'Z') || (c >= L'0' && c <= L'9')) {
            key = static_cast<UINT>(c);
            canonical = label + last;
            return true;
        }
        switch (c) {
            case L'-': key = VK_OEM_MINUS; break;
            case L'=': key = VK_OEM_PLUS; break;
            case L'[': key = VK_OEM_4; break;
            case L']': key = VK_OEM_6; break;
            case L';': key = VK_OEM_1; break;
            case L'\'': key = VK_OEM_7; break;
            case L',': key = VK_OEM_COMMA; break;
            case L'.': key = VK_OEM_PERIOD; break;
            case L'/': key = VK_OEM_2; break;
            case L'`': key = VK_OEM_3; break;
            default: return false;
        }
        canonical = label + last;
        return true;
    }
    if (last.size() >= 2 && last[0] == L'F') {
        int number = 0;
        for (size_t i = 1; i < last.size(); ++i) {
            if (last[i] < L'0' || last[i] > L'9') return false;
            number = number * 10 + (last[i] - L'0');
        }
        if (number < 1 || number > 24) return false;
        key = VK_F1 + static_cast<UINT>(number - 1);
        canonical = label + last;
        return true;
    }
    if (last == L"SPACE") key = VK_SPACE;
    else if (last == L"TAB") key = VK_TAB;
    else if (last == L"ESC" || last == L"ESCAPE") key = VK_ESCAPE;
    else if (last == L"ENTER" || last == L"RETURN") key = VK_RETURN;
    else if (last == L"BACKSPACE") key = VK_BACK;
    else if (last == L"DELETE" || last == L"DEL") key = VK_DELETE;
    else if (last == L"HOME") key = VK_HOME;
    else if (last == L"END") key = VK_END;
    else if (last == L"PAGEUP" || last == L"PGUP") key = VK_PRIOR;
    else if (last == L"PAGEDOWN" || last == L"PGDN") key = VK_NEXT;
    else if (last == L"UP") key = VK_UP;
    else if (last == L"DOWN") key = VK_DOWN;
    else if (last == L"LEFT") key = VK_LEFT;
    else if (last == L"RIGHT") key = VK_RIGHT;
    else return false;
    canonical = label + last;
    return true;
}

void showDumpToast(bool enabled);

bool applyDumpConfig(bool initial) {
    std::map<std::string, std::map<std::string, std::string, std::less<>>, std::less<>> sections;
    bool enabled = false;
    std::wstring hotkey = L"Ctrl+T";
    if (qaqr::text::readIni(dumpConfigPath(), sections)) {
        auto found = sections.find("dump");
        if (found != sections.end()) {
            auto value = found->second.find("enabled");
            if (value != found->second.end()) {
                auto text = qaqr::text::lowerAscii(value->second);
                enabled = text == "1" || text == "true" || text == "yes" || text == "on";
            }
            value = found->second.find("hotkey");
            if (value != found->second.end() && !value->second.empty()) hotkey = fromUtf8(value->second);
        }
    }
    wchar_t flag[8]{};
    if (initial && GetEnvironmentVariableW(L"QAQR_ARKNIGHTS_TEXT_DUMP", flag, 8) > 0) enabled = true;
    UINT modifiers = 0, key = 0;
    std::wstring canonical;
    if (!parseHotkey(hotkey, modifiers, key, canonical)) {
        logLine("invalid dump hotkey, using Ctrl+T");
        hotkey = L"Ctrl+T";
        parseHotkey(hotkey, modifiers, key, canonical);
    }
    bool changed = false;
    {
        std::lock_guard lock(dumpConfigMutex);
        if (canonical != dumpHotkey) { dumpHotkey = canonical; changed = true; }
        dumpHotkeyModifiers = modifiers;
        dumpHotkeyKey = key;
    }
    if (initial || changed) logLine("dump hotkey: " + toUtf8(dumpHotkey));
    if (engine().dumpEnabled() != enabled) {
        engine().setDumpEnabled(enabled);
        if (!initial || enabled) showDumpToast(enabled);
        if (!initial) logLine(enabled ? "text dump enabled (config)" : "text dump disabled (config)");
    }
    return changed;
}

void writeDumpConfig() {
    std::lock_guard lock(dumpConfigMutex);
    std::error_code error;
    ark::fs::create_directories(statusDirectory, error);
    std::string text = "[dump]\nhotkey = " + toUtf8(dumpHotkey) + "\nenabled = " +
                       (engine().dumpEnabled() ? "true" : "false") + "\n";
    std::ofstream stream(dumpConfigPath(), std::ios::binary | std::ios::trunc);
    if (!stream) return;
    stream << text;
}

void showDumpToast(bool enabled) {
    std::wstring hotkey;
    { std::lock_guard lock(dumpConfigMutex); hotkey = dumpHotkey; }
    auto text = std::wstring(enabled ? L"文本 Dump 已开启" : L"文本 Dump 已关闭") +
                L"\n" + hotkey + (enabled ? L" 关闭" : L" 开启");
    std::thread([text] {
        static std::once_flag once;
        std::call_once(once, [] {
            WNDCLASSEXW klass{};
            klass.cbSize = sizeof(klass);
            klass.lpfnWndProc = [](HWND window, UINT message, WPARAM wparam, LPARAM lparam) -> LRESULT {
                switch (message) {
                    case WM_PAINT: {
                        PAINTSTRUCT paint{};
                        auto dc = BeginPaint(window, &paint);
                        RECT client{};
                        GetClientRect(window, &client);
                        auto text = reinterpret_cast<std::wstring*>(GetWindowLongPtrW(window, GWLP_USERDATA));
                        auto background = CreateSolidBrush(RGB(26, 27, 31));
                        FillRect(dc, &client, background);
                        DeleteObject(background);
                        SetBkMode(dc, TRANSPARENT);
                        SetTextColor(dc, RGB(238, 238, 242));
                        auto font = CreateFontW(-22, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE,
                                                DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                                CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
                        auto old = SelectObject(dc, font);
                        RECT first = client, second = client;
                        first.bottom = client.bottom / 2 - 2;
                        second.top = client.bottom / 2 + 2;
                        if (text) {
                            auto newline = text->find(L'\n');
                            auto title = text->substr(0, newline);
                            auto hint = newline == std::wstring::npos ? std::wstring{} : text->substr(newline + 1);
                            DrawTextW(dc, title.c_str(), -1, &first, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                            auto hintFont = CreateFontW(-16, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                                                        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                                        CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
                            SelectObject(dc, hintFont);
                            SetTextColor(dc, RGB(175, 178, 186));
                            DrawTextW(dc, hint.c_str(), -1, &second, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                            DeleteObject(hintFont);
                        }
                        SelectObject(dc, old);
                        DeleteObject(font);
                        EndPaint(window, &paint);
                        return 0;
                    }
                    case WM_ERASEBKGND: return 1;
                    case WM_TIMER:
                        DestroyWindow(window);
                        return 0;
                    case WM_DESTROY: {
                        auto text = reinterpret_cast<std::wstring*>(GetWindowLongPtrW(window, GWLP_USERDATA));
                        delete text;
                        SetWindowLongPtrW(window, GWLP_USERDATA, 0);
                        PostQuitMessage(0);
                        return 0;
                    }
                    default: return DefWindowProcW(window, message, wparam, lparam);
                }
            };
            klass.hInstance = GetModuleHandleW(nullptr);
            klass.lpszClassName = L"QAQR_TextDumpToast";
            klass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
            RegisterClassExW(&klass);
        });
        if (toastWindow.load()) PostMessageW(toastWindow.load(), WM_CLOSE, 0, 0);
        int width = 560, height = 88;
        int x = (GetSystemMetrics(SM_CXSCREEN) - width) / 2;
        int y = 96;
        auto window = CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
            L"QAQR_TextDumpToast", L"", WS_POPUP, x, y, width, height, nullptr, nullptr,
            GetModuleHandleW(nullptr), nullptr);
        if (!window) return;
        toastWindow.store(window);
        SetLayeredWindowAttributes(window, 0, 235, LWA_ALPHA);
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(new std::wstring(text)));
        ShowWindow(window, SW_SHOWNOACTIVATE);
        UpdateWindow(window);
        SetTimer(window, 1, 2600, nullptr);
        MSG message{};
        while (GetMessageW(&message, nullptr, 0, 0) > 0) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        toastWindow.store(nullptr);
    }).detach();
}

void toggleDump() {
    engine().setDumpEnabled(!engine().dumpEnabled());
    writeDumpConfig();
    showDumpToast(engine().dumpEnabled());
    logLine(engine().dumpEnabled() ? "text dump enabled" : "text dump disabled");
}

void startHotkeys() {
    applyDumpConfig(true);
    std::thread([] {
        bool dumpRegistered = RegisterHotKey(nullptr, 1, dumpHotkeyModifiers, dumpHotkeyKey) != FALSE;
        bool reloadRegistered = RegisterHotKey(nullptr, 2, MOD_NOREPEAT, VK_F10) != FALSE;
        if (!dumpRegistered) logLine("dump hotkey registration failed");
        if (!reloadRegistered) logLine("F10 hotkey registration failed");
        MSG message{};
        while (!hotkeyStop.load()) {
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                if (message.message != WM_HOTKEY) continue;
                if (message.wParam == 1 && dumpRegistered) {
                    toggleDump();
                } else if (message.wParam == 2 && reloadRegistered) {
                    try { engine().reload(); logLine("text Mods reloaded: " + engine().stats()); }
                    catch (...) { logLine("text Mods reload failed"); }
                }
            }
            if (dumpHotkeyDirty.exchange(false)) {
                UnregisterHotKey(nullptr, 1);
                dumpRegistered = RegisterHotKey(nullptr, 1, dumpHotkeyModifiers, dumpHotkeyKey) != FALSE;
                std::wstring current;
                { std::lock_guard lock(dumpConfigMutex); current = dumpHotkey; }
                logLine("dump hotkey updated: " + toUtf8(current));
            }
            Sleep(250);
        }
    }).detach();
}

std::string dumpStateJson() {
    std::lock_guard lock(dumpConfigMutex);
    return "{\"success\":true,\"enabled\":" + std::string(engine().dumpEnabled() ? "true" : "false") +
           ",\"hotkey\":" + ark::jsonString(toUtf8(dumpHotkey)) + "}";
}

bool readable(const void* pointer, size_t size) {
    if (!pointer || !size) return false;
    MEMORY_BASIC_INFORMATION info{};
    if (!VirtualQuery(pointer, &info, sizeof(info))) return false;
    if (info.State != MEM_COMMIT) return false;
    if (info.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return false;
    auto begin = reinterpret_cast<uintptr_t>(pointer);
    auto end = begin + size;
    auto regionEnd = reinterpret_cast<uintptr_t>(info.BaseAddress) + info.RegionSize;
    return end >= begin && end <= regionEnd;
}

template <class T>
bool readAt(const void* base, size_t offset, T& value) {
    auto pointer = static_cast<const char*>(base) + offset;
    if (!readable(pointer, sizeof(T))) return false;
    std::memcpy(&value, pointer, sizeof(T));
    return true;
}

void* followJump(void* pointer) {
    if (!readable(pointer, 1)) return nullptr;
    auto code = static_cast<unsigned char*>(pointer);
    if (readable(code, 5) && code[0] == 0xE9) {
        int32_t displacement = 0;
        std::memcpy(&displacement, code + 1, sizeof(displacement));
        return followJump(code + 5 + displacement);
    }
    if (readable(code, 2) && code[0] == 0xEB) {
        return followJump(code + 2 + static_cast<int8_t>(code[1]));
    }
    if (readable(code, 6) && code[0] == 0xFF && code[1] == 0x25) {
        int32_t displacement = 0;
        std::memcpy(&displacement, code + 2, sizeof(displacement));
        void* target = nullptr;
        std::memcpy(&target, code + 6 + displacement, sizeof(target));
        return followJump(target);
    }
    if (readable(code, 6) && code[0] == 0x48 && code[1] == 0xFF && code[2] == 0x25) {
        int32_t displacement = 0;
        std::memcpy(&displacement, code + 3, sizeof(displacement));
        void* target = nullptr;
        std::memcpy(&target, code + 7 + displacement, sizeof(target));
        return followJump(target);
    }
    return pointer;
}

void* methodPointer(const void* method) {
    if (!method) return nullptr;
    void* pointer = nullptr;
    if (readAt(method, 0, pointer) && pointer) return followJump(pointer);
    if (readAt(method, sizeof(void*), pointer) && pointer) return followJump(pointer);
    return nullptr;
}

std::string methodTypeName(const void* method, uint32_t index) {
    if (!api.methodParam || !api.typeName) return {};
    auto type = api.methodParam(method, index);
    if (!type) return {};
    auto name = api.typeName(type);
    std::string value = name ? name : "";
    if (name && api.free) api.free(name);
    return value;
}

void* findClass(const char* fullName) {
    if (!fullName || !api.domainGetAssemblies) return nullptr;
    std::string name = fullName;
    auto dot = name.rfind('.');
    std::string space = dot == std::string::npos ? std::string{} : name.substr(0, dot);
    std::string simple = dot == std::string::npos ? name : name.substr(dot + 1);
    size_t count = 0;
    auto assemblies = api.domainGetAssemblies(api.domain, &count);
    if (!assemblies) return nullptr;
    for (size_t i = 0; i < count; ++i) {
        if (!assemblies[i]) continue;
        auto image = api.assemblyGetImage(assemblies[i]);
        if (!image) continue;
        if (auto klass = api.classFromName(image, space.c_str(), simple.c_str())) return klass;
    }
    // Obfuscated/renamed namespaces still expose the simple class name.
    for (size_t i = 0; i < count; ++i) {
        if (!assemblies[i]) continue;
        auto image = api.assemblyGetImage(assemblies[i]);
        if (!image) continue;
        auto total = api.imageClassCount(image);
        for (size_t index = 0; index < total; ++index) {
            auto klass = api.imageClass(image, index);
            if (!klass) continue;
            auto candidate = api.className(klass);
            if (candidate && simple == candidate) return klass;
        }
    }
    return nullptr;
}

void dumpClassFields(void* klass, const char* tag) {
    if (!klass || !api.classFields || !api.fieldName || !api.fieldOffset) return;
    logLine(std::string("fields for ") + tag + ":");
    void* iterator = nullptr;
    int count = 0;
    while (auto field = api.classFields(klass, &iterator)) {
        auto name = api.fieldName(field);
        logLine(std::string("  ") + (name ? name : "?") + " offset=" + std::to_string(api.fieldOffset(field)));
        if (++count >= 200) break;
    }
}

void dumpClassMethods(void* klass, const char* tag) {
    if (!klass || !api.classMethods) return;
    logLine(std::string("methods for ") + tag + ":");
    void* iterator = nullptr;
    int count = 0;
    while (auto method = api.classMethods(klass, &iterator)) {
        auto name = api.methodName ? api.methodName(method) : "?";
        auto params = api.methodParamCount ? api.methodParamCount(method) : 0;
        std::string signature;
        for (uint32_t index = 0; index < params; ++index) {
            if (index) signature += ",";
            signature += methodTypeName(method, index);
        }
        logLine(std::string("  ") + (name ? name : "?") + "(" + signature + ")");
        if (++count >= 300) break;
    }
}

void dumpClassCandidates(const char* token) {
    if (!token || !api.domainGetAssemblies) return;
    size_t count = 0;
    auto assemblies = api.domainGetAssemblies(api.domain, &count);
    if (!assemblies) return;
    logLine(std::string("class candidates for ") + token + ":");
    for (size_t i = 0; i < count; ++i) {
        if (!assemblies[i] || !api.assemblyGetImage) continue;
        auto image = api.assemblyGetImage(assemblies[i]);
        if (!image || !api.imageClassCount || !api.imageClass || !api.className) continue;
        auto total = api.imageClassCount(image);
        for (size_t index = 0; index < total; ++index) {
            auto klass = api.imageClass(image, index);
            if (!klass) continue;
            auto name = api.className(klass);
            if (!name || !strstr(name, token)) continue;
            std::string full;
            if (api.classNamespace) {
                auto space = api.classNamespace(klass);
                if (space && *space) full = std::string(space) + ".";
            }
            full += name;
            logLine("  " + full);
        }
    }
}

const void* findMethod(void* klass, const char* name, const int* counts, size_t countSize) {
    if (!klass || !name) return nullptr;
    for (size_t i = 0; i < countSize; ++i) {
        auto method = api.methodFromName(klass, name, counts[i]);
        if (method) return method;
    }
    if (!api.classMethods) return nullptr;
    void* iterator = nullptr;
    while (auto method = api.classMethods(klass, &iterator)) {
        auto methodName = api.methodName(method);
        if (!methodName || std::strcmp(methodName, name) != 0) continue;
        auto params = api.methodParamCount ? api.methodParamCount(method) : 0;
        for (size_t i = 0; i < countSize; ++i) if (params == static_cast<uint32_t>(counts[i])) return method;
    }
    return nullptr;
}

const void* findMethodWithParams(void* klass, const char* name,
                                 const std::vector<std::string>& hints) {
    if (!klass || !name || !api.classMethods) return nullptr;
    void* iterator = nullptr;
    while (auto method = api.classMethods(klass, &iterator)) {
        auto methodName = api.methodName ? api.methodName(method) : nullptr;
        if (!methodName || std::strcmp(methodName, name) != 0) continue;
        auto params = api.methodParamCount ? api.methodParamCount(method) : 0;
        if (params != hints.size()) continue;
        bool matches = true;
        for (uint32_t index = 0; index < params && matches; ++index) {
            auto type = methodTypeName(method, index);
            if (!hints[index].empty() && type.find(hints[index]) == std::string::npos) matches = false;
        }
        if (matches) return method;
    }
    return nullptr;
}

bool fieldOffset(void* klass, const char* name, size_t& offset) {
    if (!klass || !api.classFieldFromName) return false;
    auto field = api.classFieldFromName(klass, name);
    if (!field || !api.fieldOffset) return false;
    offset = api.fieldOffset(field);
    return true;
}

int safeStringLength(void* object) {
    if (!object || !api.stringLength) return -1;
    __try { return api.stringLength(object); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return -1; }
}

const wchar_t* safeStringChars(void* object) {
    if (!object || !api.stringChars) return nullptr;
    __try { return api.stringChars(object); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return nullptr; }
}

void* safeStringNew(const wchar_t* chars, int32_t length) {
    if (!api.stringNew) return nullptr;
    __try { return api.stringNew(chars, length); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return nullptr; }
}

std::string managedString(void* object) {
    if (!object) return {};
    auto length = safeStringLength(object);
    if (length <= 0 || length > 1000000) return {};
    auto chars = safeStringChars(object);
    if (!chars || !readable(chars, static_cast<size_t>(length) * sizeof(wchar_t))) return {};
    int size = WideCharToMultiByte(CP_UTF8, 0, chars, length, nullptr, 0, nullptr, nullptr);
    if (size <= 0) return {};
    std::string out(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, chars, length, out.data(), size, nullptr, nullptr);
    return out;
}

void* newString(const std::string& value) {
    if (!api.stringNew) return nullptr;
    int length = MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0);
    std::wstring wide;
    if (length > 0) {
        wide.resize(static_cast<size_t>(length));
        MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), wide.data(), length);
    }
    return safeStringNew(wide.c_str(), static_cast<int32_t>(wide.size()));
}

bool readFieldString(void* object, size_t offset, std::string& out) {
    void* value = nullptr;
    if (!readAt(object, offset, value) || !value) return false;
    out = managedString(value);
    return true;
}

bool readFieldPtr(void* object, size_t offset, void*& out) {
    out = nullptr;
    return readAt(object, offset, out) && out != nullptr;
}

void* fieldPtr(void* object, size_t offset) {
    void* value = nullptr;
    return readAt(object, offset, value) ? value : nullptr;
}

void rememberStory(const std::string& text, const std::string& cmd) {
    if (text.empty() || cmd.empty()) return;
    for (const auto& item : tls.recentStory) if (item.text == text && item.cmd == cmd) return;
    tls.recentStory.push_back({text, cmd, !tls.storyResolved.empty() ? tls.storyResolved : tls.storyRaw});
    if (tls.recentStory.size() > 256) tls.recentStory.erase(tls.recentStory.begin());
}

CommandContext readCommand(void* command) {
    CommandContext context;
    if (!command || !commandClass || offCommandName < 0x10 || offCommandContent < 0x10) return context;
    readFieldString(command, offCommandName, context.name);
    readFieldString(command, offCommandContent, context.content);
    // Command.param is a Dictionary<string,string> in the current game model.
    auto dictionary = fieldPtr(command, offCommandParam);
    if (dictionary && readable(dictionary, 0x28)) {
        int count = 0;
        void* entries = nullptr;
        std::memcpy(&count, static_cast<char*>(dictionary) + 0x20, sizeof(count));
        std::memcpy(&entries, static_cast<char*>(dictionary) + 0x18, sizeof(entries));
        if (count > 0 && count < 4096 && entries) {
            for (int i = 0; i < count; ++i) {
                auto entry = static_cast<char*>(entries) + 0x20 + static_cast<size_t>(i) * 24;
                if (!readable(entry, 24)) break;
                void* key = nullptr;
                void* value = nullptr;
                std::memcpy(&key, entry + 8, sizeof(key));
                std::memcpy(&value, entry + 16, sizeof(value));
                auto keyText = managedString(key);
                if (keyText.empty()) continue;
                auto lower = lowerAscii(keyText);
                if (lower == "name" || lower == "speaker" || lower == "title") {
                    auto valueText = managedString(value);
                    if (!valueText.empty()) {
                        context.speaker = valueText;
                        if (lower == "name") break;
                    }
                }
            }
        }
    }
    return context;
}

VoiceContext readVoice(void* charWord, const char* tag) {
    VoiceContext context;
    context.tag = tag ? tag : "";
    if (!charWord || !charWordClass || offCharWordId < 0x10) return context;
    readFieldString(charWord, offCharWordId, context.charWordId);
    readFieldString(charWord, offWordKey, context.wordKey);
    readFieldString(charWord, offCharId, context.charId);
    readFieldString(charWord, offVoiceId, context.voiceId);
    readFieldString(charWord, offVoiceText, context.voiceText);
    readFieldString(charWord, offVoiceTitle, context.voiceTitle);
    return context;
}

bool voiceMatches(const VoiceContext& context, const std::string& source, const std::string& translated) {
    if (!context.voiceText.empty() && (context.voiceText == source || context.voiceText == translated))
        return true;
    if (!context.voiceTitle.empty() && (context.voiceTitle == source || context.voiceTitle == translated))
        return true;
    return false;
}

ArchiveContext readArchive(void* view) {
    ArchiveContext context;
    if (!view) return context;
    // These offsets are the current Torappu HandBookInfoView layout used by
    // the legacy loader. Failure simply disables archive replacement.
    auto card = fieldPtr(view, offCardViewModel);
    if (card) {
        auto cardModel = fieldPtr(card, 0x20);
        if (cardModel && cardCharacterKeyMethod && api.invoke) {
            void* exception = nullptr;
            auto key = api.invoke(cardCharacterKeyMethod, cardModel, nullptr, &exception);
            if (!exception) context.charKey = managedString(key);
        }
    }
    readFieldString(view, offInfoName, context.infoName);
    int bar = -1;
    if (readAt(view, offBarId, bar)) context.bar = bar;
    if (context.charKey.empty() && context.infoName.empty() && context.bar < 0) return {};
    return context;
}

bool buildStoryQuery(const std::string& source, const std::string& translated, Query& query) {
    auto storyId = !tls.storyResolved.empty() ? tls.storyResolved : tls.storyRaw;
    if (storyId.empty()) return false;
    std::string cmd;
    if (!tls.commands.empty()) {
        const auto& context = tls.commands.back();
        if (!context.content.empty() && (context.content == source || context.content == translated)) {
            cmd = context.name + "[content]";
        } else if (!context.speaker.empty() &&
                   (context.speaker == source || context.speaker == translated)) {
            cmd = context.name + "[name]";
        }
    }
    if (cmd.empty()) {
        for (auto it = tls.recentStory.rbegin(); it != tls.recentStory.rend(); ++it) {
            if (it->text == source || it->text == translated) {
                cmd = it->cmd;
                if (!it->storyId.empty()) storyId = it->storyId;
                break;
            }
        }
    }
    if (cmd.empty()) return false;
    query.type = Type::Story;
    query.storyId = storyId;
    query.cmdSrc = cmd;
    query.sourceText = source;
    query.translatedText = translated;
    return true;
}

bool buildVoiceQuery(const std::string& source, const std::string& translated, Query& query) {
    const VoiceContext* found = nullptr;
    for (auto it = tls.voices.rbegin(); it != tls.voices.rend(); ++it) {
        if (voiceMatches(*it, source, translated)) { found = &*it; break; }
    }
    if (!found) {
        for (auto it = tls.recentVoices.rbegin(); it != tls.recentVoices.rend(); ++it) {
            if (voiceMatches(*it, source, translated)) { found = &*it; break; }
        }
    }
    if (!found || found->charWordId.empty()) return false;
    query.type = Type::Voice;
    query.charWordId = found->charWordId;
    query.voiceGroupKey = found->wordKey;
    query.voiceCharId = found->charId;
    query.voiceId = found->voiceId;
    query.sourceText = source;
    query.translatedText = translated;
    return true;
}

bool buildArchiveQuery(const std::string& source, const std::string& translated, Query& query) {
    if (tls.archives.empty()) return false;
    const auto& context = tls.archives.back();
    if (context.charKey.empty() || context.bar < 0) return false;
    if (context.charKey != tls.archiveChar || context.bar != tls.archiveBar ||
        context.infoName != tls.archiveInfo) {
        tls.archiveChar = context.charKey;
        tls.archiveInfo = context.infoName;
        tls.archiveBar = context.bar;
        tls.archiveEntry = -1;
        tls.archiveDetail = false;
        tls.archiveTitle.clear();
    }
    std::string role;
    if (!tls.archiveDetail) {
        ++tls.archiveEntry;
        role = "title";
        tls.archiveTitle = !source.empty() ? source : translated;
        tls.archiveDetail = true;
    } else {
        role = "detail";
        tls.archiveDetail = false;
    }
    query.type = Type::Archive;
    query.archiveKey = context.charKey;
    query.archiveBar = context.bar;
    query.archiveEntry = tls.archiveEntry;
    query.archiveRole = role;
    query.archiveEntryTitle = tls.archiveTitle;
    query.sourceText = source;
    query.translatedText = translated;
    return true;
}

Query buildQuery(const std::string& source, const std::string& translated) {
    Query query;
    if (buildStoryQuery(source, translated, query)) return query;
    if (buildVoiceQuery(source, translated, query)) return query;
    if (buildArchiveQuery(source, translated, query)) return query;
    return {};
}

void handleStoryStarted(const std::string& storyId) {
    tls.storyRaw = storyId;
    if (!storyId.empty()) tls.storyResolved.clear();
}

void handleParsedStory(void* story) {
    if (!story || !storyClass || offStoryId < 0x10 || offStoryCommands < 0x10) return;
    std::string storyId;
    readFieldString(story, offStoryId, storyId);
    if (storyId.empty()) return;
    tls.storyResolved = storyId;
    if (!engine().dumpEnabled()) return;
    auto list = fieldPtr(story, offStoryCommands);
    if (!list) return;
    void* items = nullptr;
    int count = 0;
    if (!readAt(list, 0x10, items) || !readAt(list, 0x18, count)) return;
    if (!items || count <= 0 || count > 100000) return;
    if (!readable(items, 0x20 + static_cast<size_t>(count) * sizeof(void*))) return;
    std::vector<Entry> entries;
    for (int i = 0; i < count; ++i) {
        void* command = nullptr;
        std::memcpy(&command, static_cast<char*>(items) + 0x20 + static_cast<size_t>(i) * sizeof(void*),
                    sizeof(command));
        if (!command) continue;
        auto context = readCommand(command);
        if (!context.name.empty() && !context.content.empty()) {
            Entry entry;
            entry.type = Type::Story;
            entry.fields["cmd"] = context.name + "[content]";
            entry.source = context.content;
            entry.replacement = context.content;
            entries.push_back(std::move(entry));
        }
        if (!context.name.empty() && !context.speaker.empty()) {
            Entry entry;
            entry.type = Type::Story;
            entry.fields["cmd"] = context.name + "[name]";
            entry.source = context.speaker;
            entry.replacement = context.speaker;
            entries.push_back(std::move(entry));
        }
    }
    engine().dumpStory(storyId, entries);
}

// --- hooks -----------------------------------------------------------------
using TranslateFn = void* (*)(void*, void*);
TranslateFn originalTranslate = nullptr;

void* hookTranslate(void* self, void* sourceObject) {
    void* result = originalTranslate ? originalTranslate(self, sourceObject) : nullptr;
    try {
        auto source = managedString(sourceObject);
        auto translated = managedString(result);
        if (source.empty()) return result;
        auto query = buildQuery(source, translated.empty() ? source : translated);
        if (query.type == Type::Story || query.type == Type::Voice || query.type == Type::Archive) {
            if (engine().dumpEnabled() && query.type != Type::Story) engine().dumpQuery(query);
            Resolved resolved;
            if (engine().resolve(query, resolved) && !resolved.entry.replacement.empty()) {
                if (resolved.entry.replacement != translated) {
                    if (auto replacement = newString(resolved.entry.replacement)) result = replacement;
                }
            }
        }
    } catch (...) {
        logLine("translate hook failed");
    }
    return result;
}

using SetTextFn = void (*)(void*, void*, void*, void*, void*, void*);
SetTextFn originalSetText = nullptr;

void* directArgument(void* value) {
    auto current = managedString(value);
    if (current.empty()) return value;
    std::string replacement;
    if (!engine().replaceDirect(current, replacement)) return value;
    if (auto created = newString(replacement)) return created;
    return value;
}

void hookSetText(void* self, void* textComponent, void* first, void* second, void* a, void* b) {
    void* one = directArgument(first);
    void* two = directArgument(second);
    if (originalSetText) originalSetText(self, textComponent, one, two, a, b);
}

using StartStoryFn = void (*)(void*, void*, void*, void*);
StartStoryFn originalStartStory2 = nullptr;
using StartStory3Fn = void (*)(void*, void*, void*, void*, void*);
StartStory3Fn originalStartStory3 = nullptr;

void hookStartStory2(void* self, void* story, void* a, void* b) {
    handleStoryStarted(managedString(story));
    if (originalStartStory2) originalStartStory2(self, story, a, b);
}

void hookStartStory3(void* self, void* story, void* a, void* b, void* c) {
    handleStoryStarted(managedString(story));
    if (originalStartStory3) originalStartStory3(self, story, a, b, c);
}

using StartStoryInternalFn = void (*)(void*, void*, void*, void*, void*, void*);
StartStoryInternalFn originalStartStoryInternal = nullptr;

void hookStartStoryInternal(void* self, void* a, void* b, void* c, void* hint, void* e) {
    auto story = managedString(hint);
    handleStoryStarted(story.empty() ? "<story_by_string>" : story);
    if (originalStartStoryInternal) originalStartStoryInternal(self, a, b, c, hint, e);
}

using TryParse4Fn = bool (*)(void*, void*, void*, void**);
TryParse4Fn originalTryParse4 = nullptr;
using TryParse3Fn = bool (*)(void*, void*, void**);
TryParse3Fn originalTryParse3 = nullptr;

bool hookTryParse4(void* self, void* story, void* param, void** output) {
    bool result = originalTryParse4 ? originalTryParse4(self, story, param, output) : false;
    if (result && output && *output) handleParsedStory(*output);
    return result;
}

bool hookTryParse3(void* self, void* story, void** output) {
    bool result = originalTryParse3 ? originalTryParse3(self, story, output) : false;
    if (result && output && *output) handleParsedStory(*output);
    return result;
}

using DialogFn = bool (*)(void*, void*, void*);
DialogFn originalDialog = nullptr;
DialogFn originalAside = nullptr;
DialogFn originalMultiline = nullptr;
DialogFn originalEndtip = nullptr;
DialogFn originalSubtitle = nullptr;

template <DialogFn* Original>
bool callWithCommand(void* self, void* command, void* extra) {
    auto before = tls.commands.size();
    try {
        auto context = readCommand(command);
        if (!context.name.empty()) {
            tls.commands.push_back(context);
            rememberStory(context.content, context.name + "[content]");
            rememberStory(context.speaker, context.name + "[name]");
        }
    } catch (...) {
    }
    bool result = *Original ? (*Original)(self, command, extra) : false;
    while (tls.commands.size() > before) tls.commands.pop_back();
    return result;
}

bool hookDialog(void* self, void* command, void* extra) {
    return callWithCommand<&originalDialog>(self, command, extra);
}
bool hookAside(void* self, void* command, void* extra) {
    return callWithCommand<&originalAside>(self, command, extra);
}
bool hookMultiline(void* self, void* command, void* extra) {
    return callWithCommand<&originalMultiline>(self, command, extra);
}
bool hookEndtip(void* self, void* command, void* extra) {
    return callWithCommand<&originalEndtip>(self, command, extra);
}
bool hookSubtitle(void* self, void* command, void* extra) {
    return callWithCommand<&originalSubtitle>(self, command, extra);
}

using VoiceFn = void (*)(void*, void*, void*);
VoiceFn originalHomeVoice = nullptr;
VoiceFn originalBattleVoice = nullptr;

void pushVoice(VoiceContext context, bool remember, VoiceFn original, void* self, void* data, void* extra) {
    bool pushed = false;
    if (!context.charWordId.empty() || !context.voiceText.empty() || !context.voiceTitle.empty()) {
        tls.voices.push_back(context);
        pushed = true;
    }
    if (remember) {
        tls.recentVoices.push_back(context);
        if (tls.recentVoices.size() > 64) tls.recentVoices.erase(tls.recentVoices.begin());
    }
    if (original) original(self, data, extra);
    if (pushed) tls.voices.pop_back();
}

void hookHomeVoice(void* self, void* charWord, void* extra) {
    try {
        pushVoice(readVoice(charWord, "HomeIllust.LoadTargetText"), true, originalHomeVoice, self, charWord, extra);
    } catch (...) {
        if (originalHomeVoice) originalHomeVoice(self, charWord, extra);
    }
}

void hookBattleVoice(void* self, void* charWord, void* extra) {
    try {
        pushVoice(readVoice(charWord, "BattleFinish.IllustView"), false, originalBattleVoice, self, charWord, extra);
    } catch (...) {
        if (originalBattleVoice) originalBattleVoice(self, charWord, extra);
    }
}

using ConvertFn = void* (*)(void*, void*);
ConvertFn originalConvert = nullptr;

void* hookConvert(void* self, void* data) {
    try {
        auto context = readVoice(data, "HandBook.ConvertFromData");
        if (!context.charWordId.empty() || !context.voiceText.empty()) {
            tls.recentVoices.push_back(context);
            if (tls.recentVoices.size() > 64) tls.recentVoices.erase(tls.recentVoices.begin());
        }
    } catch (...) {
    }
    return originalConvert ? originalConvert(self, data) : nullptr;
}

using ArchiveFn = void (*)(void*, void*);
ArchiveFn originalArchive = nullptr;

void hookArchive(void* self, void* extra) {
    bool pushed = false;
    try {
        auto context = readArchive(self);
        if (!context.charKey.empty() || context.bar >= 0) {
            tls.archives.push_back(context);
            pushed = true;
        }
    } catch (...) {
    }
    if (originalArchive) originalArchive(self, extra);
    if (pushed) tls.archives.pop_back();
}

struct HookInstall {
    const char* className;
    const char* methodName;
    int counts[4];
    int countCount;
    void* detour;
    void** original;
    const char* tag;
};

bool installHook(const HookInstall& hook, int& installed) {
    auto klass = findClass(hook.className);
    if (!klass) { logLine(std::string("missing class: ") + hook.className); return false; }
    auto method = findMethod(klass, hook.methodName, hook.counts, static_cast<size_t>(hook.countCount));
    if (!method) { logLine(std::string("missing method: ") + hook.className + "." + hook.methodName); return false; }
    auto target = methodPointer(method);
    if (!target) { logLine(std::string("no method pointer: ") + hook.tag); return false; }
    auto status = MH_CreateHook(target, hook.detour, hook.original);
    if (status != MH_OK) {
        logLine(std::string("MH_CreateHook failed: ") + hook.tag + " status=" + std::to_string(status));
        return false;
    }
    status = MH_QueueEnableHook(target);
    if (status != MH_OK) {
        logLine(std::string("MH_EnableHook failed: ") + hook.tag + " status=" + std::to_string(status));
        return false;
    }
    ++installed;
    std::string signature;
    auto parameters = api.methodParamCount ? api.methodParamCount(method) : 0;
    for (uint32_t index = 0; index < parameters; ++index) {
        if (index) signature += ",";
        signature += methodTypeName(method, index);
    }
    logLine(std::string("hook installed: ") + hook.tag + " (" + signature + ")");

    return true;
}

bool resolveModel() {
    storyClass = findClass("Torappu.AVG.Story");
    commandClass = findClass("Torappu.AVG.Command");
    charWordClass = findClass("Torappu.CharWordData");
    cardClass = findClass("Torappu.UI.HandBook.HandBookCardViewModel");
    if (storyClass) {
        logLine(std::string("Story: id=") + std::to_string(fieldOffset(storyClass, "id", offStoryId)) +
                " commands=" + std::to_string(fieldOffset(storyClass, "commands", offStoryCommands)));
    }
    if (commandClass) {
        fieldOffset(commandClass, "command", offCommandName);
        fieldOffset(commandClass, "content", offCommandContent);
        fieldOffset(commandClass, "param", offCommandParam);
        fieldOffset(commandClass, "lineNumber", offCommandLine);
        logLine("Command offsets: name=" + std::to_string(offCommandName) +
                " content=" + std::to_string(offCommandContent) +
                " param=" + std::to_string(offCommandParam));
    }
    if (charWordClass) {
        fieldOffset(charWordClass, "charWordId", offCharWordId);
        fieldOffset(charWordClass, "wordKey", offWordKey);
        fieldOffset(charWordClass, "charId", offCharId);
        fieldOffset(charWordClass, "voiceId", offVoiceId);
        fieldOffset(charWordClass, "voiceText", offVoiceText);
        fieldOffset(charWordClass, "voiceTitle", offVoiceTitle);
        fieldOffset(charWordClass, "placeType", offPlaceType);
        logLine("CharWord offsets: id=" + std::to_string(offCharWordId) +
                " text=" + std::to_string(offVoiceText));
    }
    if (cardClass) {
        auto method = findMethod(cardClass, "get_characterKey", nullptr, 0);
        if (!method) {
            const int counts[] = {0};
            method = findMethod(cardClass, "get_characterKey", counts, 1);
        }
        cardCharacterKeyMethod = method;
    }
    // HandBookInfoView layout used by the legacy loader.
    offCardViewModel = 0x40;
    offInfoName = 0x58;
    offBarId = 0xE4;
    return commandClass && charWordClass;
}

} // namespace

bool install(const Api& value, const ark::fs::path& dataRoot, const ark::fs::path& modRoot,
             const ark::fs::path& status) {
    if (ready) return hooksInstalled;
    api = value;
    statusDirectory = status;
    logLine("text module initializing; dataRoot=" + dataRoot.string() + " modRoot=" + modRoot.string());
    if (!api.domainGetAssemblies || !api.classFromName || !api.methodFromName || !api.stringNew) {
        logLine("required IL2CPP API missing");
        return false;
    }
    resolveModel();
    dumpClassFields(storyClass, "Torappu.AVG.Story");
    dumpClassFields(commandClass, "Torappu.AVG.Command");
    dumpClassFields(charWordClass, "Torappu.CharWordData");
    dumpClassMethods(findClass("Torappu.AVGTextManager"), "Torappu.AVGTextManager");
    dumpClassMethods(findClass("Torappu.UI.UITextAspects"), "Torappu.UI.UITextAspects");
    dumpClassMethods(findClass("Torappu.AVG.AVGParser"), "Torappu.AVG.AVGParser");
    dumpClassMethods(findClass("Torappu.UI.BattleFinish.BattleFinishIllustView"), "BattleFinishIllustView");
    dumpClassCandidates("AVGTextManager");
    dumpClassCandidates("UITextAspects");
    dumpClassCandidates("AVGParser");
    engine().initialize(dataRoot, modRoot);
    {
        wchar_t flag[8]{};
        if (GetEnvironmentVariableW(L"QAQR_ARKNIGHTS_TEXT_DUMP", flag, 8) > 0) engine().setDumpEnabled(true);
    }
    logLine("text index: " + engine().stats());
    startHotkeys();
    struct InstallEntry {
        HookInstall hook;
        bool required;
    } entries[] = {
        {{"Torappu.AVGTextManager", "Translate", {1}, 1, reinterpret_cast<void*>(&hookTranslate),
          reinterpret_cast<void**>(&originalTranslate), "AVGTextManager.Translate"}, true},
        {{"Torappu.UI.UITextAspects", "SetText", {5}, 1, reinterpret_cast<void*>(&hookSetText),
          reinterpret_cast<void**>(&originalSetText), "UITextAspects.SetText"}, true},
        {{"Torappu.AVG.AVG", "StartStory", {2}, 1, reinterpret_cast<void*>(&hookStartStory2),
          reinterpret_cast<void**>(&originalStartStory2), "AVG.StartStory(2)"}, false},
        {{"Torappu.AVG.AVG", "StartStory", {3}, 1, reinterpret_cast<void*>(&hookStartStory3),
          reinterpret_cast<void**>(&originalStartStory3), "AVG.StartStory(3)"}, false},
        {{"Torappu.AVG.DialogPanel", "_ExecuteDialog", {1}, 1, reinterpret_cast<void*>(&hookDialog),
          reinterpret_cast<void**>(&originalDialog), "DialogPanel._ExecuteDialog"}, false},
        {{"Torappu.AVG.DialogPanel", "_ExecuteAside", {1}, 1, reinterpret_cast<void*>(&hookAside),
          reinterpret_cast<void**>(&originalAside), "DialogPanel._ExecuteAside"}, false},
        {{"Torappu.AVG.DialogPanel", "_ExecuteMultiline", {1}, 1,
          reinterpret_cast<void*>(&hookMultiline), reinterpret_cast<void**>(&originalMultiline),
          "DialogPanel._ExecuteMultiline"}, false},
        {{"Torappu.AVG.DialogPanel", "_ExecuteEndtip", {1}, 1, reinterpret_cast<void*>(&hookEndtip),
          reinterpret_cast<void**>(&originalEndtip), "DialogPanel._ExecuteEndtip"}, false},
        {{"Torappu.AVG.SubtitlePanel", "_ExecuteSubtitle", {1}, 1,
          reinterpret_cast<void*>(&hookSubtitle), reinterpret_cast<void**>(&originalSubtitle),
          "SubtitlePanel._ExecuteSubtitle"}, false},
        {{"Torappu.UI.Home.HomeIllustView", "_LoadTargetText", {1, 2}, 2,
          reinterpret_cast<void*>(&hookHomeVoice), reinterpret_cast<void**>(&originalHomeVoice),
          "HomeIllustView._LoadTargetText"}, false},
        {{"Torappu.UI.HandBook.HandBookInfoTextViewModel", "ConvertFromData", {1}, 1,
          reinterpret_cast<void*>(&hookConvert), reinterpret_cast<void**>(&originalConvert),
          "HandBookInfoTextViewModel.ConvertFromData"}, false},
        {{"Torappu.UI.HandBook.HandBookInfoView", "RenderCurrentBarId", {0, 1}, 2,
          reinterpret_cast<void*>(&hookArchive), reinterpret_cast<void**>(&originalArchive),
          "HandBookInfoView.RenderCurrentBarId"}, false},
    };
    int installed = 0;
    for (const auto& entry : entries) {
        try {
            installHook(entry.hook, installed);
        } catch (...) {
            logLine(std::string("hook install exception: ") + entry.hook.tag);
        }
    }
    try {
        auto parser = findClass("Torappu.AVG.AVGParser");
        auto method = findMethodWithParams(parser, "TryParse",
            {"System.String", "Torappu.AVG.Story.StoryParam", "Torappu.AVG.Story&"});
        if (method) {
            auto target = methodPointer(method);
            if (target && MH_CreateHook(target, reinterpret_cast<void*>(&hookTryParse4),
                                        reinterpret_cast<void**>(&originalTryParse4)) == MH_OK) {
                if (MH_QueueEnableHook(target) == MH_OK) {
                    ++installed;
                    logLine("hook installed: AVGParser.TryParse(string,param,out story)");
                }
            }
        } else {
            logLine("missing method: AVGParser.TryParse(string,param,out story)");
        }
        method = findMethodWithParams(parser, "TryParse", {"System.String", "Torappu.AVG.Story&"});
        if (method) {
            auto target = methodPointer(method);
            if (target && MH_CreateHook(target, reinterpret_cast<void*>(&hookTryParse3),
                                        reinterpret_cast<void**>(&originalTryParse3)) == MH_OK) {
                if (MH_QueueEnableHook(target) == MH_OK) {
                    ++installed;
                    logLine("hook installed: AVGParser.TryParse(string,out story)");
                }
            }
        }
    } catch (...) {
        logLine("TryParse hook install exception");
    }
    if (installed) {
        auto status = MH_ApplyQueued();
        if (status != MH_OK) logLine("MH_ApplyQueued failed: " + std::to_string(status));
    }
    hooksInstalled = installed > 0;
    ready = hooksInstalled;
    logLine("text hooks active: " + std::to_string(installed));
    return hooksInstalled;
}

void uninstall() {
    // The process is shutting down; do not call into IL2CPP.
    hooksInstalled = false;
}

bool installed() { return hooksInstalled; }

std::string control(const std::string& command) {
    if (!ready) throw std::runtime_error("Text runtime is not ready");
    if (command == "status") return dumpStateJson();
    if (command == "on" || command == "off" || command == "toggle") {
        bool target = command == "toggle" ? !engine().dumpEnabled() : command == "on";
        engine().setDumpEnabled(target);
        writeDumpConfig();
        showDumpToast(target);
        logLine(target ? "text dump enabled (control)" : "text dump disabled (control)");
        return dumpStateJson();
    }
    const std::string prefix = "hotkey=";
    if (command.rfind(prefix, 0) == 0) {
        auto value = fromUtf8(command.substr(prefix.size()));
        UINT modifiers = 0, key = 0;
        std::wstring canonical;
        if (!parseHotkey(value, modifiers, key, canonical))
            throw std::runtime_error("Invalid dump hotkey");
        {
            std::lock_guard lock(dumpConfigMutex);
            dumpHotkey = canonical;
            dumpHotkeyModifiers = modifiers;
            dumpHotkeyKey = key;
        }
        dumpHotkeyDirty.store(true);
        writeDumpConfig();
        logLine("dump hotkey updated via control: " + toUtf8(canonical));
        return dumpStateJson();
    }
    throw std::runtime_error("Unknown dump control command");
}

} // namespace qaqr::text::hooks
