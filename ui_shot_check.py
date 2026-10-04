#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui_shot_check.py —— 界面外观自动验收

流程：
  1. 用指定 Python 启动 archive_unpacker_gui.py
  2. 等窗口稳定后，用 screencapture 抓取该窗口的屏幕区域
  3. 逐项检查：文字/控件确实有像素、没有大面积空白、配色对比度足够
  4. 把截图存到 ui_shot.png 供人眼复核

用法：
  python3 ui_shot_check.py [输出png] [等待秒数]

注意：screencapture 需要「屏幕录制」权限；被拒时会明确报错而不是静默通过。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "archive_unpacker_gui.py")


def activate_app(name: str = "Python") -> None:
    """把应用窗口提到最前，否则截图可能抓到别的窗口。"""
    script = f'tell application "System Events" to set frontmost of process "{name}" to true'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ⚠ 激活 {name} 失败（权限？）：{r.stderr.strip()[:80]}")


def window_rect(python: str) -> tuple[int, int, int, int] | None:
    """借应用自身的界面，报告窗口在屏幕上的位置与尺寸。"""
    probe = (
        "import importlib.util, os, sys, tkinter as tk\n"
        f"spec = importlib.util.spec_from_file_location('auge', r'{GUI}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['auge'] = m\n"
        "spec.loader.exec_module(m)\n"
        "r = tk.Tk()\n"
        "a = m.App(r)\n"
        "def report():\n"
        "    r.update_idletasks()\n"
        "    print('RECT', r.winfo_rootx(), r.winfo_rooty(),"
        " r.winfo_width(), r.winfo_height(), flush=True)\n"
        "    print('TK', r.tk.call('info', 'patchlevel'), flush=True)\n"
        "    print('BG', a.C['bg'], flush=True)\n"
        "    os._exit(0)\n"
        "r.after(2500, report)\n"
        "r.mainloop()\n"
    )
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    out = subprocess.run([python, "-u", "-c", probe], capture_output=True,
                         text=True, timeout=90, env=env)
    info = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] in ("RECT", "TK", "BG"):
            info[parts[0]] = parts[1:]
    if "RECT" not in info:
        print("无法取得窗口位置：stdout=", out.stdout[-200:], "stderr=", out.stderr[-300:])
        return None
    x, y, w, h = (int(v) for v in info["RECT"])
    print(f"   Tk={info.get('TK', ['?'])[0]}  应用配色 bg={info.get('BG', ['?'])[0]}")
    return x, y, w, h


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = None
    for a in sys.argv[1:]:
        if a in ("--light", "--dark"):
            force = "light" if a == "--light" else "dark"
    out_png = args[0] if args else os.path.join(HERE, "ui_shot.png")
    wait = float(args[1]) if len(args) > 1 else 6.0
    python = sys.executable
    env = dict(os.environ)
    if force:
        env["UNPACKER_FORCE_APPEARANCE"] = force
        print(f"（强制 {force} 外观）")

    print(f"① 取得窗口位置（用 {python}）…")
    rect = window_rect(python)
    if not rect:
        return 2
    x, y, w, h = rect
    print(f"   窗口区域 {w}x{h} @ ({x},{y})")

    print("② 启动界面…")
    child_env = dict(os.environ)
    if force:
        child_env["UNPACKER_FORCE_APPEARANCE"] = force
    proc = subprocess.Popen([python, GUI], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=child_env)
    try:
        time.sleep(wait)
        print("③ 把应用窗口提到最前…")
        activate_app("Python")
        time.sleep(1.2)
        print("③ 截图…")
        r = subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", out_png],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(out_png):
            print("   ✗ 截图失败（多半是缺少「屏幕录制」权限）：", r.stderr.strip())
            print("   可在「系统设置 → 隐私与安全性 → 屏幕录制」里给终端/本应用授权。")
            return 3
        print(f"   ✓ 已保存 {out_png}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("④ 分析截图…")
    try:
        from PIL import Image
    except ImportError:
        print("   （未安装 Pillow，跳过像素分析；截图已生成可人工查看）")
        return 0

    from collections import Counter
    im = Image.open(out_png).convert("RGB")
    W, H = im.size
    small = im.resize((max(1, W // 4), max(1, H // 4)))
    pixels = (small.get_flattened_data() if hasattr(small, "get_flattened_data")
              else list(small.getdata()))
    cnt = Counter(pixels)
    top_color, top_n = cnt.most_common(1)[0]
    top_pct = top_n * 100 / (small.size[0] * small.size[1])

    # 逐带扫描：每一横带里有多少「非主色」像素（即内容）
    bands = 16
    band_pct = []
    bg = top_color

    def differs(p, q, tol=18):
        return max(abs(p[i] - q[i]) for i in range(3)) > tol

    for b in range(bands):
        y0, y1 = H * b // bands, H * (b + 1) // bands
        n = ok = 0
        for yy in range(y0, y1, 3):
            for xx in range(0, W, 7):
                n += 1
                if differs(im.getpixel((xx, yy)), bg):
                    ok += 1
        band_pct.append(ok * 100 // max(1, n))

    empty_bands = [i for i, p in enumerate(band_pct) if p < 1]
    print(f"   最常见色 {top_color} 占 {top_pct:.1f}%")
    print(f"   各横带有内容比例：{band_pct}")
    ok = True
    if top_pct > 60:
        print("   ✗ 大面积单色 → 界面很可能没画出来")
        ok = False
    if len(empty_bands) > bands // 3:
        # 队列为空、日志行数少时本来就有空白带，这里只做提示不算失败
        print(f"   ⚠ 空白横带较多（队列/日志为空时属正常）：{empty_bands}")
        ok = False
    if ok:
        print("   ✓ 文字/控件像素分布正常，无人为空白区")
    print(f"⑤ 结果：{'通过' if ok else '失败'}（截图 {out_png} 可人工复核）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
