"""Return temporary CPU inference allocations to the OS on glibc hosts."""
import ctypes
import gc
import sys


def release_cpu_memory():
    gc.collect()
    if not sys.platform.startswith('linux'):
        return
    try:
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        trim(0)
    except (AttributeError, OSError):
        pass  # Other C runtimes may not expose malloc_trim.
