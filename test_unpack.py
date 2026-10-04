#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_unpack.py —— 核心逻辑单元测试（只用标准库，直接 python3 test_unpack.py）

被测：格式嗅探、批量发现、解压（zip/tar/伪扩展名）、密码尝试、乱码修复、
      SHA256/MD5 清单、错误处理（未知格式/错误密码/空密码）。
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unpack_core as U  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {extra}" if extra and not cond else ""))


def make_fixtures(root: str) -> dict[str, str]:
    fx = {}
    # 普通 zip
    p = os.path.join(root, "plain.zip")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("dir/中文名.txt", "内容\n".encode())
        z.writestr("top.txt", b"top\n")
    fx["plain_zip"] = p

    # 伪扩展名 zip（.7z）
    p2 = os.path.join(root, "fake.7z")
    shutil.copyfile(p, p2)
    fx["fake_7z"] = p2

    # tar.gz：优先用系统 tar 造（PAX 头 + UTF-8 名，最贴近真实世界），
    # 顺便验证「Python tarfile 造的包也能正确解出日文名」
    src = os.path.join(root, "tarsrc")
    os.makedirs(os.path.join(src, "sub"), exist_ok=True)
    with open(os.path.join(src, "sub", "日文.txt"), "w", encoding="utf-8") as f:
        f.write("tar payload\n")
    p3 = os.path.join(root, "t.tar.gz")
    if shutil.which("tar"):
        subprocess.check_call(["tar", "-czf", p3, "sub/日文.txt"], cwd=src,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        with tarfile.open(p3, "w:gz") as t:
            t.add(os.path.join(src, "sub", "日文.txt"), arcname="sub/日文.txt")
    fx["targz"] = p3
    p4 = os.path.join(root, "fake.rar")
    shutil.copyfile(p3, p4)
    fx["fake_rar"] = p4

    # Python tarfile 造的 tar.gz（PAX 头，unar 会乱码，bsdtar 正常）
    p3b = os.path.join(root, "py.tar.gz")
    with tarfile.open(p3b, "w:gz") as t:
        data = b"py tar\n"
        ti = tarfile.TarInfo("sub/日文py.txt")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    fx["pytargz"] = p3b

    # 顶层散文件 zip（验证 -D：不建外层目录）
    p5 = os.path.join(root, "scatter.zip")
    with zipfile.ZipFile(p5, "w") as z:
        z.writestr("a.txt", b"a\n")
        z.writestr("b/c.txt", b"c\n")
    fx["scatter"] = p5

    # 乱码 zip：UTF-8 字节 + 未置 UTF-8 标志位（CP437 解码即乱码）
    p6 = os.path.join(root, "moji.zip")
    orig = "日本語ファイル.txt"
    legacy = orig.encode("utf-8").decode("cp437")   # 得到 ’ú–{… 之类
    with zipfile.ZipFile(p6, "w") as z:
        info = zipfile.ZipInfo(legacy)
        info.flag_bits &= ~0x800                    # 明确不声明 UTF-8
        z.writestr(info, "テスト\n".encode())
    fx["mojibake"] = p6

    # 非压缩包
    p7 = os.path.join(root, "notarchive.txt")
    with open(p7, "w") as f:
        f.write("hello\n")
    fx["notarchive"] = p7
    return fx


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="unpack_test_")
    print(f"测试目录：{tmp}\n")
    fx = make_fixtures(tmp)

    print("== 1. 格式嗅探 ==")
    check("zip 识别", U.sniff(fx["plain_zip"])[0] == "zip")
    check("伪 .7z 识别为 zip", U.sniff(fx["fake_7z"])[0] == "zip",
          f"得到 {U.sniff(fx['fake_7z'])[0]}")
    check("tar.gz 识别", U.sniff(fx["targz"])[0] == "gzip")
    check("伪 .rar 识别为 gzip", U.sniff(fx["fake_rar"])[0] == "gzip")
    check("文本文件识别为 unknown", U.sniff(fx["notarchive"])[0] == "unknown")
    check("扩展名提示：.7z → 7z", U.sniff_ext(fx["fake_7z"])[0] == "7z")
    check("is_archive 判定正确",
          U.is_archive(fx["plain_zip"]) and not U.is_archive(fx["notarchive"]))

    print("== 2. 目录批量发现 ==")
    found = U.expand_inputs([tmp])
    names = sorted(os.path.basename(f) for f in found)
    check("目录扫描找出 7 个包（排除 notarchive.txt）", len(found) == 7, f"找到 {names}")
    check("排除非压缩包", fx["notarchive"] not in found)
    check("去重", len(found) == len(set(found)))
    sub = os.path.join(tmp, "subdir_deep")
    os.makedirs(sub, exist_ok=True)
    shutil.copyfile(fx["plain_zip"], os.path.join(sub, "deep.zip"))
    check("非递归不含子目录", len(U.expand_inputs([tmp])) == 7)
    check("递归包含子目录", len(U.expand_inputs([tmp], recursive=True)) == 8)

    print("== 3. 解压：zip（含中文文件名）==")
    d = os.path.join(tmp, "out_plain")
    r = U.extract_archive(fx["plain_zip"], d)
    check("解压成功", r["ok"], r.get("error", ""))
    check("中文文件名正确", os.path.exists(os.path.join(d, "dir", "中文名.txt")))
    check("文件数正确", r["files"] == 2, f"files={r['files']}")
    check("未加密时不试密码", r["password"] is None)
    check("内容一致",
          open(os.path.join(d, "dir", "中文名.txt"), "rb").read() == "内容\n".encode())

    print("== 4. 解压：伪扩展名 .7z ==")
    d = os.path.join(tmp, "out_fake")
    r = U.extract_archive(fx["fake_7z"], d)
    check("按真实格式解压成功", r["ok"], r.get("error", ""))
    check("顶层文件未多套一层目录", os.path.exists(os.path.join(d, "top.txt")))
    check("顶层无多余子目录", not any(
        os.path.isdir(os.path.join(d, x)) and x not in ("dir",) for x in os.listdir(d)))

    print("== 5. 解压：tar.gz / 伪 .rar / Python PAX 头 ==")
    d = os.path.join(tmp, "out_targz")
    r = U.extract_archive(fx["targz"], d)
    check("tar.gz 解压成功", r["ok"], r.get("error", ""))
    check("日文文件名正确（shell tar 造的 PAX 头）",
          os.path.exists(os.path.join(d, "sub", "日文.txt")),
          f"实际 {os.listdir(os.path.join(d, 'sub')) if os.path.isdir(os.path.join(d, 'sub')) else os.listdir(d)}")
    check("macOS 的 ._ 影子文件被忽略", not any(
        n.startswith("._") for n in os.listdir(os.path.join(d, "sub")))
        if os.path.isdir(os.path.join(d, "sub")) else True)
    d = os.path.join(tmp, "out_fakerar")
    r = U.extract_archive(fx["fake_rar"], d)
    check("伪 .rar（实为 tar.gz）解压成功", r["ok"], r.get("error", ""))
    d = os.path.join(tmp, "out_pytar")
    r = U.extract_archive(fx["pytargz"], d)
    check("Python tarfile(PAX) 造的包解压成功", r["ok"], r.get("error", ""))
    sub = os.path.join(d, "sub")
    got = os.listdir(sub) if os.path.isdir(sub) else []
    check("PAX 头里的日文名不乱码（unar 在 macOS 会出 ??，故应走 bsdtar）",
          "日文py.txt" in got, f"实际 {got}，后端 {r['backend']}")

    print("== 6. 错误处理 ==")
    r = U.extract_archive(fx["notarchive"], os.path.join(tmp, "out_bad"))
    check("未知格式被拒绝", not r["ok"])
    check("错误信息可读", bool(r["error"]), r["error"])

    print("== 7. 校验清单 ==")
    d = os.path.join(tmp, "out_plain")
    m = U.verify_and_manifest(d)
    check("哈希了 2 个文件", m["hashed"] == 2, f"hashed={m['hashed']}")
    sha_lines = open(m["sha256_manifest"], encoding="utf-8").read().strip().splitlines()
    md5_lines = open(m["md5_manifest"], encoding="utf-8").read().strip().splitlines()
    check("清单含表头注释", sha_lines[0].startswith("#"))
    entry = [l for l in sha_lines if l and not l.startswith("#")]
    check("SHA256 行数正确", len(entry) == 2, f"{entry}")
    # 独立复算一个
    target = os.path.join(d, "dir", "中文名.txt")
    real = hashlib.sha256(open(target, "rb").read()).hexdigest()
    check("SHA256 与独立复算一致",
          any(l.startswith(real) for l in entry), f"期望 {real[:16]}…")
    real_md5 = hashlib.md5(open(target, "rb").read()).hexdigest()
    check("MD5 与独立复算一致",
          any(l.startswith(real_md5) for l in md5_lines if not l.startswith("#")))
    check("清单行含相对路径", any("中文名.txt" in l for l in entry))

    print("== 8. 乱码文件名修复 ==")
    d = os.path.join(tmp, "out_moji")
    r = U.extract_archive(fx["mojibake"], d)
    check("乱码包解压成功", r["ok"], r.get("error", ""))
    before = os.listdir(d)
    suspect = [n for n in before if U.looks_like_mojibake(n)]
    check("检测到乱码文件名", bool(suspect), f"实际 {before}")
    check("正常中文名不会被误判", not U.looks_like_mojibake("中文名.txt"))
    check("正常日文名不会被误判", not U.looks_like_mojibake("日本語.txt"))
    fixed = U.fix_mojibake_name(suspect[0]) if suspect else None
    check("乱码可还原为日文", fixed == "日本語ファイル.txt", f"得到 {fixed!r}")
    n = U.fix_mojibake_tree(d)
    after = os.listdir(d)
    check("修复后得到正确日文名", any("日本語" in x for x in after), f"实际 {after}")
    check("修复计数 >0", n > 0)
    check("重复修复不再改动", U.fix_mojibake_tree(d) == 0)

    print("== 9. 密码尝试（需要加密 zip 用例）==")
    enc = os.path.join(tmp, "enc.zip")
    ziptool = shutil.which("zip")
    made_enc = False
    if ziptool:
        src = os.path.join(tmp, "encsrc")
        os.makedirs(os.path.join(src, "inner"), exist_ok=True)
        with open(os.path.join(src, "inner", "secret.txt"), "w") as f:
            f.write("secret\n")
        rc = subprocess.call([ziptool, "-r", "-q", "-P", "hello123", enc, "."],
                             cwd=src, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        made_enc = (rc == 0 and os.path.isfile(enc))
    if made_enc:
        check("加密 zip 被识别为加密", U.is_encrypted(enc, "zip"))
        d = os.path.join(tmp, "out_enc")
        r = U.extract_archive(enc, d, passwords=["wrong1", "hello123"])
        check("第 2 个候选密码成功", r["ok"] and r["password"] == "hello123",
              f"ok={r['ok']} pw={r['password']} err={r.get('error','')[:80]}")
        check("加密包内容正确", os.path.exists(os.path.join(d, "inner", "secret.txt")))
        d = os.path.join(tmp, "out_enc_bad")
        r = U.extract_archive(enc, d, passwords=["nope1", "nope2"])
        check("全部密码错误时返回失败", not r["ok"])
        d_none = os.path.join(tmp, "out_enc_none")
        r = U.extract_archive(enc, d_none, passwords=[])
        check("无密码可用时给出明确提示", not r["ok"] and "密码" in r["error"],
              r.get("error", ""))
        U.cleanup_partial(d_none)
        check("cleanup_partial 后不留残留（含 0 字节残file）",
              not os.path.isdir(d_none), f"实际还在：{d_none}")
        check("已解压目录不会被 cleanup_partial 影响",
              os.path.isdir(os.path.join(tmp, "out_enc", "inner")))
    else:
        print("  · 系统 zip 命令不可用，跳过密码用例")

    print("== 10. 安全与命名 ==")
    check("safe_dirname 处理斜杠", "/" not in U.safe_dirname("a/b:c?d"))
    check("output_stem 剥掉 .tar.gz", U.output_stem("归档.tar.gz") == "归档",
          U.output_stem("归档.tar.gz"))
    check("output_stem 剥掉 .tar.bz2", U.output_stem("归档.tar.bz2") == "归档")
    check("output_stem 复合扩展名会得到同名（由 GUI 负责加序号避让）",
          U.output_stem("a.tar.gz") == U.output_stem("a.tar.bz2") == "a")
    check("safe_dirname 空输入兜底", U.safe_dirname("") == "extracted")
    check("human_size", U.human_size(1536) == "1.5 KB", U.human_size(1536))
    check("load_passwords 去重且界面密码优先",
          U.load_passwords(user_password="x")[0] == "x"
          and len(set(U.load_passwords(user_password="x"))) == len(U.load_passwords(user_password="x")))

    print()
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败清单：")
        for f in FAILED:
            print("  -", f)
    if os.environ.get("KEEP_TEST_DIR") != "1":
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("测试目录保留：", tmp)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
