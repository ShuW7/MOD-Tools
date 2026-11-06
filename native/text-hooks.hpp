#pragma once
#include "text.hpp"

#include <cstdint>

namespace qaqr::text::hooks {

struct Api {
    void* gameAssembly = nullptr;
    void* domain = nullptr;
    const void** (*domainGetAssemblies)(void*, size_t*) = nullptr;
    void* (*assemblyGetImage)(const void*) = nullptr;
    void* (*classFromName)(void*, const char*, const char*) = nullptr;
    size_t (*imageClassCount)(const void*) = nullptr;
    void* (*imageClass)(const void*, size_t) = nullptr;
    const char* (*className)(void*) = nullptr;
    const char* (*classNamespace)(void*) = nullptr;
    const void* (*methodFromName)(void*, const char*, int) = nullptr;
    const void* (*classMethods)(void*, void**) = nullptr;
    const char* (*methodName)(const void*) = nullptr;
    uint32_t (*methodParamCount)(const void*) = nullptr;
    const void* (*methodParam)(const void*, uint32_t) = nullptr;
    char* (*typeName)(const void*) = nullptr;
    void* (*classFields)(void*, void**) = nullptr;
    const char* (*fieldName)(void*) = nullptr;
    size_t (*fieldOffset)(void*) = nullptr;
    const void* (*fieldType)(void*) = nullptr;
    void* (*classFieldFromName)(void*, const char*) = nullptr;
    void* (*stringNew)(const wchar_t*, int32_t) = nullptr;
    const wchar_t* (*stringChars)(void*) = nullptr;
    int32_t (*stringLength)(void*) = nullptr;
    void* (*invoke)(const void*, void*, void**, void**) = nullptr;
    void (*free)(void*) = nullptr;
};

bool install(const Api& api, const ark::fs::path& dataRoot, const ark::fs::path& modRoot,
             const ark::fs::path& statusDirectory);
void uninstall();
bool installed();
std::string control(const std::string& command);

} // namespace qaqr::text::hooks
