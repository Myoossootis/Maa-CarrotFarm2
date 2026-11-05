"""Read-only Windows process bridge. JSON in/out; never writes target memory."""
import ctypes as c
import ctypes.wintypes as w
import json
import re
import struct
import sys
import time

k = c.WinDLL('kernel32', use_last_error=True)
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
k.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]


class MBI(c.Structure):
    _fields_ = [('BaseAddress', c.c_void_p), ('AllocationBase', c.c_void_p),
                ('AllocationProtect', w.DWORD), ('PartitionId', w.WORD),
                ('RegionSize', c.c_size_t), ('State', w.DWORD),
                ('Protect', w.DWORD), ('Type', w.DWORD)]


class PROCESSENTRY32(c.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', c.c_size_t), ('th32ModuleID', w.DWORD),
                ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]


k.VirtualQueryEx.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(MBI), c.c_size_t]
k.VirtualQueryEx.restype = c.c_size_t
k.CreateToolhelp32Snapshot.restype = w.HANDLE
k.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(PROCESSENTRY32)]
k.Process32NextW.argtypes = k.Process32FirstW.argtypes
k.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4


class PROCESS_MEMORY_COUNTERS(c.Structure):
    _fields_ = [('cb', w.DWORD), ('PageFaultCount', w.DWORD)] + [
        (name, c.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]


psapi = c.WinDLL('psapi')
psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(PROCESS_MEMORY_COUNTERS), w.DWORD]


def processes():
    snapshot = k.CreateToolhelp32Snapshot(2, 0)
    entry = PROCESSENTRY32()
    entry.dwSize = c.sizeof(entry)
    result = []
    ok = k.Process32FirstW(snapshot, c.byref(entry))
    while ok:
        if entry.szExeFile.lower() == 'wechatappex.exe':
            handle = k.OpenProcess(0x410, False, entry.th32ProcessID)
            times = [w.FILETIME() for _ in range(4)]
            born = None
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = c.sizeof(counters)
            if handle:
                if k.GetProcessTimes(handle, *[c.byref(t) for t in times]):
                    born = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
                psapi.GetProcessMemoryInfo(handle, c.byref(counters), c.sizeof(counters))
                k.CloseHandle(handle)
            result.append(dict(pid=entry.th32ProcessID, parent=entry.th32ParentProcessID,
                               born=str(born), workingSet=counters.WorkingSetSize))
        ok = k.Process32NextW(snapshot, c.byref(entry))
    k.CloseHandle(snapshot)
    return result


class Memory:
    def __init__(self, pid):
        self.handle = k.OpenProcess(0x410, False, pid)  # QUERY_INFORMATION | VM_READ only
        if not self.handle:
            raise OSError('Cannot open process for reading: ' + str(pid))
        self.pages = {}

    def close(self):
        k.CloseHandle(self.handle)

    def read(self, address, size):
        buffer = c.create_string_buffer(size)
        got = c.c_size_t()
        k.ReadProcessMemory(self.handle, address, buffer, size, c.byref(got))
        return buffer.raw[:got.value]

    def cached(self, address, size):
        out = bytearray()
        while size:
            page, offset = address & ~4095, address & 4095
            if page not in self.pages:
                self.pages[page] = self.read(page, 4096)
            count = min(size, 4096 - offset)
            part = self.pages[page][offset:offset + count]
            if len(part) != count:
                raise ValueError('Unreadable page')
            out += part
            address += count
            size -= count
        return bytes(out)

    def chunks(self, private=False):
        address = 0
        while address < 0x7fffffffffff:
            m = MBI()
            if not k.VirtualQueryEx(self.handle, address, c.byref(m), c.sizeof(m)):
                break
            base, size = m.BaseAddress or 0, m.RegionSize
            if (m.State == 0x1000 and m.Type in ([0x20000] if private else [0x20000, 0x40000])
                    and m.Protect & 255 in [2, 4, 8, 0x40, 0x80] and not m.Protect & 0x100):
                # Overlap protects records and AES schedules crossing chunk boundaries.
                for offset in range(0, size, 4 * 1024 * 1024):
                    start = max(0, offset - 256)
                    yield base + start, self.read(base + start, min(4 * 1024 * 1024 + offset - start, size - start))
            if base + size <= address:
                break
            address = base + size

    def string(self, cage, pointer, depth=0):
        if not pointer & 1 or depth > 256:
            raise ValueError('Not a string pointer')
        address = cage + pointer - 1
        header = self.cached(address, 24)
        map_ptr = struct.unpack_from('<I', header)[0]
        typ = struct.unpack_from('<H', self.cached(cage + map_ptr - 1, 16), 8)[0]
        length = struct.unpack_from('<I', header, 8)[0]
        if typ >= 128 or length > 100000:
            raise ValueError('Unsupported string layout')
        rep = typ & 7
        if rep == 0:
            data = self.cached(address + 12, length * (1 if typ & 8 else 2))
            return data.decode('latin1' if typ & 8 else 'utf-16-le')
        a = struct.unpack_from('<I', header, 12)[0]
        if rep == 5:
            return self.string(cage, a, depth + 1)
        if rep == 1:
            b = struct.unpack_from('<I', header, 16)[0]
            return self.string(cage, a, depth + 1) + self.string(cage, b, depth + 1)
        if rep == 3:
            offset = struct.unpack_from('<i', header, 16)[0] >> 1
            return self.string(cage, a, depth + 1)[offset:offset + length]
        raise ValueError('External string; use raw extra counterpart')

    def record(self, address):
        raw = self.cached(address, 16)
        uid, item, _, pointer = struct.unpack('<4I', raw)
        if uid & 1 or item not in (3931 * 2, 3629 * 2, 3921 * 2, 3972 * 2) or not 1000 < uid >> 1 < 1073741824:
            raise ValueError('Not a game record')
        extra = self.string(address & ~0xffffffff, pointer)
        if (item == 7862 and not re.search(r'(?:^|;)idx,\d+(?:;|$)', extra)) or (item == 7258 and 'plrd,' not in extra):
            raise ValueError('Unexpected extra structure')
        if item == 7842 and not re.search(r'(?:^|;)season,\d+(?:;|$)', extra):
            raise ValueError('Missing match season')
        if item == 7944 and not all(re.search(r'(?:^|;)'+key+r',\d+(?:;|$)', extra) for key in ('r_id','r_c','exp')):
            raise ValueError('Invalid contribution record')
        return dict(address=hex(address), uid=uid >> 1, itemId=item >> 1, extra=extra)


def run(request):
    if request['op'] == 'processes':
        return processes()
    memory = Memory(request['pid'])
    try:
        if request['op'] == 'keys':
            if request.get('addresses'):
                return [dict(address=a, key=memory.read(int(a, 16), 16).hex()) for a in request['addresses']]
            result = {}
            for base, data in memory.chunks(private=True):
                for match in re.finditer(b'\x0a\0\0\0', data):
                    for delta in (240, 176):
                        p = match.start() - delta
                        if p < 0 or p + 32 > len(data):
                            continue
                        words = struct.unpack_from('<8I', data, p)
                        if (words[5] == words[1] ^ words[4] and words[6] == words[2] ^ words[5]
                                and words[7] == words[3] ^ words[6] and len(set(words)) > 5):
                            result[hex(base + p)] = data[p:p + 16].hex()
            return [dict(address=a, key=v) for a, v in result.items()]
        if request['op'] == 'records':
            records = {}
            for base, data in memory.chunks():
                for match in re.finditer(b'\xb6\x1e\0\0|\x5a\x1c\0\0|\xa2\x1e\0\0|\x08\x1f\0\0', data):
                    p = match.start()
                    if p < 4 or p + 12 > len(data):
                        continue
                    address = base + p - 4
                    try:
                        record = memory.record(address)
                        records[record['address']] = record
                    except (ValueError, struct.error, UnicodeError):
                        pass
            return dict(records=list(records.values()), hintUsed=False)
        raise ValueError('Unknown read-only operation')
    finally:
        memory.close()


if __name__ == '__main__':
    started = time.perf_counter()
    try:
        result = run(json.load(sys.stdin))
        print(json.dumps(dict(result=result, seconds=time.perf_counter() - started), ensure_ascii=True))
    except Exception as exc:
        print(json.dumps(dict(error=str(exc))))
        sys.exit(1)
