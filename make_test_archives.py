#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_test_archives.py —— 造一批测试压缩包到指定目录（默认 /tmp/unpack_fixtures）

覆盖场景：
  · 普通 zip（UTF-8 中文/日文文件名，flag bit 11 置位）
  · “乱码 zip”：文件名以 UTF-8 字节存但未置 UTF-8 标志 → 解压后 CP437 乱码
  · 加密 zip（Python 标准库不支持写加密，需 zip -P；没有则跳过并提示）
  · tar.gz / tar.bz2
  · 伪扩展名文件：把 zip 改名成 .7z、把 tar.gz 改名成 .rar
  · 无扩展名文件
用法：python3 make_test_archives.py [输出目录]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/unpack_fixtures"

CN = "中文目录"
JP = "日本語フォルダ"
TXT_CN = "说明_中文.txt"
TXT_JP = "説明_日本語.txt"


def payload_files() -> dict[str, bytes]:
    return {
        f"{CN}/{TXT_CN}": "中文内容测试\n".encode("utf-8"),
        f"{JP}/{TXT_JP}": "日本語のテスト内容\n".encode("utf-8"),
        "readme.txt": b"plain ascii\n",
        "深层/嵌套/目录/文件.bin": bytes(range(256)) * 8,
    }


def write_zip(path: str, files: dict[str, bytes], utf8_flag: bool = True) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            if not utf8_flag:
                # 制造真实的历史乱码：zip 头里存的是 UTF-8 字节，但没置 UTF-8 标志位，
                # 解压端按 CP437 解码 → 乱码。CPython 会自动加 UTF-8 标志，
                # 所以要先把名字“降级”成 CP437 可编码的等价字符再写。
                try:
                    name = name.encode("utf-8").decode("cp437")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    pass
            info = zipfile.ZipInfo(name)
            if utf8_flag:
                info.flag_bits |= 0x800          # 声明 UTF-8
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def write_tar(path: str, files: dict[str, bytes], mode: str) -> None:
    with tarfile.open(path, mode) as t:
        for name, data in files.items():
            import io
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = 1700000000
            t.addfile(ti, io.BytesIO(data))


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    files = payload_files()
    made: list[str] = []

    # 1) 正常 zip（UTF-8 标志）
    p = os.path.join(OUT, "正常_utf8中文.zip")
    write_zip(p, files, utf8_flag=True)
    made.append(p)

    # 2) 乱码 zip：文件名是 UTF-8 字节，但未置 UTF-8 标志
    p = os.path.join(OUT, "乱码_mojibake.zip")
    write_zip(p, files, utf8_flag=False)
    made.append(p)

    # 3) 伪扩展名：zip 改名 .7z
    p = os.path.join(OUT, "伪装成7z的zip.7z")
    shutil.copyfile(made[0], p)
    made.append(p)

    # 4) tar.gz / tar.bz2
    p = os.path.join(OUT, "归档.tar.gz")
    write_tar(p, files, "w:gz")
    made.append(p)
    p = os.path.join(OUT, "归档.tar.bz2")
    write_tar(p, files, "w:bz2")
    made.append(p)

    # 5) 伪扩展名：tar.gz 改名 .rar
    p = os.path.join(OUT, "伪装成rar的targz.rar")
    shutil.copyfile(os.path.join(OUT, "归档.tar.gz"), p)
    made.append(p)

    # 6) 无扩展名
    p = os.path.join(OUT, "无扩展名压缩包")
    shutil.copyfile(made[0], p)
    made.append(p)

    # 7) 顶层散文件的 zip（校验 -D 不建外层目录）
    p = os.path.join(OUT, "散文件.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("a.txt", b"a\n")
        z.writestr("b/c.txt", b"c\n")
    made.append(p)

    # 8) 加密 zip（需要系统 zip 命令支持 -P；Info-ZIP 可以）
    ziptool = shutil.which("zip")
    if ziptool:
        src = os.path.join(OUT, "_enc_src")
        os.makedirs(os.path.join(src, CN), exist_ok=True)
        for name, data in files.items():
            fp = os.path.join(src, name)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "wb") as f:
                f.write(data)
        enc = os.path.join(OUT, "加密_密码_hello123.zip")
        rc = subprocess.call([ziptool, "-r", "-q", "-P", "hello123",
                              enc, "."], cwd=src,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc == 0:
            made.append(enc)
        else:
            print("· 加密 zip 生成失败（zip -P 不可用），跳过")
        shutil.rmtree(src, ignore_errors=True)
    else:
        print("· 系统没有 zip 命令，跳过加密 zip 用例")

    print(f"已生成 {len(made)} 个测试包 → {OUT}")
    for m in sorted(os.listdir(OUT)):
        fp = os.path.join(OUT, m)
        print(f"  {os.path.getsize(fp):>10,} B  {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
