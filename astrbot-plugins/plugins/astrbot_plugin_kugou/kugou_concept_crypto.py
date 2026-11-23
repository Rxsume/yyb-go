#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
酷狗概念版加密/签名工具 — 从 MusicBot-Go (concept_crypto.go) 移植
依赖: pycryptodome
"""
import hashlib
import json
import os
import random
import string
import urllib.parse
from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

# ============ 常量 (来自 concept_client.go) ============
CONCEPT_APPID = "3116"
CONCEPT_CLIENT_VER = "11440"
CONCEPT_SRC_APPID = "2919"
CONCEPT_QR_APPID = "1001"
CONCEPT_UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"
CONCEPT_QR_TEXT_URL = "https://h5.kugou.com/apps/loginQRCode/html/index.html?appid=3116&"
CONCEPT_ROUTE_USER = "usercenter.kugou.com"
CONCEPT_ROUTE_TRACK = "trackercdn.kugou.com"
CONCEPT_KGT_HASH = "5d816a0"
CONCEPT_KGRF = "B9EDA08A64250DEFFBCADDEE00F8F25F"
CONCEPT_PAGE_ID = "967177915"
CONCEPT_PPAGE_ID = "356753938,823673182,967485191"
CONCEPT_SIGN_SECRET = "LnT6xpN3khm36zse0QzvmgTZ3waWdRSA"
CONCEPT_PLAY_SECRET = "185672dd44712f60bb1736df5a377e82"
CONCEPT_REFRESH_KEY = "c24f74ca2820225badc01946dba4fdf7"
CONCEPT_REFRESH_IV = "adc01946dba4fdf7"
CONCEPT_T2_KEY = "fd14b35e3f81af3817a20ae7adae7020"
CONCEPT_T2_IV = "17a20ae7adae7020"
CONCEPT_T1_KEY = "5e4ef500e9597fe004bd09a46d8add98"
CONCEPT_T1_IV = "04bd09a46d8add98"
CONCEPT_T2_FIXED_PART = "0f607264fc6318a92b9e13c65db7cd3c"
DEFAULT_CONCEPT_VIP_TYPE = "6"

# 搜索/网关用的 key
KUGOU_GATEWAY_SIGN_KEY = "OIlwieks28dk2k092lksi2UIkp"
KUGOU_PLAY_SIGN_KEY = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"

CONCEPT_LOGIN_BASE_URL = "https://login-user.kugou.com"
CONCEPT_LOGIN_TOKEN_URL = "http://login.user.kugou.com/v5/login_by_token"
CONCEPT_GATEWAY_BASE_URL = "https://gateway.kugou.com"
CONCEPT_USER_SERVICE_BASE_URL = "https://userservice.kugou.com"
CONCEPT_VIP_BASE_URL = "https://kugouvip.kugou.com"

# RSA 公钥
CONCEPT_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDECi0Np2UR87scwrvTr72L6oO01rBbbBPriSDFPxr3Z5syug0O24QyQO8bg27+0+4kBzTBTBOZ/WWU0WryL1JSXRTXLgFVxtzIY41Pe7lPOgsfTCn5kZcvKhYKJesKnnJDNr5/abvTGf+rHG3YRwsCHcQ08/q6ifSioBszvb3QiwIDAQAB
-----END PUBLIC KEY-----"""


# ============ MD5 ============
def concept_md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


# ============ 签名 ============
def concept_signature_web(params: dict) -> str:
    """Web 签名: MD5(KUGOU_PLAY_SIGN_KEY + 排序拼接的KV + KUGOU_PLAY_SIGN_KEY)"""
    keys = sorted(params.keys())
    builder = ""
    for k in keys:
        v = params[k]
        if isinstance(v, list):
            for vv in v:
                builder += f"{k}={vv}"
        else:
            builder += f"{k}={v}"
    return concept_md5(KUGOU_PLAY_SIGN_KEY + builder + KUGOU_PLAY_SIGN_KEY)


def concept_signature_android(params: dict, body: str = "") -> str:
    """Android 签名: MD5(CONCEPT_SIGN_SECRET + 排序拼接的KV + body + CONCEPT_SIGN_SECRET)"""
    keys = sorted(params.keys())
    builder = ""
    for k in keys:
        v = params[k]
        if isinstance(v, list):
            for vv in v:
                builder += f"{k}={vv}"
        else:
            builder += f"{k}={v}"
    return concept_md5(CONCEPT_SIGN_SECRET + builder + body + CONCEPT_SIGN_SECRET)


def concept_sign_key(hash_val: str, mid: str, user_id: str, app_id: str) -> str:
    return concept_md5(
        hash_val.strip().lower()
        + CONCEPT_PLAY_SECRET
        + app_id.strip()
        + mid.strip()
        + user_id.strip()
    )


# ============ AES-CBC ============
def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    padding = block_size - len(data) % block_size
    if padding == 0:
        padding = block_size
    return data + bytes([padding]) * padding


def pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16 != 0:
        raise ValueError("invalid pkcs7 data length")
    padding = data[-1]
    if padding <= 0 or padding > 16 or padding > len(data):
        raise ValueError("invalid pkcs7 padding")
    if data[-padding:] != bytes([padding]) * padding:
        raise ValueError("invalid pkcs7 padding value")
    return data[:-padding]


def aes_cbc_encrypt_hex(payload, key: str, iv: str) -> str:
    """AES-CBC 加密，输出 hex"""
    if isinstance(payload, (dict, list)):
        plain = json.dumps(payload, separators=(",", ":")).encode()
    elif isinstance(payload, str):
        plain = payload.encode()
    elif isinstance(payload, bytes):
        plain = payload
    else:
        plain = str(payload).encode()

    key_b = key.encode() if isinstance(key, str) else key
    iv_b = iv.encode() if isinstance(iv, str) else iv

    cipher = AES.new(key_b, AES.MODE_CBC, iv_b)
    encrypted = cipher.encrypt(pkcs7_pad(plain))
    return encrypted.hex()


def aes_cbc_encrypt_string_hex(text: str, key: str, iv: str) -> str:
    return aes_cbc_encrypt_hex(text, key, iv)


def aes_cbc_decrypt_hex(cipher_hex: str, key: str, iv: str) -> str:
    data = bytes.fromhex(cipher_hex.strip())
    key_b = key.encode() if isinstance(key, str) else key
    iv_b = iv.encode() if isinstance(iv, str) else iv
    cipher = AES.new(key_b, AES.MODE_CBC, iv_b)
    plain = cipher.decrypt(data)
    return pkcs7_unpad(plain).decode()


def aes_cbc_encrypt_hex_auto(payload):
    """自动生成 key/iv，返回 (cipher_hex, temp_key)"""
    temp_key = random_alpha_num(16).lower()
    md5_text = concept_md5(temp_key)
    key = md5_text[:32]
    iv = key[-16:]
    cipher_hex = aes_cbc_encrypt_hex(payload, key, iv)
    return cipher_hex, temp_key


def playlist_aes_encrypt(payload):
    """设备注册用的 AES 加密，返回 (base64_cipher, temp_key)"""
    import base64
    temp_key = random_alpha(6).lower()
    md5_text = concept_md5(temp_key)
    key = md5_text[:16]
    iv = md5_text[16:32]

    if isinstance(payload, (dict, list)):
        plain = json.dumps(payload, separators=(",", ":")).encode()
    elif isinstance(payload, str):
        plain = payload.encode()
    else:
        plain = str(payload).encode()

    cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
    encrypted = cipher.encrypt(pkcs7_pad(plain))
    return base64.b64encode(encrypted).decode(), temp_key


def playlist_aes_decrypt_from_binary(body: bytes, temp_key: str) -> str:
    import base64
    md5_text = concept_md5(temp_key)
    key = md5_text[:16]
    iv = md5_text[16:32]
    decoded = base64.b64decode(base64.b64encode(body))
    cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
    plain = cipher.decrypt(decoded)
    return pkcs7_unpad(plain).decode()


# ============ RSA ============
def rsa_pkcs1v15_encrypt_hex(payload, public_key_pem: str = None) -> str:
    """RSA PKCS1v15 加密，输出 hex"""
    import base64
    if public_key_pem is None:
        public_key_pem = CONCEPT_PUBLIC_KEY_PEM
    key = RSA.import_key(public_key_pem)
    cipher = PKCS1_v1_5.new(key)
    if isinstance(payload, (dict, list)):
        plain = json.dumps(payload, separators=(",", ":")).encode()
    elif isinstance(payload, str):
        plain = payload.encode()
    else:
        plain = str(payload).encode()
    encrypted = cipher.encrypt(plain)
    return encrypted.hex()


def rsa_raw_encrypt_hex(payload, public_key_pem: str = None) -> str:
    """RSA 原始加密 (直接 m^e mod n)，输出 hex"""
    if public_key_pem is None:
        public_key_pem = CONCEPT_PUBLIC_KEY_PEM
    key = RSA.import_key(public_key_pem)
    if isinstance(payload, (dict, list)):
        plain = json.dumps(payload, separators=(",", ":")).encode()
    elif isinstance(payload, str):
        plain = payload.encode()
    else:
        plain = str(payload).encode()

    key_length = (key.n.bit_length() + 7) // 8
    if len(plain) > key_length:
        raise ValueError("rsa payload exceeds key size")
    padded = plain.rjust(key_length, b'\x00')
    m = int.from_bytes(padded, 'big')
    c = pow(m, key.e, key.n)
    return f"{c:0{key_length * 2}x}"


# ============ 随机数 ============
def random_guid() -> str:
    import uuid
    return str(uuid.uuid4())


def random_alpha_num(length: int) -> str:
    charset = string.ascii_uppercase + string.digits
    return ''.join(random.choice(charset) for _ in range(length))


def random_alpha(length: int) -> str:
    charset = string.ascii_lowercase
    return ''.join(random.choice(charset) for _ in range(length))


def calculate_mid(guid: str) -> str:
    """从 guid 计算 mid: MD5(guid) 转十进制"""
    digest = concept_md5(guid)
    return str(int(digest, 16))


# ============ 设备信息 ============
def normalize_device(device: dict = None) -> dict:
    if device is None:
        device = {}
    if not device.get("guid"):
        device["guid"] = concept_md5(random_guid())
    if not device.get("mid"):
        device["mid"] = calculate_mid(device["guid"])
    if not device.get("dev"):
        device["dev"] = random_alpha(10)
    device["dev"] = device["dev"].upper()
    if not device.get("mac"):
        device["mac"] = "02:00:00:00:00:00"
    if not device.get("dfid"):
        device["dfid"] = "-"
    return device


# ============ Cookie 构建 ============
def build_concept_cookie(state: dict) -> str:
    pairs = []
    device = normalize_device(state.get("device", {}))

    def append_kv(k, v):
        v = (v or "").strip()
        if v:
            pairs.append(f"{k}={v}")

    append_kv("token", state.get("token", ""))
    append_kv("userid", state.get("userid", ""))
    append_kv("t1", state.get("t1", ""))
    append_kv("vip_token", state.get("vip_token", ""))
    append_kv("vip_type", state.get("vip_type", "") or DEFAULT_CONCEPT_VIP_TYPE)
    append_kv("dfid", device.get("dfid", ""))
    append_kv("KUGOU_API_MID", device.get("mid", ""))
    append_kv("KUGOU_API_GUID", device.get("guid", ""))
    append_kv("KUGOU_API_DEV", device.get("dev", ""))
    append_kv("KUGOU_API_MAC", device.get("mac", ""))
    return "; ".join(pairs)


def default_query(state: dict, now_ts: int = None, include_auth: bool = True) -> tuple:
    """构建默认查询参数"""
    import time
    if now_ts is None:
        now_ts = int(time.time())
    device = normalize_device(state.get("device", {}))
    params = {
        "dfid": device.get("dfid", "-"),
        "mid": device.get("mid", ""),
        "uuid": "-",
        "appid": CONCEPT_APPID,
        "clientver": CONCEPT_CLIENT_VER,
        "clienttime": str(now_ts),
    }
    if include_auth or state.get("token") or state.get("userid"):
        if state.get("token"):
            params["token"] = state["token"]
        if state.get("userid"):
            params["userid"] = state["userid"]
    return params, device
