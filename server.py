#!/usr/bin/env python3
"""
Alger Music Mobile Host Server
Runs on this Mac to allow any phone (iPhone / Android) on the same Wi-Fi to:
1. Search songs across platforms (NetEase, KuGou, etc.)
2. Stream music in high quality / FLAC with lockscreen controls
3. Download FLAC / MP3 songs directly to the phone's storage
"""

import http.server
import json
import os
import socket
import socketserver
import time
import urllib.parse
import urllib.request
from typing import Dict, Any

PORT = int(os.environ.get("PORT", 8080))
API_BASE = "https://music-api.gdstudio.xyz/api.php"
MUSIC_DIR = os.path.expanduser("~/Music")

# Simple in-memory cache
url_cache: Dict[str, Any] = {}

def get_local_ip() -> str:
    """Get the local network IP of this Mac."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class MusicHostHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Concise logging
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]} {args[1]}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Root: Serve index.html
        if path == "/" or path == "/index.html":
            self.serve_index()
            return

        # 2. API: Search songs
        if path == "/api/search":
            self.handle_search(query)
            return

        # 3. API: Get song URL & info
        if path == "/api/url":
            self.handle_get_url(query)
            return

        # 4. API: Stream audio (Range-request enabled for iOS Safari)
        if path == "/api/stream":
            self.handle_stream(query, is_download=False)
            return

        # 5. API: Direct Download audio
        if path == "/api/download":
            self.handle_stream(query, is_download=True)
            return

        # 6. API: Get Lyric
        if path == "/api/lyric":
            self.handle_lyric(query)
            return

        # 7. API: Get Pic
        if path == "/api/pic":
            self.handle_pic(query)
            return

        # 8. API: Local music library
        if path == "/api/local":
            self.handle_local_list()
            return

        # 9. API: Local song stream / download
        if path == "/api/local/file":
            self.handle_local_file(query)
            return

        # Default: 404
        self.send_error(404, "Not Found")

    def serve_index(self):
        html_file = os.path.join(os.path.dirname(__file__), "index.html")
        if not os.path.exists(html_file):
            self.send_error(404, "index.html missing")
            return
        with open(html_file, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def handle_search(self, query):
        q = query.get("q", [""])[0]
        source = query.get("source", ["netease"])[0]
        page = query.get("page", ["1"])[0]

        if not q:
            self.send_json({"error": "Missing query", "data": []})
            return

        api_url = f"{API_BASE}?types=search&source={source}&name={urllib.parse.quote(q)}&count=25&pages={page}"
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
                self.send_json({"success": True, "data": data})
        except Exception as e:
            self.send_json({"success": False, "error": str(e), "data": []})

    def fetch_upstream_song_url(self, source: str, song_id: str) -> Dict[str, Any]:
        cache_key = f"{source}:{song_id}"
        cached = url_cache.get(cache_key)
        if cached and (time.time() - cached.get("time", 0)) < 900:
            return cached["data"]

        api_url = f"{API_BASE}?types=url&source={source}&id={song_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            url_cache[cache_key] = {"time": time.time(), "data": data}
            return data

    def handle_get_url(self, query):
        source = query.get("source", ["netease"])[0]
        song_id = query.get("id", [""])[0]
        if not song_id:
            self.send_json({"error": "Missing song id"})
            return
        try:
            data = self.fetch_upstream_song_url(source, song_id)
            self.send_json({"success": True, "data": data})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)})

    def handle_pic(self, query):
        source = query.get("source", ["netease"])[0]
        pic_id = query.get("id", [""])[0]
        if not pic_id:
            self.send_error(400, "Missing pic id")
            return
        try:
            api_url = f"{API_BASE}?types=pic&source={source}&id={pic_id}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
                pic_url = data.get("url", "")
                if pic_url:
                    self.send_response(302)
                    self.send_header("Location", pic_url)
                    self.end_headers()
                    return
        except Exception:
            pass
        self.send_error(404, "Pic not found")

    def handle_lyric(self, query):
        source = query.get("source", ["netease"])[0]
        lyric_id = query.get("id", [""])[0]
        if not lyric_id:
            self.send_json({"error": "Missing lyric id"})
            return
        try:
            api_url = f"{API_BASE}?types=lyric&source={source}&id={lyric_id}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
                self.send_json({"success": True, "data": data})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)})

    def handle_stream(self, query, is_download: bool = False):
        source = query.get("source", ["netease"])[0]
        song_id = query.get("id", [""])[0]
        name = query.get("name", ["Song"])[0]
        artist = query.get("artist", ["Artist"])[0]

        if not song_id:
            self.send_error(400, "Missing id")
            return

        try:
            song_info = self.fetch_upstream_song_url(source, song_id)
            stream_url = song_info.get("url")
            if not stream_url:
                self.send_error(404, "Song stream not found")
                return

            is_flac = ".flac" in stream_url.lower() or song_info.get("br", 0) >= 800
            ext = "flac" if is_flac else "mp3"
            content_type = "audio/flac" if is_flac else "audio/mpeg"

            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://music.163.com/"
            }

            range_header = self.headers.get("Range")
            if range_header and not is_download:
                headers["Range"] = range_header

            upstream_req = urllib.request.Request(stream_url, headers=headers)
            with urllib.request.urlopen(upstream_req, timeout=20) as resp:
                status_code = resp.status
                self.send_response(status_code)
                self.send_header("Content-Type", content_type)

                cl = resp.headers.get("Content-Length")
                if cl:
                    self.send_header("Content-Length", cl)

                cr = resp.headers.get("Content-Range")
                if cr:
                    self.send_header("Content-Range", cr)

                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")

                if is_download:
                    filename = f"{artist} - {name}.{ext}".replace('/', '_').replace('"', '')
                    quoted = urllib.parse.quote(filename)
                    self.send_header("Content-Disposition", f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quoted}')

                self.end_headers()

                # Stream content in 64KB blocks
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
        except Exception as e:
            print(f"Streaming error for {song_id}: {e}")
            try:
                self.send_error(500, f"Streaming error: {e}")
            except Exception:
                pass

    def handle_local_list(self):
        songs = []
        if os.path.exists(MUSIC_DIR):
            for root, _, files in os.walk(MUSIC_DIR):
                for f in files:
                    if f.lower().endswith(('.flac', '.mp3', '.m4a', '.wav')):
                        rel = os.path.relpath(os.path.join(root, f), MUSIC_DIR)
                        size = os.path.getsize(os.path.join(root, f))
                        ext = f.split('.')[-1].upper()
                        name = f.rsplit('.', 1)[0]
                        songs.append({
                            "name": name,
                            "path": rel,
                            "size": size,
                            "format": ext
                        })
        self.send_json({"success": True, "songs": songs})

    def handle_local_file(self, query):
        rel_path = query.get("file", [""])[0]
        is_dl = query.get("download", ["0"])[0] == "1"
        if not rel_path:
            self.send_error(400, "Missing file param")
            return
        full_path = os.path.abspath(os.path.join(MUSIC_DIR, rel_path))
        if not full_path.startswith(MUSIC_DIR) or not os.path.exists(full_path):
            self.send_error(404, "File not found")
            return

        size = os.path.getsize(full_path)
        ext = full_path.split('.')[-1].lower()
        content_type = "audio/flac" if ext == "flac" else "audio/mpeg"

        range_header = self.headers.get("Range")
        start = 0
        end = size - 1

        if range_header and not is_dl:
            # Parse Range: bytes=start-end
            parts = range_header.replace("bytes=", "").split("-")
            start = int(parts[0]) if parts[0] else 0
            if len(parts) > 1 and parts[1]:
                end = int(parts[1])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            length = (end - start) + 1
        else:
            self.send_response(200)
            length = size

        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")

        if is_dl:
            fname = os.path.basename(full_path)
            quoted = urllib.parse.quote(fname)
            self.send_header("Content-Disposition", f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quoted}')

        self.end_headers()

        with open(full_path, "rb") as f:
            f.seek(start)
            bytes_left = length
            while bytes_left > 0:
                chunk_size = min(64 * 1024, bytes_left)
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    bytes_left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break

    def send_json(self, data: Any):
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

def run():
    ip = get_local_ip()
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadedHTTPServer(server_address, MusicHostHandler)
    print("=" * 60)
    print("🎵 Alger Music Mobile Host is RUNNING!")
    print(f"👉 Local Access:   http://localhost:{PORT}")
    print(f"📱 Phone Access:   http://{ip}:{PORT}")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()

if __name__ == "__main__":
    run()
