#!/usr/bin/env python3
"""
Alger Music Mobile Host Server
Optimized for both Local Mac and Cloud (Render.com / AWS / VPS) deployment.
Features:
- Multi-engine Search (GDStudio + Direct NetEase CloudSearch + Kuwo fallback)
- High-res Audio Streaming with Range header support for iOS Safari
- Direct Download with proper attachment headers
- Synced Lyrics & Album Artwork
"""

import ast
import http.server
import json
import os
import socket
import socketserver
import time
import urllib.parse
import urllib.request
from typing import Dict, Any, List

PORT = int(os.environ.get("PORT", 8080))
API_BASE = "https://music-api.gdstudio.xyz/api.php"
MUSIC_DIR = os.path.expanduser("~/Music")

# Cache for resolved song URLs (15 mins TTL)
url_cache: Dict[str, Any] = {}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://music.gdstudio.xyz/"
}

def get_local_ip() -> str:
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
        path_str = self.path
        try:
            path_str = path_str.encode("iso-8859-1").decode("utf-8")
        except Exception:
            pass
        parsed = urllib.parse.urlparse(path_str)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self.serve_index()
            return

        if path == "/api/search":
            self.handle_search(query)
            return

        if path == "/api/url":
            self.handle_get_url(query)
            return

        if path == "/api/stream":
            self.handle_stream(query, is_download=False)
            return

        if path == "/api/download":
            self.handle_stream(query, is_download=True)
            return

        if path == "/api/lyric":
            self.handle_lyric(query)
            return

        if path == "/api/pic":
            self.handle_pic(query)
            return

        if path == "/api/local":
            self.handle_local_list()
            return

        if path == "/api/local/file":
            self.handle_local_file(query)
            return

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
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        try:
            count = max(10, min(100, int(query.get("count", ["50"])[0])))
        except ValueError:
            count = 50

        if not q:
            self.send_json({"success": True, "data": []})
            return

        # 1. Try GDStudio first
        songs = self.search_gdstudio(q, source, page, min(count, 50))
        if songs:
            self.send_json({"success": True, "data": songs, "engine": "gdstudio", "page": page, "count": len(songs)})
            return

        # 2. Fallback to Direct NetEase CloudSearch (Never blocked by Cloudflare!)
        print(f"GDStudio failed or blocked for '{q}'. Falling back to NetEase direct search...")
        songs = self.search_netease_direct(q, page, count)
        if songs:
            self.send_json({"success": True, "data": songs, "engine": "netease_direct", "page": page, "count": len(songs)})
            return

        # 3. Fallback to Kuwo direct search
        print(f"Falling back to Kuwo search for '{q}'...")
        songs = self.search_kuwo_direct(q, page, count)
        self.send_json({"success": True, "data": songs, "engine": "kuwo_direct", "page": page, "count": len(songs)})

    def search_gdstudio(self, q: str, source: str, page: int = 1, count: int = 50) -> List[Dict[str, Any]]:
        api_url = f"{API_BASE}?types=search&source={source}&name={urllib.parse.quote(q)}&count={count}&pages={page}"
        try:
            req = urllib.request.Request(api_url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=6) as r:
                raw = r.read().decode("utf-8")
                data = json.loads(raw)
                if isinstance(data, list) and len(data) > 0:
                    return data
        except Exception as e:
            print(f"GDStudio search error: {e}")
        return []

    def search_netease_direct(self, q: str, page: int = 1, count: int = 50) -> List[Dict[str, Any]]:
        offset = max(0, (page - 1) * count)
        url = "https://music.163.com/api/cloudsearch/pc"
        data = urllib.parse.urlencode({
            "s": q,
            "type": 1,
            "offset": offset,
            "limit": count
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": BROWSER_HEADERS["User-Agent"],
            "Referer": "https://music.163.com/",
            "Cookie": "os=pc"
        })
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                res = json.loads(r.read().decode("utf-8"))
                songs = res.get("result", {}).get("songs", [])
                formatted = []
                for s in songs:
                    pic_url = s.get("al", {}).get("picUrl", "")
                    formatted.append({
                        "id": str(s["id"]),
                        "name": s.get("name", ""),
                        "artist": [a.get("name", "") for a in s.get("ar", [])],
                        "album": s.get("al", {}).get("name", ""),
                        "pic_id": pic_url,
                        "pic_url": pic_url,
                        "url_id": str(s["id"]),
                        "lyric_id": str(s["id"]),
                        "source": "netease",
                        "format": "FLAC / 320K"
                    })
                return formatted
        except Exception as e:
            print(f"NetEase direct search error: {e}")
        return []

    def search_kuwo_direct(self, q: str, page: int = 1, count: int = 50) -> List[Dict[str, Any]]:
        pn = max(0, page - 1)
        url = f"http://search.kuwo.cn/r.s?client=kt&all={urllib.parse.quote(q)}&rformat=json&encoding=utf8&rn={count}&pn={pn}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=6) as r:
                raw = r.read().decode("utf-8", errors="ignore")
                data = ast.literal_eval(raw)
                songs = data.get("abslist", [])
                formatted = []
                for s in songs:
                    formatted.append({
                        "id": s.get("MUSICRID", "").replace("MUSIC_", ""),
                        "name": s.get("SONGNAME", "").replace("&nbsp;", " "),
                        "artist": [s.get("ARTIST", "").replace("&nbsp;", " ")],
                        "album": s.get("ALBUM", "").replace("&nbsp;", " "),
                        "pic_id": "",
                        "url_id": s.get("MUSICRID", ""),
                        "lyric_id": "",
                        "source": "kuwo",
                        "format": "HQ MP3"
                    })
                return formatted
        except Exception as e:
            print(f"Kuwo search error: {e}")
        return []

    def fetch_audio_url(self, source: str, song_id: str, name: str = "", artist: str = "") -> Dict[str, Any]:
        cache_key = f"{source}:{song_id}"
        cached = url_cache.get(cache_key)
        if cached and (time.time() - cached.get("time", 0)) < 900:
            return cached["data"]

        # 1. Try GDStudio
        try:
            api_url = f"{API_BASE}?types=url&source={source}&id={song_id}"
            req = urllib.request.Request(api_url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("url"):
                    url_cache[cache_key] = {"time": time.time(), "data": data}
                    return data
        except Exception as e:
            print(f"GDStudio url error for {song_id}: {e}")

        # 2. Fallback to Kuwo music matcher
        if name:
            kuwo_url = self.match_kuwo_url(name, artist)
            if kuwo_url:
                data = {"url": kuwo_url, "br": 320, "size": 0, "from": "kuwo"}
                url_cache[cache_key] = {"time": time.time(), "data": data}
                return data

        # 3. Fallback to NetEase outer url
        fallback_url = f"http://music.163.com/song/media/outer/url?id={song_id}.mp3"
        return {"url": fallback_url, "br": 192, "size": 0, "from": "netease_outer"}

    def match_kuwo_url(self, name: str, artist: str) -> str:
        query = f"{name} {artist}".strip()
        url = f"http://search.kuwo.cn/r.s?client=kt&all={urllib.parse.quote(query)}&rformat=json&encoding=utf8&rn=1&pn=0"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read().decode("utf-8", errors="ignore")
                data = ast.literal_eval(raw)
                songs = data.get("abslist", [])
                if songs and songs[0].get("MUSICRID"):
                    mid = songs[0]["MUSICRID"]
                    u_api = f"http://antiserver.kuwo.cn/anti.s?type=convert_url&rid={mid}&format=mp3&response=url"
                    with urllib.request.urlopen(u_api, timeout=5) as u_r:
                        res = u_r.read().decode("utf-8").strip()
                        if res.startswith("http"):
                            return res
        except Exception as e:
            print(f"Kuwo audio match error: {e}")
        return ""

    def handle_get_url(self, query):
        source = query.get("source", ["netease"])[0]
        song_id = query.get("id", [""])[0]
        name = query.get("name", [""])[0]
        artist = query.get("artist", [""])[0]
        if not song_id:
            self.send_json({"error": "Missing song id"})
            return
        data = self.fetch_audio_url(source, song_id, name, artist)
        self.send_json({"success": True, "data": data})

    def handle_pic(self, query):
        source = query.get("source", ["netease"])[0]
        pic_id = query.get("id", [""])[0]
        if not pic_id:
            self.send_error(400, "Missing pic id")
            return

        # If pic_id is already a full URL, redirect directly
        if pic_id.startswith("http"):
            self.send_response(302)
            self.send_header("Location", pic_id)
            self.end_headers()
            return

        try:
            api_url = f"{API_BASE}?types=pic&source={source}&id={pic_id}"
            req = urllib.request.Request(api_url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=6) as r:
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

    def fetch_lyric_data(self, source: str, lyric_id: str) -> Dict[str, Any]:
        # Try GDStudio
        try:
            api_url = f"{API_BASE}?types=lyric&source={source}&id={lyric_id}"
            req = urllib.request.Request(api_url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode())
                if data.get("lyric"):
                    return data
        except Exception:
            pass

        # Fallback to NetEase official direct lyrics
        try:
            api_url = f"https://music.163.com/api/song/lyric?id={lyric_id}&lv=1&kv=1&tv=-1"
            req = urllib.request.Request(api_url, headers={"Referer": "https://music.163.com/"})
            with urllib.request.urlopen(req, timeout=6) as r:
                res = json.loads(r.read().decode())
                lrc = res.get("lrc", {}).get("lyric", "")
                tlyric = res.get("tlyric", {}).get("lyric", "")
                return {"lyric": lrc, "tlyric": tlyric}
        except Exception as e:
            print(f"Fetch lyric error for {lyric_id}: {e}")
        return {"lyric": "", "tlyric": ""}

    def handle_lyric(self, query):
        source = query.get("source", ["netease"])[0]
        lyric_id = query.get("id", [""])[0]
        if not lyric_id:
            self.send_json({"error": "Missing lyric id"})
            return

        data = self.fetch_lyric_data(source, lyric_id)
        if data.get("lyric"):
            self.send_json({"success": True, "data": data})
        else:
            self.send_json({"success": False, "error": "Lyric not found"})

    def handle_stream(self, query, is_download: bool = False):
        source = query.get("source", ["netease"])[0]
        song_id = query.get("id", [""])[0]
        name = query.get("name", ["Song"])[0]
        artist = query.get("artist", ["Artist"])[0]
        fmt = query.get("format", ["flac"])[0].lower()

        if not song_id:
            self.send_error(400, "Missing id")
            return

        # Handle lyric download
        if is_download and fmt == "lrc":
            lyric_id = query.get("lyric_id", [song_id])[0]
            lyric_data = self.fetch_lyric_data(source, lyric_id)
            lyric_text = lyric_data.get("lyric", "")
            if not lyric_text:
                lyric_text = f"[00:00.00]{name} - {artist}\n[00:05.00]Instrumental / No lyrics available\n"
            fname = f"{artist} - {name}.lrc".replace('/', '_').replace('"', '')
            quoted = urllib.parse.quote(fname)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quoted}')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(lyric_text.encode("utf-8"))
            return

        try:
            # Format selection logic
            if is_download and fmt in ("mp3", "320", "128"):
                # Try Kuwo MP3 or NetEase outer MP3
                stream_url = self.match_kuwo_url(name, artist)
                if not stream_url:
                    song_info = self.fetch_audio_url(source, song_id, name, artist)
                    stream_url = song_info.get("url")
                ext = "mp3"
                content_type = "audio/mpeg"
                bitrate_tag = " (320k)" if fmt in ("mp3", "320") else " (128k)"
            else:
                # Default / FLAC lossless
                song_info = self.fetch_audio_url(source, song_id, name, artist)
                stream_url = song_info.get("url")
                is_flac = ".flac" in (stream_url or "").lower() or song_info.get("br", 0) >= 800
                ext = "flac" if is_flac else "mp3"
                content_type = "audio/flac" if is_flac else "audio/mpeg"
                bitrate_tag = ""

            if not stream_url:
                self.send_error(404, "Song stream not found")
                return

            headers = {
                "User-Agent": BROWSER_HEADERS["User-Agent"],
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
                    filename = f"{artist} - {name}{bitrate_tag}.{ext}".replace('/', '_').replace('"', '')
                    quoted = urllib.parse.quote(filename)
                    self.send_header("Content-Disposition", f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quoted}')

                self.end_headers()

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
