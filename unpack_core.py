#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unpack_core.py —— 解压缩工具的核心逻辑（不依赖 GUI，可单独测试/复用）

能力：
  1. 按「文件头魔数」识别真实格式，扩展名骗人也能认（例如 .7z 实为 RAR5）
  2. 多后端调度：unar / 7zz / unrar / bsdtar / python(zipfile|tarfile)
  3. 密码自动尝试（界面输入 → passwords.txt → 常见默认密码）
  4. 目录批量解压，每个包解到独立子目录
  5. 解压后生成 SHA256 / MD5 校验清单
  6. 中日文文件名乱码识别与修复（zipfile CP437 误判场景）

设计原则：不做危险操作（不删除、不覆盖已解压成功的目录）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import threading
import zipfile

# ---------------------------------------------------------------- 格式识别

MAGIC = [
    # 注意顺序：RAR5 的签名以 RAR4 签名为前缀，必须先匹配 RAR5
    (b"Rar!\x1a\x07\x01\x00", "rar5", "RAR5 压缩包"),
    (b"Rar!\x1a\x07\x00", "rar", "RAR 压缩包"),
    (b"7z\xbc\xaf\x27\x1c", "7z", "7-Zip 压缩包"),
    (b"PK\x03\x04", "zip", "ZIP 压缩包"),
    (b"PK\x05\x06", "zip", "ZIP 压缩包（空包）"),
    (b"PK\x07\x08", "zip", "ZIP 压缩包（分卷片段）"),
    (b"\x1f\x8b", "gzip", "GZip 压缩"),
    (b"BZh", "bzip2", "BZip2 压缩"),
    (b"\xfd7zXZ\x00", "xz", "XZ 压缩"),
    (b"\x28\xb5\x2f\xfd", "zstd", "Zstandard 压缩"),
    (b"LZIP", "lzip", "Lzip 压缩"),
    (b"MSCF", "cab", "Microsoft CAB"),
    (b"ARJ\x60", "arj", "ARJ 压缩包"),
]

# 有些“伪扩展名”的常见真实后缀，用于给用户更直观的说明
EXT_HINT = {
    "rar": ".rar", "rar5": ".rar", "7z": ".7z", "zip": ".zip",
    "gzip": ".tar.gz", "bzip2": ".tar.bz2", "xz": ".tar.xz",
    "zstd": ".tar.zst", "tar": ".tar", "iso": ".iso", "cab": ".cab",
}


def sniff(path: str) -> tuple[str, str]:
    """按文件头判断真实格式。返回 (kind, 人类可读说明)。"""
    with open(path, "rb") as f:
        head = f.read(512)
    if not head:
        return "empty", "空文件"
    for sig, kind, desc in MAGIC:
        if head.startswith(sig):
            return kind, desc
    # ISO 9660：0x8001 处有 "CD001"（注意别被 'BZh'/'MSCF' 之类的偏移巧合骗到）
    try:
        with open(path, "rb") as f:
            f.seek(0x8001)
            if f.read(5) == b"CD001":
                return "iso", "ISO 9660 光盘镜像"
    except OSError:
        pass
    # tar：偏移 257 处的 "ustar"
    if len(head) > 265 and head[257:262] == b"ustar":
        return "tar", "TAR 归档"
    return "unknown", "未知格式"


def is_archive(path: str) -> bool:
    return sniff(path)[0] not in ("unknown", "empty")


def sniff_ext(path: str) -> tuple[str, str]:
    """按扩展名猜测格式（仅用于和真实格式对比展示）。"""
    name = os.path.basename(path).lower()
    for ext, kind in (
        (".tar.gz", "gzip"), (".tgz", "gzip"), (".tar.bz2", "bzip2"),
        (".tbz2", "bzip2"), (".tar.xz", "xz"), (".txz", "xz"),
        (".tar.zst", "zstd"), (".tzst", "zstd"), (".tar", "tar"),
        (".rar", "rar"), (".7z", "7z"), (".zip", "zip"), (".iso", "iso"),
        (".cab", "cab"), (".gz", "gzip"), (".bz2", "bzip2"), (".xz", "xz"),
    ):
        if name.endswith(ext):
            return kind, ext
    return "unknown", os.path.splitext(name)[1] or "无扩展名"


TAR_KINDS = {"tar", "gzip", "bzip2", "xz", "zstd", "lzip"}

# ---------------------------------------------------------------- 后端探测

BACKEND_PATHS: dict[str, str] = {}


def find_tool(name: str) -> str | None:
    if name in BACKEND_PATHS:
        return BACKEND_PATHS[name] or None
    p = shutil.which(name)
    if not p:
        # 常见非 PATH 位置
        for cand in (
            f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}",
            f"/opt/homebrew/Caskroom/miniconda/base/bin/{name}",
            f"/usr/bin/{name}",
        ):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                p = cand
                break
    BACKEND_PATHS[name] = p or ""
    return p


def backend_report() -> list[dict]:
    """返回依赖探测结果，供界面显示与安装提示。"""
    items = [
        {"tool": "unar", "brew": "unar", "role": "主力：RAR/RAR5/7z/ZIP 全格式 + 密码", "required": True},
        {"tool": "lsar", "brew": "unar", "role": "列出包内容（预览）", "required": False},
        {"tool": "7zz", "brew": "sevenzip", "role": "7z 备用后端", "required": False},
        {"tool": "unrar", "brew": "unrar", "role": "RAR 备用后端", "required": False},
        {"tool": "bsdtar", "brew": "libarchive", "role": "tar/zip 备用后端", "required": False},
        {"tool": "brew", "brew": None, "role": "用于一键安装缺失依赖", "required": False},
    ]
    out = []
    for it in items:
        path = find_tool(it["tool"])
        out.append({**it, "path": path, "ok": bool(path)})
    return out


def missing_brew_packages() -> list[str]:
    """需要（且能）通过 brew 安装的缺失依赖。"""
    if not find_tool("brew"):
        return []
    pkgs = []
    if not find_tool("unar"):
        pkgs.append("unar")
    if not find_tool("7zz"):
        pkgs.append("sevenzip")
    if not find_tool("unrar"):
        pkgs.append("unrar")
    if not find_tool("bsdtar"):
        pkgs.append("libarchive")
    return pkgs


def brew_install_command(pkgs: list[str]) -> str:
    brew = find_tool("brew") or "brew"
    return f"{brew} install " + " ".join(pkgs) if pkgs else ""


def available_backends() -> list[str]:
    return [t for t in ("unar", "7zz", "unrar", "bsdtar") if find_tool(t)]


# ---------------------------------------------------------------- 密码管理

DEFAULT_PASSWORDS = [
    "oldmanemu.net", "www.oldmanemu.net", "oldmanemu",
    "123456", "1234", "12345678", "0000", "password",
    "www.emu-zone.org", "www.emu618.com", "emu618",
]


def load_passwords(extra_file: str | None = None,
                   user_password: str | None = None) -> list[str]:
    """密码候选：界面输入 → passwords.txt → 内置常见密码（去重保序）。"""
    cands: list[str] = []
    if user_password:
        cands.append(user_password)
    for f in filter(None, [extra_file, os.path.join(os.path.dirname(os.path.abspath(__file__)), "passwords.txt")]):
        if os.path.isfile(f):
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        s = line.strip()
                        if s and not s.startswith("#"):
                            cands.append(s)
            except OSError:
                pass
    cands.extend(DEFAULT_PASSWORDS)
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ---------------------------------------------------------------- 进程执行

class Cancelled(Exception):
    pass


def run(cmd: list[str], timeout: float | None = None,
        cancel: threading.Event | None = None,
        cwd: str | None = None) -> tuple[int, str, str]:
    """执行命令，返回 (returncode, stdout, stderr)。支持取消与超时。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace", cwd=cwd,
    )
    try:
        while True:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                raise Cancelled()
            try:
                out, err = proc.communicate(timeout=0.25)
                return proc.returncode, out, err
            except subprocess.TimeoutExpired:
                if timeout is not None and timeout <= 0:
                    proc.kill()
                    raise
                if timeout is not None:
                    timeout -= 0.25
    finally:
        if proc.poll() is None:
            proc.kill()


def archive_entries(path: str, kind: str, password: str | None = None,
                    cancel: threading.Event | None = None) -> list[str]:
    """列出压缩包内的条目名（不落盘）。失败返回 []。"""
    if kind in ("rar", "rar5", "7z", "zip", "cab", "arj") and find_tool("lsar"):
        cmd = ["lsar", "-j"]
        if password:
            cmd += ["-p", password]
        cmd.append(path)
        try:
            rc, out, _ = run(cmd, cancel=cancel)
        except Cancelled:
            raise
        if rc == 0:
            import json
            try:
                data = json.loads(out)
                return [e.get("XADFileName", "") for e in data.get("lsarContents", []) if e.get("XADFileName")]
            except Exception:
                pass
    # python 兜底：zip
    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as z:
                return z.namelist()
        except Exception:
            return []
    return []


def is_encrypted(path: str, kind: str) -> bool:
    """粗判是否加密（用于界面提示与自动试密码）。"""
    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as z:
                return any(zi.flag_bits & 0x1 for zi in z.infolist())
        except Exception:
            return False
    if kind in ("rar", "rar5", "7z", "cab", "arj"):
        if kind == "7z":
            # 7z 头里带 AES 标志时，文件名区不可读 → 直接判为加密
            try:
                with open(path, "rb") as f:
                    if b"\x06\xf1\x07\x01" in f.read(4096):
                        return True
            except OSError:
                pass
        if find_tool("lsar"):
            try:
                rc, out, err = run(["lsar", path])
                text = (out + err).lower()
                if "password" in text or "密码" in text:
                    return True
                return False          # 能顺利列出目录 → 未加密（头未加密）
            except Exception:
                return False
    return False


# ------------------------------------------------- 后端执行（含密码尝试）

def _attempt_plan(path: str, kind: str, dest: str) -> list[tuple[str, list[str]]]:
    """
    生成候选命令列表 [(后端名, argv), ...]，按可靠性排序。
    经验（macOS 实测）：
      · tar 系列（含 .tar.gz/.tar.xz/.tar.zst）里若用 PAX 扩展头存 UTF-8 文件名，
        unar 会把它解码成 '??'，而 bsdtar(libarchive) 正常 → tar 系列 bsdtar 优先。
      · RAR/RAR5/7z 只有 unar 支持，仍以其为主。
    """
    plans: list[tuple[str, list[str]]] = []
    unar, sevenzz, unrar, bsdtar = (find_tool("unar"), find_tool("7zz"),
                                     find_tool("unrar"), find_tool("bsdtar"))
    if kind in TAR_KINDS and bsdtar:
        plans.append(("bsdtar", [bsdtar, "-xf"]))
    # 主力：unar，全格式，-D 不建外层目录，-f 覆盖，-o 输出目录
    if unar:
        plans.append(("unar", [unar, "-f", "-D", "-o", dest]))
    # 7zz
    if kind in ("7z", "zip", "rar", "rar5", "cab", "gzip", "bzip2", "xz", "zstd", "tar") and sevenzz:
        plans.append(("7zz", [sevenzz, "x", "-y", f"-o{dest}"]))
    # unrar
    if kind in ("rar", "rar5") and unrar:
        plans.append(("unrar", [unrar, "x", "-y", "-o+"]))
    # 其它格式的 bsdtar 兜底
    if bsdtar and kind == "zip" and not any(p[0] == "bsdtar" for p in plans):
        plans.append(("bsdtar", [bsdtar, "-xf"]))
    # python 内置
    if kind == "zip":
        plans.append(("python-zipfile", ["__python_zip__"]))
    if kind in TAR_KINDS:
        plans.append(("python-tarfile", ["__python_tar__"]))
    return plans


def _backend_argv(name: str, argv: list[str], path: str, dest: str,
                  password: str | None) -> list[str]:
    """把密码/路径补进 argv。"""
    if name == "unar":
        cmd = list(argv)
        if password:
            cmd += ["-p", password]
        cmd.append(path)
        return cmd
    if name == "7zz":
        cmd = list(argv)
        if password:
            cmd.append(f"-p{password}")
        else:
            cmd.append("-p")          # 空密码：避免交互式提问卡住
        cmd.append(path)
        return cmd
    if name == "unrar":
        cmd = list(argv)
        if password:
            cmd.insert(1, f"-p{password}")
        else:
            cmd.insert(1, "-p-")      # 不询问密码
        cmd.append(path)
        return cmd
    if name == "bsdtar":
        return list(argv) + [path]
    return list(argv)


def _python_extract(kind: str, path: str, dest: str, password: str | None,
                    cancel: threading.Event | None = None) -> None:
    """zipfile / tarfile 兜底解压。"""
    if kind == "zip":
        with zipfile.ZipFile(path) as z:
            if password:
                z.setpassword(password.encode("utf-8"))
            names = z.namelist()
            for i, n in enumerate(names):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                z.extract(n, dest)
        return
    import tarfile
    mode = {"tar": "r:", "gzip": "r:gz", "bzip2": "r:bz2",
            "xz": "r:xz", "zstd": "r:*", "lzip": "r:*"}.get(kind, "r:*")
    with tarfile.open(path, mode) as t:
        members = t.getmembers()
        for m in members:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            t.extract(m, dest)
    return


def _password_ok_error(text: str) -> bool:
    t = text.lower()
    keys = ("password", "wrong password", "incorrect", "encrypted",
            "密码", "crc failed", "checksum error", "damaged")
    return any(k in t for k in keys)


def extract_archive(path: str, dest: str, kind: str | None = None,
                    passwords: list[str] | None = None,
                    log=lambda s: None,
                    cancel: threading.Event | None = None,
                    timeout: float | None = None) -> dict:
    """
    解压一个包到 dest。返回 dict：
      ok, backend, password, dest, error, files, bytes, seconds
    密码按 passwords 顺序尝试；所有密码都失败则返回最后一次错误。
    """
    import time
    t0 = time.time()
    if kind is None:
        kind, _ = sniff(path)
    # 语义：None = 用内置/密码本候选；[] = 明确不试任何密码
    if passwords is None:
        passwords = load_passwords()
    os.makedirs(dest, exist_ok=True)

    plans = _attempt_plan(path, kind, dest)
    if not plans:
        return {"ok": False, "backend": None, "password": None, "dest": dest,
                "error": f"没有可用后端处理 {kind} 格式（请安装 unar 或 sevenzip）",
                "files": 0, "bytes": 0, "seconds": time.time() - t0}

    # 密码候选：不加密的包只需要“无密码”一轮
    if is_encrypted(path, kind):
        if not passwords:
            return {"ok": False, "backend": None, "password": None,
                    "dest": dest, "error": "该压缩包已加密，但没有任何可用密码",
                    "files": 0, "bytes": 0, "seconds": time.time() - t0}
        pw_list: list[str | None] = list(passwords)
    else:
        pw_list = [None]

    last_err = ""
    for backend, base_argv in plans:
        for pw in pw_list:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            label = f"{backend}" + (f" 密码 {pw!r}" if pw else " 无密码")
            log(f"  尝试 {label} …")
            try:
                if backend.startswith("python"):
                    _python_extract(kind, path, dest, pw, cancel)
                    rc, out, err = 0, "", ""
                else:
                    cmd = _backend_argv(backend, base_argv, path, dest, pw)
                    rc, out, err = run(cmd, timeout=timeout, cancel=cancel, cwd=dest)
            except Cancelled:
                raise
            except Exception as e:                      # 后端自身异常
                last_err = f"{backend}: {e}"
                log(f"    ✗ {last_err}")
                continue

            if rc == 0:
                files, nbytes = dir_stats(dest)
                if files == 0:
                    # 有些后端密码错/包有问题时仍返回 0，但一个文件都没解出来
                    last_err = "后端返回成功但未解出任何文件（密码错误或包损坏）"
                    log(f"    ✗ {last_err}")
                    continue
                log(f"    ✓ {backend} 成功"
                    + (f"（密码 {pw!r}）" if pw else ""))
                return {"ok": True, "backend": backend, "password": pw,
                        "dest": dest, "error": "", "files": files,
                        "bytes": nbytes, "seconds": time.time() - t0}

            text = (out or "") + (err or "")
            last_err = text.strip().splitlines()[-1] if text.strip() else f"退出码 {rc}"
            log(f"    ✗ {last_err[:200]}")
            if not _password_ok_error(text) and pw is None:
                # 非密码类错误（格式不支持/文件损坏）→ 换下一个后端
                break
    err = last_err or "所有后端与密码均失败"
    if any(pw for pw in pw_list):
        err = (f"解密失败：密码都不对（已试 {len([p for p in pw_list if p])} 个）；"
               f"最后错误：{last_err}")
    # 密码错的后端可能已经往目标目录写了垃圾/半截文件，失败就清掉
    cleanup_partial(dest, log=log)
    return {"ok": False, "backend": None, "password": None, "dest": dest,
            "error": err, "files": 0, "bytes": 0, "seconds": time.time() - t0}


# ---------------------------------------------------------------- 结果校验

def dir_stats(root: str) -> tuple[int, int]:
    """返回 (文件数, 总字节数)，跳过 .DS_Store。"""
    n = total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__MACOSX"]
        for fn in filenames:
            if fn in (".DS_Store", "Thumbs.db"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.islink(fp):
                    n += 1
                    continue
                total += os.path.getsize(fp)
                n += 1
            except OSError:
                pass
    return n, total


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} TB"


def hash_file(path: str, algo: str = "sha256", chunk: int = 1 << 20,
              cancel: threading.Event | None = None) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_and_manifest(root: str, out_dir: str | None = None,
                        log=lambda s: None,
                        cancel: threading.Event | None = None,
                        max_total_bytes: int = 8 << 30) -> dict:
    """
    遍历 root，生成 SHA256 / MD5 清单文件，并返回统计。
    超过 max_total_bytes 时只统计不逐个哈希（避免耗时过长）。
    """
    out_dir = out_dir or root
    rows_sha, rows_md5 = [], []
    files = total = 0
    skipped_reason = ""
    # 先收集清单，便于估体量
    targets: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__MACOSX"]
        for fn in sorted(filenames):
            if fn in (".DS_Store", "Thumbs.db") or fn.startswith(".unpack-"):
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.isfile(fp) and not os.path.islink(fp):
                targets.append((fp, os.path.relpath(fp, root)))
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
                files += 1
    if total > max_total_bytes:
        skipped_reason = (f"总大小 {human_size(total)} 超过阈值 "
                          f"{human_size(max_total_bytes)}，已跳过逐文件哈希")
        log(f"  ⚠ {skipped_reason}")
        return {"files": files, "bytes": total, "hashed": 0,
                "sha256_manifest": None, "md5_manifest": None,
                "skipped_reason": skipped_reason}

    for fp, rel in targets:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            sha = hash_file(fp, "sha256", cancel=cancel)
            md5 = hash_file(fp, "md5", cancel=cancel)
        except OSError as e:
            log(f"  ⚠ 无法读取 {rel}: {e}")
            continue
        rows_sha.append(f"{sha}  {rel}")
        rows_md5.append(f"{md5}  {rel}")

    sha_path = os.path.join(out_dir, ".unpack-SHA256SUMS.txt")
    md5_path = os.path.join(out_dir, ".unpack-MD5SUMS.txt")
    header = (f"# generated by archive-unpacker\n"
              f"# root: {os.path.abspath(root)}\n"
              f"# files: {len(rows_sha)}\n")
    with open(sha_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(rows_sha) + "\n")
    with open(md5_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(rows_md5) + "\n")
    log(f"  ✓ 校验清单：{os.path.basename(sha_path)} / {os.path.basename(md5_path)}")
    return {"files": files, "bytes": total, "hashed": len(rows_sha),
            "sha256_manifest": sha_path, "md5_manifest": md5_path,
            "skipped_reason": ""}


# ------------------------------------------------- 中日文文件名乱码修复

def _cjk_ratio(s: str) -> float:
    letters = [c for c in s if not c.isspace() and c not in "./\\_- "]
    if not letters:
        return 0.0
    cjk = [c for c in letters if "\u3040" <= c <= "\u9fff"
           or "\uac00" <= c <= "\ud7af" or "\uf900" <= c <= "\ufaff"]
    return len(cjk) / len(letters)


MOJIBAKE_ENCODINGS = ("utf-8", "shift_jis", "cp932", "gbk", "big5",
                      "euc_jp", "euc_kr")


def fix_mojibake_name(name: str, encodings=MOJIBAKE_ENCODINGS) -> str | None:
    """
    尝试把乱码名还原：mojibake --cp437--> 原始字节 --> 猜编码解码。

    要点：
      · 先做 NFC 规范化 —— macOS 的文件系统会把重音字符拆成组合序列，
        直接 encode('cp437') 会失败（U+0300 组合符不在 CP437 里）。
      · UTF-8 放第一位：CP437 误解码出来的字节序列本来就是 UTF-8。
      · 仅当「原名字不含 CJK、还原后含 CJK」才算成功，避免误改正常名字。
    """
    import unicodedata
    name = unicodedata.normalize("NFC", name)
    if _cjk_ratio(name) > 0.1:
        return None
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return None
    for enc in encodings:
        try:
            fixed = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if fixed and fixed != name and _cjk_ratio(fixed) >= 0.3:
            return fixed
    return None


def looks_like_mojibake(name: str) -> bool:
    """
    判断文件名是否疑似「UTF-8 字节被按 CP437 误解码」产生的乱码。
    判据：原名字里基本没有 CJK，但按 CP437 还原后能解出 CJK。
    这样既不会误判正常中文/日文名，也能抓住含重音字母的乱码（如 µùÑµ£¼）。
    """
    if _cjk_ratio(name) > 0.1:
        return False
    return fix_mojibake_name(name) is not None


def fix_mojibake_tree(root: str, log=lambda s: None,
                      cancel: threading.Event | None = None,
                      dry_run: bool = False) -> int:
    """自底向上重命名乱码文件/目录。返回修复数量。"""
    fixed_count = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        for name in filenames + dirnames:
            if not looks_like_mojibake(name):
                continue
            fixed = fix_mojibake_name(name)
            if not fixed:
                continue
            src = os.path.join(dirpath, name)
            dst = os.path.join(dirpath, fixed)
            if os.path.exists(dst):
                log(f"  ⚠ 跳过（目标已存在）：{name}")
                continue
            log(f"  {'预览' if dry_run else '修复'}：{name}  →  {fixed}")
            if not dry_run:
                try:
                    os.rename(src, dst)
                    fixed_count += 1
                except OSError as e:
                    log(f"  ✗ 重命名失败 {name}: {e}")
            else:
                fixed_count += 1
    return fixed_count


# ---------------------------------------------------------------- 批量任务

def safe_dirname(stem: str) -> str:
    """把包名变成安全的输出目录名。"""
    bad = '/\\:*?"<>|\n\r\t'
    out = "".join(("_" if c in bad else c) for c in stem).strip(" .")
    return out or "extracted"


def output_stem(filename: str) -> str:
    """
    由压缩包文件名推导输出目录名，正确剥掉复合扩展名。

    os.path.splitext 只去掉最后一段（'a.tar.gz' → 'a.tar'），会让
    '归档.tar.gz' 与 '归档.tar.bz2' 撞进同一个输出目录，所以显式处理。
    """
    name = os.path.basename(filename)
    low = name.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz",
                ".tgz", ".tbz", ".tbz2", ".txz", ".tzst"):
        if low.endswith(ext):
            return safe_dirname(name[: -len(ext)])
    stem, _ = os.path.splitext(name)
    return safe_dirname(stem or name)


def find_archives(root: str, recursive: bool = False) -> list[str]:
    """在目录中找出所有（按魔数判断的）压缩包。"""
    res: list[str] = []
    if recursive:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if not d.startswith(".")]
            for fn in fns:
                fp = os.path.join(dp, fn)
                if sniff(fp)[0] not in ("unknown", "empty"):
                    res.append(fp)
    else:
        for fn in sorted(os.listdir(root)):
            fp = os.path.join(root, fn)
            if os.path.isfile(fp) and sniff(fp)[0] not in ("unknown", "empty"):
                res.append(fp)
    return sorted(res)


def expand_inputs(paths: list[str], recursive: bool = False,
                  log=lambda s: None) -> list[str]:
    """把「文件 + 目录」混合输入展开成压缩包文件列表（去重）。"""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            found = find_archives(p, recursive=recursive)
            log(f"目录 {os.path.basename(p) or p}：发现 {len(found)} 个压缩包")
            out.extend(found)
        elif os.path.isfile(p):
            out.append(p)
        else:
            log(f"⚠ 不存在：{p}")
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            uniq.append(ap)
    return uniq


def cleanup_partial(dest: str, log=lambda s: None) -> None:
    """
    解压失败后清理残留：
      · 0 字节文件（zipfile 密码错时会先建文件再失败）
      · 只剩空目录的层级
    除非整个目录变成空，否则不动非空内容。
    """
    if not os.path.isdir(dest):
        return
    removed_files = 0
    try:
        for dirpath, dirnames, filenames in os.walk(dest, topdown=False):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    if not os.path.islink(fp) and os.path.getsize(fp) == 0:
                        os.unlink(fp)
                        removed_files += 1
                except OSError:
                    pass
            if not os.listdir(dirpath):
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
        if not os.path.isdir(dest):
            log(f"  已清理失败残留：{dest}")
        elif not os.listdir(dest):
            os.rmdir(dest)
            log(f"  已清理空目录 {dest}")
        elif removed_files:
            log(f"  已清理 {removed_files} 个 0 字节残留文件")
    except OSError:
        pass


# ---------------------------------------------------------------- 自检 main

def _self_test() -> int:
    print("== 后端探测 ==")
    for it in backend_report():
        print(f"  {'✓' if it['ok'] else '✗'} {it['tool']:<8} {it['role']}  {it['path'] or ''}")
    miss = missing_brew_packages()
    if miss:
        print("  缺失可安装：", brew_install_command(miss))
    print("== 魔数自检 ==")
    import tempfile
    cases = {
        b"Rar!\x1a\x07\x01\x00": "rar5",
        b"Rar!\x1a\x07\x00": "rar",
        b"7z\xbc\xaf\x27\x1c": "7z",
        b"PK\x03\x04": "zip",
        b"\x1f\x8b": "gzip",
        b"BZh9": "bzip2",
        b"\xfd7zXZ\x00": "xz",
        b"not an archive at all": "unknown",
    }
    bad = 0
    for data, expect in cases.items():
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data + b"\x00" * 600)
            tmp = f.name
        got, desc = sniff(tmp)
        os.unlink(tmp)
        flag = "✓" if got == expect else "✗"
        if got != expect:
            bad += 1
        print(f"  {flag} {expect:<8} -> {got} ({desc})")
    print("== 乱码检测与还原自检 ==")
    # 真实机制：UTF-8 字节被按 CP437 误解码 → 乱码；工具应能识别并还原
    orig = "日本語ファイル.txt"
    fake = orig.encode("utf-8").decode("cp437")
    got_fake = looks_like_mojibake(fake)
    back = fix_mojibake_name(fake)
    ok_fake = got_fake and back == orig
    if not ok_fake:
        bad += 1
    print(f"  {'✓' if got_fake else '✗'} 识别乱码：{fake!r}")
    print(f"  {'✓' if back == orig else '✗'} 还原结果：{back!r}")
    for legit in ("中文名.txt", "日本語.txt", "readme.txt"):
        mis = looks_like_mojibake(legit)
        if mis:
            bad += 1
        print(f"  {'✓' if not mis else '✗'} 正常名不误判：{legit}")
    print("自检结果：", "全部通过" if bad == 0 else f"{bad} 项失败")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_self_test())
