from __future__ import annotations

import re

from hindsight.schema import Env, Frame

_FILE_LINE = re.compile(r'File "([^"]+)", line \d+, in \S+\n\s*(\S+?)\(')
_PKG = re.compile(r"([A-Za-z][A-Za-z0-9_.\-]+)\s*(?:==|\s)\s*(\d+\.\d+(?:\.\d+)?)")
_CUDA = re.compile(r"cuda[\s\-:]*?(\d+\.\d+)", re.IGNORECASE)
_OS = re.compile(r"(Ubuntu \d+\.\d+|Debian \d+|CentOS \d+|macOS \d+\.\d+|Windows \d+)")
_ARCH = re.compile(r"\b(x86_64|aarch64|arm64|amd64)\b")


def normalize_trace(raw: str, top_k: int = 20) -> list[Frame]:
    matches = _FILE_LINE.findall(raw)
    if not matches:
        return []
    # Deepest frame (last in a Python traceback) is most relevant -> position 0.
    ordered = list(reversed(matches))[:top_k]
    frames: list[Frame] = []
    for position, (path, symbol) in enumerate(ordered):
        module = re.split(r"[\\/]", path)[-1]
        frames.append(
            Frame(symbol=symbol, module=module, position=position, weight=1.0 / (position + 1))
        )
    return frames


def frame_tokens(frames: list[Frame]) -> str:
    return " ".join(f"{f.symbol}@{f.module}" for f in frames)


def parse_env(text: str, labels: list[str] | None = None) -> Env:
    blob = text + " " + " ".join(labels or [])
    packages: dict[str, str] = {}
    for name, ver in _PKG.findall(text):
        low = name.lower()
        if low in {"ubuntu", "debian", "centos", "cuda", "python", "macos", "windows"}:
            continue
        packages[name] = ver
    cuda = None
    m = _CUDA.search(blob)
    if m:
        cuda = m.group(1)
    os_m = _OS.search(blob)
    arch_m = _ARCH.search(blob)
    return Env(
        packages=packages,
        cuda=cuda,
        os=os_m.group(1) if os_m else None,
        arch=arch_m.group(1) if arch_m else None,
    )
