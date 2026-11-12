"""Bounded idle-only native console QA using documented Windows ConPTY APIs.

References: Microsoft Learn, creating-a-pseudoconsole-session and closepseudoconsole.
No third-party terminal package, interactive RDP session or customer process is used.
"""
import ctypes as C
from ctypes import wintypes as W
import subprocess
import time


class Coord(C.Structure):
    _fields_ = [("X", C.c_short), ("Y", C.c_short)]


class StartupInfo(C.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
                ("lpReserved2", C.POINTER(W.BYTE)), ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]


class StartupInfoEx(C.Structure):
    _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", C.c_void_p)]


class ProcessInfo(C.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


def capture_idle(binary, directory, environment):
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreatePipe.argtypes = [C.POINTER(W.HANDLE), C.POINTER(W.HANDLE), C.c_void_p, W.DWORD]
    kernel.CreatePipe.restype = W.BOOL
    kernel.CreatePseudoConsole.argtypes = [Coord, W.HANDLE, W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    kernel.CreatePseudoConsole.restype = C.c_long
    kernel.InitializeProcThreadAttributeList.argtypes = [C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)]
    kernel.InitializeProcThreadAttributeList.restype = W.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p]
    kernel.UpdateProcThreadAttribute.restype = W.BOOL
    kernel.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR, C.c_void_p, C.POINTER(ProcessInfo)]
    kernel.CreateProcessW.restype = W.BOOL
    kernel.PeekNamedPipe.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.c_void_p, C.POINTER(W.DWORD), C.c_void_p]
    kernel.PeekNamedPipe.restype = W.BOOL
    kernel.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
    kernel.ReadFile.restype = W.BOOL
    kernel.WriteFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
    kernel.WriteFile.restype = W.BOOL
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.WaitForSingleObject.restype = W.DWORD
    kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
    kernel.TerminateProcess.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.ClosePseudoConsole.argtypes = [W.HANDLE]
    kernel.DeleteProcThreadAttributeList.argtypes = [C.c_void_p]

    def checked(success, context):
        if not success:
            raise OSError(C.get_last_error(), context)

    input_read, input_write, output_read, output_write = (W.HANDLE() for _ in range(4))
    console = W.HANDLE()
    process = ProcessInfo()
    attributes = None
    transcript = bytearray()

    def send(data):
        written = W.DWORD()
        data_buffer = C.create_string_buffer(data)
        checked(kernel.WriteFile(input_write, data_buffer, len(data), C.byref(written), None), "ConPTY input write")

    try:
        checked(kernel.CreatePipe(C.byref(input_read), C.byref(input_write), None, 0), "ConPTY input pipe")
        checked(kernel.CreatePipe(C.byref(output_read), C.byref(output_write), None, 0), "ConPTY output pipe")
        result = kernel.CreatePseudoConsole(Coord(115, 45), input_read, output_write, 0, C.byref(console))
        if result < 0:
            raise OSError(result, "CreatePseudoConsole")
        size = C.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
        assert size.value > 0
        attributes = C.create_string_buffer(size.value)
        checked(kernel.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size)), "ConPTY startup attributes")
        checked(kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020016, console, C.sizeof(console), None, None), "ConPTY startup console")
        startup = StartupInfoEx()
        startup.StartupInfo.cb = C.sizeof(startup)
        startup.lpAttributeList = C.cast(attributes, C.c_void_p)
        command = C.create_unicode_buffer(subprocess.list2cmdline([str(binary), "--no-alt-screen"]))
        block = C.create_unicode_buffer("\0".join(k + "=" + v for k, v in sorted(environment.items(), key=lambda pair: pair[0].casefold())) + "\0\0")
        checked(kernel.CreateProcessW(None, command, None, None, False, 0x00080000 | 0x00000400, block, str(directory), C.byref(startup), C.byref(process)), "ConPTY test process")
        # These pipe ends now belong to the pseudoconsole; don't keep extra references alive.
        kernel.CloseHandle(input_read)
        input_read = W.HANDLE()
        kernel.CloseHandle(output_write)
        output_write = W.HANDLE()
        start = time.monotonic()
        while time.monotonic() - start < 40:
            available = W.DWORD()
            if not kernel.PeekNamedPipe(output_read, None, 0, None, C.byref(available), None):
                break
            if available.value:
                buffer = C.create_string_buffer(min(available.value, 65536))
                count = W.DWORD()
                checked(kernel.ReadFile(output_read, buffer, len(buffer), C.byref(count), None), "ConPTY output read")
                chunk = buffer.raw[:count.value]
                transcript.extend(chunk)
                if b"\x1b[6n" in chunk:
                    send(b"\x1b[1;1R")
                if b"\x1b]11;?" in chunk:
                    send(b"\x1b]11;rgb:1010/1010/1010\x1b\\")
                if b"Ask CoderX" in transcript and b"MagnetAPI.org Codex CLI" in transcript:
                    break
            elif kernel.WaitForSingleObject(process.hProcess, 0) == 0:
                break
            else:
                time.sleep(.05)
        return transcript.decode(errors="replace")
    finally:
        # No prompt was submitted in this fresh disposable profile: only this known idle test PID is stopped.
        if process.hProcess:
            if kernel.WaitForSingleObject(process.hProcess, 0) != 0:
                try:
                    send(b"\x03")
                except OSError:
                    pass
                if kernel.WaitForSingleObject(process.hProcess, 5000) != 0:
                    kernel.TerminateProcess(process.hProcess, 0)
                    kernel.WaitForSingleObject(process.hProcess, 5000)
            kernel.CloseHandle(process.hThread)
            kernel.CloseHandle(process.hProcess)
        for handle in [input_read, input_write, output_read, output_write]:
            if handle:
                kernel.CloseHandle(handle)
        # Close the output pipe first, as required to avoid shutdown deadlocks on older Windows.
        if console:
            kernel.ClosePseudoConsole(console)
        if attributes is not None:
            kernel.DeleteProcThreadAttributeList(attributes)
