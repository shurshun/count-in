"""Copies one file out of a release zip on GitHub without downloading the whole archive.

Usage: python3 fetch_from_release.py <zip url> <name suffix inside the zip> <output path>
zipfile reads the archive through HTTP range requests, so only the central directory and the file travel.
"""

import shutil
import sys
import urllib.request
import zipfile


class RangeFile:
    def __init__(self, url):
        self.url = urllib.request.urlopen(urllib.request.Request(url, method="HEAD")).url  # follow the CDN redirect
        self.size = int(urllib.request.urlopen(urllib.request.Request(self.url, method="HEAD")).headers["Content-Length"])
        self.pos = 0

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def read(self, n=-1):
        end = self.size if n < 0 else min(self.pos + n, self.size)
        if end <= self.pos:
            return b""
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end - 1}"})
        data = urllib.request.urlopen(req).read()
        self.pos += len(data)
        return data


url, suffix, out = sys.argv[1:]
with zipfile.ZipFile(RangeFile(url)) as z:
    name = next(n for n in z.namelist() if n.endswith(suffix))
    with z.open(name) as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 22)
