"""Configure stdout/stderr for UTF-8 (fixes Windows charmap errors with emoji logs)."""

import os
import sys


def configure_stdio_utf8() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def safe_print(*args, **kwargs) -> None:
    """Print that never raises UnicodeEncodeError on Windows."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        end = kwargs.get("end", "\n")
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
            sys.stdout.buffer.write(str(end).encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        else:
            print(text.encode("ascii", errors="replace").decode("ascii"), **kwargs)
