#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
酷狗概念版客户端 — 从 MusicBot-Go (concept_client.go) 移植
功能: 扫码登录 / 搜索 / 获取下载链接 / 下载文件

Vendored from lisalee23042605/kgmusicbot (kugou_client.py, 2026-11-22),
verbatim except for the bot adaptations noted below. The bot only
uses the QR login (create_qrcode / check_qrcode) and device
registration; search/download stay vendored but unused.

BOT ADAPTATIONS (do not lose on re-vendor):
- Import: ``from .kugou_crypto`` -> ``from .kugou_concept_crypto``.
- Session persistence is DISABLED: the upstream ``save_session`` /
  ``load_session`` wrote the whole state (INCLUDING token + userid)
  to ``kugou_session.json`` next to this file. The bot holds login
  state in memory per QR attempt and persists ONLY the device dict
  (dfid/guid/mid/dev/mac) via the wrapper (app/kugou_concept.py,
  data/kugou_concept_device.json, 0600). ``save_session`` now only
  normalises the in-memory device + cookie; ``load_session`` returns
  an empty state. Never re-enable the disk write here: a token must
  never be persisted to disk by the bot.
"""
import base64
import io
import json
import os
import time
import urllib.request
import urllib.parse

from .kugou_concept_crypto import (
    CONCEPT_APPID, CONCEPT_CLIENT_VER, CONCEPT_SRC_APPID, CONCEPT_QR_APPID,
    CONCEPT_UA, CONCEPT_QR_TEXT_URL, CONCEPT_ROUTE_USER, CONCEPT_ROUTE_TRACK,
    CONCEPT_KGT_HASH, CONCEPT_KGRF, CONCEPT_PAGE_ID, CONCEPT_PPAGE_ID,
    CONCEPT_SIGN_SECRET, CONCEPT_PLAY_SECRET, CONCEPT_REFRESH_KEY, CONCEPT_REFRESH_IV,
    CONCEPT_T2_KEY, CONCEPT_T2_IV, CONCEPT_T1_KEY, CONCEPT_T1_IV, CONCEPT_T2_FIXED_PART,
    DEFAULT_CONCEPT_VIP_TYPE,
    CONCEPT_LOGIN_BASE_URL, CONCEPT_LOGIN_TOKEN_URL, CONCEPT_GATEWAY_BASE_URL,
    CONCEPT_USER_SERVICE_BASE_URL, CONCEPT_VIP_BASE_URL,
    KUGOU_GATEWAY_SIGN_KEY, KUGOU_PLAY_SIGN_KEY,
    concept_md5, concept_signature_web, concept_signature_android, concept_sign_key,
    aes_cbc_encrypt_hex, aes_cbc_encrypt_string_hex, aes_cbc_decrypt_hex,
    aes_cbc_encrypt_hex_auto, playlist_aes_encrypt, playlist_aes_decrypt_from_binary,
    rsa_pkcs1v15_encrypt_hex, rsa_raw_encrypt_hex,
    random_guid, random_alpha_num, random_alpha, calculate_mid,
    normalize_device, build_concept_cookie, default_query,
)

# BOT ADAPTATION: the upstream SESSION_FILE (kugou_session.json) is
# NOT used — see the module docstring. Login state lives in memory;
# only the device dict is persisted, by the wrapper.
SESSION_FILE = None


# ============ 搜索 (不需要登录) ============
def search(keyword: str, limit: int = 8) -> list:
    """搜索歌曲 — 来自 client.go searchSongs()"""
    keyword = keyword.strip()
    if not keyword:
        return []
    params = urllib.parse.urlencode({
        "keyword": keyword,
        "platform": "WebFilter",
        "format": "json",
        "page": "1",
        "pagesize": str(limit),
    })
    url = f"http://songsearch.kugou.com/song_search_v2?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    results = []
    for item in data.get("data", {}).get("lists", []):
        # 清理 <em> 标签
        def clean(s):
            return (s or "").replace("<em>", "").replace("</em>", "").strip()

        file_hash = (item.get("FileHash") or "").strip().lower()
        if not file_hash:
            continue

        results.append({
            "name": clean(item.get("SongName")),
            "artist": clean(item.get("SingerName")),
            "album": clean(item.get("AlbumName")),
            "album_id": (item.get("AlbumID") or "").strip(),
            "hash": file_hash,
            "hq_hash": (item.get("HQFileHash") or "").strip().lower(),
            "sq_hash": (item.get("SQFileHash") or "").strip().lower(),
            "res_hash": (item.get("ResFileHash") or "").strip().lower(),
            "duration": item.get("Duration", 0),
            "audio_id": str(item.get("AudioID", "")),
            "mix_song_id": str(item.get("MixSongID", "")),
        })
    return results[:limit]


# ============ 获取歌曲详情 (gateway) ============
def fetch_gateway_track_info(hash_val: str) -> dict:
    """获取歌曲信息 — 来自 client.go fetchGatewayTrackInfo()"""
    # 网关要求大写 hash + album_audio_id 字段
    body_map = {
        "area_code": "1",
        "show_privilege": "1",
        "show_album_info": "1",
        "is_publish": "",
        "appid": 1005,
        "clientver": 11451,
        "mid": "211008",
        "dfid": "-",
        "clienttime": int(time.time()),
        "key": KUGOU_GATEWAY_SIGN_KEY,
        "data": [{"hash": hash_val.strip().upper(), "album_audio_id": "0"}],
    }
    body = json.dumps(body_map, separators=(",", ":")).encode()
    url = "https://gateway.kugou.com/v3/album_audio/audio"
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "KG-THash": "13a3164",
        "KG-RC": "1",
        "KG-Fake": "0",
        "KG-RF": "00869891",
        "User-Agent": "Android712-AndroidPhone-11451-376-0-FeeCacheUpdate-wifi",
        "x-router": "kmr.service.kugou.com",
    })
    # API 有时返回空，重试最多 3 次
    item = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            d = data.get("data", [])
            if d and isinstance(d[0], list) and d[0] and isinstance(d[0][0], dict) and d[0][0]:
                item = d[0][0]
                break
            # 有时返回 [{...}] 而不是 [[{...}]]
            if d and isinstance(d[0], dict) and d[0]:
                item = d[0]
                break
        except Exception:
            pass
        time.sleep(0.5)
        # 重建请求（clienttime 需要更新）
        body_map["clienttime"] = int(time.time())
        body = json.dumps(body_map, separators=(",", ":")).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "KG-THash": "13a3164",
            "KG-RC": "1",
            "KG-Fake": "0",
            "KG-RF": "00869891",
            "User-Agent": "Android712-AndroidPhone-11451-376-0-FeeCacheUpdate-wifi",
            "x-router": "kmr.service.kugou.com",
        })

    if not item:
        return {}

    audio = item.get("audio_info", {})
    album = item.get("album_info", {})

    return {
        "hash": (audio.get("hash") or audio.get("hash_128") or hash_val).strip().lower(),
        "name": (item.get("ori_audio_name") or "").strip(),
        "artist": (item.get("author_name") or "").strip(),
        "album": (album.get("album_name") or "").strip(),
        "album_id": (album.get("album_id") or "").strip(),
        "album_audio_id": (item.get("album_audio_id") or "").strip(),
        "audio_id": str(audio.get("audio_id", "")),
        "file_hash": (audio.get("hash_128") or "").strip().lower(),
        "hq_hash": (audio.get("hash_320") or "").strip().lower(),
        "sq_hash": (audio.get("hash_flac") or "").strip().lower(),
        "res_hash": (audio.get("hash_high") or audio.get("hash_super") or "").strip().lower(),
        "duration": int(audio.get("timelength", 0) or 0) // 1000,
        "ext": (audio.get("extname") or "").strip(),
    }


# ============ 会话管理 ============
def load_session() -> dict:
    # BOT ADAPTATION: no disk session (see module docstring).
    return {}


def save_session(state: dict):
    # BOT ADAPTATION: in-memory only — normalise the device and
    # rebuild the cookie, but NEVER write the state (token/userid)
    # to disk. Device persistence is the wrapper's job.
    state["device"] = normalize_device(state.get("device", {}))
    state["cookie"] = build_concept_cookie(state)


def has_usable_session(state: dict) -> bool:
    return bool(state.get("token") and state.get("userid"))


# ============ 设备注册 ============
def register_device(state: dict, force: bool = False) -> dict:
    """注册设备 — 来自 concept_client.go registerDevice()"""
    device = normalize_device(state.get("device", {}))
    if force:
        device["dfid"] = "-"

    query, _ = default_query({**state, "device": device}, int(time.time()))
    body_cipher, body_key = playlist_aes_encrypt({
        "availableRamSize": 4983533568,
        "availableRomSize": 48114719,
        "availableSDSize": 48114717,
        "basebandVer": "",
        "batteryLevel": 100,
        "batteryStatus": 3,
        "brand": "Redmi",
        "buildSerial": "unknown",
        "device": "marble",
        "imei": device["guid"],
        "imsi": "",
        "manufacturer": "Xiaomi",
        "uuid": device["guid"],
        "accelerometer": False,
        "accelerometerValue": "",
        "gravity": False,
        "gravityValue": "",
        "gyroscope": False,
        "gyroscopeValue": "",
        "light": False,
        "lightValue": "",
        "magnetic": False,
        "magneticValue": "",
        "orientation": False,
        "orientationValue": "",
        "pressure": False,
        "pressureValue": "",
        "step_counter": False,
        "step_counterValue": "",
        "temperature": False,
        "temperatureValue": "",
    })

    p = rsa_pkcs1v15_encrypt_hex({
        "aes": body_key,
        "uid": int(state.get("userid", 0) or 0),
        "token": (state.get("token") or "").strip(),
    })

    query["part"] = "1"
    query["platid"] = "1"
    query["p"] = p
    query["signature"] = concept_signature_android(query, body_cipher)

    url = f"{CONCEPT_USER_SERVICE_BASE_URL}/risk/v2/r_register_dev?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, data=body_cipher.encode(), headers={
        "Content-Type": "text/plain;charset=UTF-8",
        "User-Agent": CONCEPT_UA,
        "kg-rc": "1",
        "kg-thash": CONCEPT_KGT_HASH,
        "kg-rec": "1",
        "kg-rf": CONCEPT_KGRF,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body_bytes = resp.read()
        decoded = playlist_aes_decrypt_from_binary(body_bytes, body_key)
        resp_data = json.loads(decoded)
        dfid = (resp_data.get("data", {}).get("dfid") or "").strip()
        if dfid:
            device["dfid"] = dfid
            device["source"] = "register/dev"
        else:
            if force:
                raise RuntimeError(f"device register returned no dfid: {resp_data}")
            device["dfid"] = device.get("dfid") or random_alpha_num(24)
            device["source"] = "fallback"
    except Exception as e:
        if force:
            raise
        device["dfid"] = device.get("dfid") or random_alpha_num(24)
        device["source"] = "fallback"

    device["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return device


def ensure_device(state: dict) -> dict:
    device = normalize_device(state.get("device", {}))
    if all(device.get(k) for k in ["dfid", "guid", "mid", "dev", "mac"]):
        if device["dfid"] != "-":
            return device
    return register_device(state, force=False)


# ============ 扫码登录 ============
def create_qrcode(state: dict) -> dict:
    """生成登录二维码 — 来自 concept_client.go CreateQRCode()"""
    state["device"] = ensure_device(state)

    query, _ = default_query(state, int(time.time()), True)
    query["appid"] = CONCEPT_QR_APPID
    query["type"] = "1"
    query["plat"] = "4"
    query["qrcode_txt"] = CONCEPT_QR_TEXT_URL
    query["srcappid"] = CONCEPT_SRC_APPID
    query["signature"] = concept_signature_web(query)

    url = f"{CONCEPT_LOGIN_BASE_URL}/v2/qrcode?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": CONCEPT_UA,
        "kg-rc": "1",
        "kg-thash": CONCEPT_KGT_HASH,
        "kg-rec": "1",
        "kg-rf": CONCEPT_KGRF,
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    qr_key = (data.get("data", {}).get("qrcode") or "").strip()
    if not qr_key:
        raise RuntimeError("二维码 key 为空")

    qr_url = f"https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode={urllib.parse.quote(qr_key)}"

    # 生成二维码图片 base64
    import qrcode
    img = qrcode.make(qr_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    state["qr_key"] = qr_key
    state["qr_url"] = qr_url
    state["qr_status"] = 1
    save_session(state)

    return {"url": qr_url, "base64": qr_b64, "qr_key": qr_key}


def check_qrcode(state: dict) -> dict:
    """检查二维码扫码状态 — 来自 concept_client.go CheckQRCode()"""
    qr_key = (state.get("qr_key") or "").strip()
    if not qr_key:
        raise RuntimeError("当前没有待扫码二维码")

    query, _ = default_query(state, int(time.time()), True)
    query["plat"] = "4"
    query["appid"] = CONCEPT_APPID
    query["srcappid"] = CONCEPT_SRC_APPID
    query["qrcode"] = qr_key
    query["signature"] = concept_signature_web(query)

    url = f"{CONCEPT_LOGIN_BASE_URL}/v2/get_userinfo_qrcode?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": CONCEPT_UA,
        "kg-rc": "1",
        "kg-thash": CONCEPT_KGT_HASH,
        "kg-rec": "1",
        "kg-rf": CONCEPT_KGRF,
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    resp_data = data.get("data", {})
    status = resp_data.get("status", 0)
    state["qr_status"] = status

    result = {"status": status, "nickname": str(resp_data.get("nickname", "")).strip()}

    if status == 4:
        # 登录成功
        token = str(resp_data.get("token", "")).strip()
        userid = str(resp_data.get("userid", "")).strip()
        nickname = str(resp_data.get("nickname", "")).strip()
        state["token"] = token
        state["userid"] = userid
        if nickname:
            state["nickname"] = nickname
        state["login_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        state["session_source"] = "concept_qr"
        state["qr_key"] = ""
        state["qr_url"] = ""
        save_session(state)
        result["token"] = token
        result["userid"] = userid
    elif status == 0:
        state["qr_key"] = ""
        state["qr_url"] = ""

    save_session(state)
    return result


# ============ 获取下载链接 ============
QUALITY_MAP = {
    "high": "high",
    "lossless": "flac",
    "high_320": "320",
    "standard": "128",
}

def quality_code(quality: str) -> str:
    return QUALITY_MAP.get(quality, "128")


def fetch_song_url(state: dict, song_hash: str, album_id: str = "", album_audio_id: str = "", quality: str = "high") -> dict:
    """获取播放/下载链接 — 来自 concept_client.go FetchSongURL()"""
    if not has_usable_session(state):
        raise RuntimeError("酷狗概念版未登录，请先扫码登录")

    state["device"] = ensure_device(state)
    query, device = default_query(state, int(time.time()), False)

    song_hash = song_hash.strip().lower()
    query["album_id"] = album_id or "0"
    query["area_code"] = "1"
    query["hash"] = song_hash
    query["ssa_flag"] = "is_fromtrack"
    query["version"] = "11436"
    query["page_id"] = CONCEPT_PAGE_ID
    query["quality"] = quality_code(quality)
    query["album_audio_id"] = album_audio_id or "0"
    query["behavior"] = "play"
    query["pid"] = "411"
    query["cmd"] = "26"
    query["pidversion"] = "3001"
    query["IsFreePart"] = "0"
    query["ppage_id"] = CONCEPT_PPAGE_ID
    query["cdnBackup"] = "1"
    query["kcard"] = "0"
    query["module"] = ""
    query["key"] = concept_sign_key(song_hash, query.get("mid", ""), query.get("userid", ""), query.get("appid", ""))
    query["signature"] = concept_signature_android(query, "")

    url = f"{CONCEPT_GATEWAY_BASE_URL}/v5/url?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": CONCEPT_UA,
        "kg-rc": "1",
        "kg-thash": CONCEPT_KGT_HASH,
        "kg-rec": "1",
        "kg-rf": CONCEPT_KGRF,
        "x-router": CONCEPT_ROUTE_TRACK,
        "Cookie": build_concept_cookie(state),
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    err_code = data.get("errcode", 0)
    if err_code == 20028:
        raise RuntimeError("设备验证失败(需要重新注册设备)")

    if data.get("status") != 1:
        raise RuntimeError(f"获取下载链接失败: status={data.get('status')} err={data.get('error', '')}")

    urls = data.get("url", [])
    if not urls or not urls[0].strip():
        raise RuntimeError("无可用下载链接")

    return {
        "url": urls[0].strip(),
        "ext": (data.get("extName") or "").strip(),
        "duration": data.get("timeLength", 0),
        "hash": song_hash,
        "quality": quality,
    }


# ============ 下载文件 ============
def download_file(url: str, output_path: str = None, filename: str = None) -> str:
    """下载音乐文件"""
    if not url:
        raise RuntimeError("下载链接为空")

    # 从 URL 猜测扩展名
    if not filename:
        url_lower = url.lower().split("?")[0]
        if url_lower.endswith(".flac"):
            ext = ".flac"
        elif url_lower.endswith(".mp3"):
            ext = ".mp3"
        elif url_lower.endswith(".m4a"):
            ext = ".m4a"
        else:
            ext = ".mp3"
        filename = f"download_{int(time.time())}{ext}"

    if not output_path:
        output_path = os.path.join(os.path.dirname(__file__), "downloads")
    os.makedirs(output_path, exist_ok=True)
    filepath = os.path.join(output_path, filename)

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(filepath, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)

    return filepath


# ============ 一站式: 搜索 → 下载 ============
def search_and_download(keyword: str, index: int = 0, quality: str = "high") -> dict:
    """搜索并下载第 index 首歌"""
    results = search(keyword)
    if not results:
        raise RuntimeError(f"未找到 '{keyword}' 的搜索结果")

    if index >= len(results):
        raise RuntimeError(f"序号超出范围，共 {len(results)} 条结果")

    song = results[index]
    state = load_session()

    # 先获取详细信息
    try:
        track_info = fetch_gateway_track_info(song["hash"])
        if track_info:
            album_id = track_info.get("album_id", song.get("album_id", ""))
            album_audio_id = track_info.get("album_audio_id", "")
            song_name = track_info.get("name", song["name"])
            artist = track_info.get("artist", song["artist"])
        else:
            album_id = song.get("album_id", "")
            album_audio_id = ""
            song_name = song["name"]
            artist = song["artist"]
    except Exception:
        album_id = song.get("album_id", "")
        album_audio_id = ""
        song_name = song["name"]
        artist = song["artist"]

    # 获取下载链接
    download_info = fetch_song_url(state, song["hash"], album_id, album_audio_id, quality)

    # 下载文件
    safe_name = f"{artist} - {song_name}".replace("/", "_").replace("\\", "_").replace(":", "_")
    ext = download_info.get("ext", "mp3")
    if not ext.startswith("."):
        ext = "." + ext
    filename = f"{safe_name}{ext}"
    filepath = download_file(download_info["url"], filename=filename)

    return {
        "filepath": filepath,
        "filename": filename,
        "song_name": song_name,
        "artist": artist,
        "quality": quality,
        "url": download_info["url"],
    }
