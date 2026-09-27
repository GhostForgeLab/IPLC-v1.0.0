#!/usr/bin/env python3
"""Build a source-only IPLC Light release archive."""

import gzip
import io
import tarfile
from pathlib import Path


root = Path(__file__).resolve().parents[1]
output = root / "dist/iplc-light.tar.gz"
output.parent.mkdir(exist_ok=True)

with output.open("wb") as file:
    with gzip.GzipFile(filename="", mode="wb", mtime=0, fileobj=file) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            directory = tarfile.TarInfo("iplc-light")
            directory.mode = 0o755
            directory.mtime = 0
            archive.addfile(directory)
            for filename in ("README.md", "install.sh", "iplc.py"):
                contents = (root / filename).read_bytes()
                item = tarfile.TarInfo("iplc-light/" + filename)
                item.size = len(contents)
                item.mode = 0o755 if filename != "README.md" else 0o644
                item.mtime = 0
                archive.addfile(item, io.BytesIO(contents))

print(output)
