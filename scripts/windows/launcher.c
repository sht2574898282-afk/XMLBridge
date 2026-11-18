#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, LPWSTR arguments, int show) {
    (void)instance; (void)previous; (void)show;
    wchar_t module[32768];
    DWORD length = GetModuleFileNameW(NULL, module, 32768);
    if (!length || length >= 32768) return 1;
    wchar_t *slash = wcsrchr(module, L'\\');
    if (!slash) return 1;
    *slash = L'\0';
    const wchar_t *suffix = L"\\runtime\\pythonw.exe";
    size_t needed = wcslen(module) + wcslen(suffix) + 1;
    wchar_t *python = calloc(needed, sizeof(wchar_t));
    if (!python) return 1;
    wcscpy(python, module); wcscat(python, suffix);
    const wchar_t *entry = L"\" -I -B -m xmlbridge.windows_launcher ";
    size_t command_size = wcslen(python) + wcslen(entry) + wcslen(arguments) + 2;
    wchar_t *command = calloc(command_size, sizeof(wchar_t));
    if (!command) { free(python); return 1; }
    wcscpy(command, L"\""); wcscat(command, python); wcscat(command, entry); wcscat(command, arguments);
    STARTUPINFOW startup = {0}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {0};
    BOOL launched = CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &startup, &process);
    free(command); free(python);
    if (!launched) {
        MessageBoxW(NULL, L"无法启动内置运行时。请先完整解压 ZIP，然后双击 XMLBridge.exe；不要只复制 EXE。", L"XMLBridge", MB_ICONERROR);
        return 1;
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1; GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    return (int)code;
}
