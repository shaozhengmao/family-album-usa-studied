#!/usr/bin/env python3
"""
支持 HTTP Range 的本地静态服务器。

为什么需要它：
    `python3 -m http.server` 对 mp3 请求一律返回 200 + 全文件，不认 Range，
    浏览器于是把音频的 seekable 判定成 [0,0] —— 视觉上能播，但「单句循环」
    「点某句从那里播」会全部失效（currentTime 赋值被静默丢弃）。
    GitHub Pages / nginx / CDN 都支持 Range，所以线上正常，只有本地调试会踩。

用法：
    python3 tools/dev_server.py [port]      # 默认 8080
"""

import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class _RangeFile:
    """把文件的 [start, end] 片段包装成 file-like，供 copyfile 读取。"""

    def __init__(self, path, start, end):
        self.f = open(path, "rb")
        self.f.seek(start)
        self.remain = end - start + 1

    def read(self, n=-1):
        if self.remain <= 0:
            return b""
        if n is None or n < 0 or n > self.remain:
            n = self.remain
        data = self.f.read(n)
        self.remain -= len(data)
        return data

    def close(self):
        self.f.close()


class RangeHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if rng and os.path.isfile(path):
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m and (m.group(1) or m.group(2)):
                size = os.path.getsize(path)
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                if 0 <= start <= end:
                    self.send_response(206)
                    self.send_header("Content-Type", self.guess_type(path))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(end - start + 1))
                    self.end_headers()
                    return _RangeFile(path, start, end)
        return super().send_head()

    def log_message(self, fmt, *args):
        if "/audio-acts/" in (self.path or ""):
            return          # 音频请求太多，静音
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    srv = ThreadingHTTPServer(("127.0.0.1", port), RangeHandler)
    print(f"serving on http://127.0.0.1:{port}/  (Range enabled)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
