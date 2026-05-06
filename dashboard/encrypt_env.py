"""
.env 文件加密工具 — Windows DPAPI

使用 Windows Data Protection API (DPAPI) 加密 .env 文件。
加密后的 .env.enc 只能由当前 Windows 用户在当前机器上解密。

用法:
    python encrypt_env.py              # 加密 .env → .env.enc
    python encrypt_env.py --decrypt    # 解密 .env.enc → .env (调试用)
    python encrypt_env.py --test       # 测试加解密功能
"""
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path


# ============================================================
# Windows DPAPI 封装
# ============================================================

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_protect(plaintext: bytes, description: str = "CryptoOptions .env") -> bytes:
    """使用 DPAPI 加密（绑定当前用户+机器）"""
    data_in = DATA_BLOB()
    data_in.cbData = len(plaintext)
    data_in.pbData = ctypes.cast(ctypes.create_string_buffer(plaintext, len(plaintext)), ctypes.POINTER(ctypes.c_char))
    data_out = DATA_BLOB()

    # CRYPTPROTECT_UI_FORBIDDEN = 0x1 — 禁止 UI 弹窗
    flags = 0x1  # 仅绑定当前用户

    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data_in),
        ctypes.c_wchar_p(description),
        None, None, None,
        flags,
        ctypes.byref(data_out),
    ):
        raise OSError("CryptProtectData failed")

    encrypted = ctypes.string_at(data_out.pbData, data_out.cbData)
    ctypes.windll.kernel32.LocalFree(data_out.pbData)
    return encrypted


def _dpapi_unprotect(encrypted: bytes) -> bytes:
    """使用 DPAPI 解密"""
    data_in = DATA_BLOB()
    data_in.cbData = len(encrypted)
    data_in.pbData = ctypes.cast(ctypes.create_string_buffer(encrypted, len(encrypted)), ctypes.POINTER(ctypes.c_char))
    data_out = DATA_BLOB()
    desc_out = ctypes.c_wchar_p()

    flags = 0x1

    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        ctypes.byref(desc_out),
        None, None, None,
        flags,
        ctypes.byref(data_out),
    ):
        raise OSError("CryptUnprotectData failed — 可能不是同一个 Windows 用户")

    plaintext = ctypes.string_at(data_out.pbData, data_out.cbData)
    ctypes.windll.kernel32.LocalFree(data_out.pbData)
    return plaintext


# ============================================================
# 加解密操作
# ============================================================

def encrypt_env(env_path: Path, enc_path: Path) -> None:
    """将 .env 加密为 .env.enc"""
    if not env_path.exists():
        print(f"错误: {env_path} 不存在")
        sys.exit(1)

    plaintext = env_path.read_bytes()
    encrypted = _dpapi_protect(plaintext)

    # Write as base64 for safe transport between OS reinstalls
    import base64
    b64 = base64.b64encode(encrypted)
    enc_path.write_bytes(b64)
    print(f"[OK] Encrypted: {env_path} -> {enc_path}")
    print(f"     Original: {len(plaintext)} bytes -> Encrypted: {len(b64)} bytes (base64)")
    print(f"     You can safely delete {env_path} (gitignored)")


def decrypt_env(enc_path: Path, env_path: Path) -> None:
    """将 .env.enc 解密为 .env（调试用）"""
    if not enc_path.exists():
        print(f"错误: {enc_path} 不存在")
        sys.exit(1)

    import base64
    b64_data = enc_path.read_bytes()
    encrypted = base64.b64decode(b64_data)
    plaintext = _dpapi_unprotect(encrypted)
    env_path.write_bytes(plaintext)
    print(f"[OK] Decrypted: {enc_path} -> {env_path}")


def test_dpapi() -> None:
    """测试 DPAPI 加解密是否正常"""
    test_data = b"BINANCE_API_KEY=test123\nBINANCE_SECRET_KEY=test456\n"
    print("测试 Windows DPAPI 加解密...")
    encrypted = _dpapi_protect(test_data, "Test encryption")
    print(f"  加密成功: {len(test_data)} bytes → {len(encrypted)} bytes encrypted")

    decrypted = _dpapi_unprotect(encrypted)
    assert decrypted == test_data, f"解密结果不匹配! {decrypted[:50]} != {test_data[:50]}"
    print("  解密验证通过 ✅")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    base_dir = Path(__file__).parent
    env_path = base_dir / ".env"
    enc_path = base_dir / ".env.enc"

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--decrypt":
            decrypt_env(enc_path, env_path)
        elif cmd == "--test":
            test_dpapi()
        elif cmd == "--help":
            print(__doc__)
        else:
            print(f"未知参数: {cmd}\n用法: python encrypt_env.py [--decrypt|--test]")
    else:
        # 默认：加密
        encrypt_env(env_path, enc_path)
