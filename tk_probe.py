#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tk_probe.py —— Tk 渲染对照实验（最小复现）

开两个窗口：
  窗口 A：只用 Tk 默认主题、默认字体的最小界面（不做任何自定义）
  窗口 B：和 App 一样用 clam + 自定义字体

如果 A 画得出来、B 画不出来 → 是自定义样式的问题（我改 App）
如果两个都画不出来 → 是这个 Tk 构建在你这台机器上根本画不出控件，
                      该换后端（装 Tk 8.6 或用原生对话框做界面）

用法：python3 tk_probe.py
"""

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

FAMILIES = None


def probe_fonts():
    global FAMILIES
    FAMILIES = set(tkfont.families())


def window_a():
    """A：完全默认设置。"""
    w = tk.Toplevel()
    w.title("A · Tk 默认主题（对照）")
    w.geometry("520x300+60+80")
    tk.Label(w, text="A 窗口：这是 Tk 默认样式的文字",
             font=("Helvetica", 16)).pack(pady=16)
    tk.Button(w, text="默认按钮", font=("Helvetica", 14)).pack(pady=8)
    ttk.Button(w, text="ttk 按钮（默认主题）").pack(pady=8)
    tk.Entry(w, width=30).pack(pady=8)
    tk.Text(w, height=4).pack(fill="both", expand=True, padx=10, pady=10)
    return w


def window_b():
    """B：clam + 自定义字体 + 显式配色（和 App 相同做法）。"""
    w = tk.Toplevel()
    w.title("B · clam + 自定义字体（和 App 相同）")
    w.geometry("520x300+620+80")
    st = ttk.Style(w)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    fam = next((f for f in ("PingFang SC", "Heiti SC", "STHeiti", "Helvetica")
                if FAMILIES is None or f in FAMILIES), "Helvetica")
    f_ui = tkfont.Font(root=w, family=fam, size=13)
    f_big = tkfont.Font(root=w, family=fam, size=18, weight="bold")
    st.configure(".", background="#24262b", foreground="#f0f0f2", font=f_ui)
    st.configure("TLabel", background="#24262b", foreground="#f0f0f2")
    st.configure("Big.TLabel", font=f_big)
    st.configure("TButton", background="#33373d", foreground="#f0f0f2")
    w.configure(background="#24262b")
    ttk.Label(w, text="B 窗口：clam + PingFang SC", style="Big.TLabel").pack(pady=16)
    ttk.Button(w, text="clam 按钮").pack(pady=8)
    ttk.Entry(w, width=30).pack(pady=8)
    ttk.Treeview(w, columns=("a",), show="headings", height=3).pack(
        fill="both", expand=True, padx=10, pady=10)
    return w


def main() -> int:
    root = tk.Tk()
    root.title("Tk 渲染对照实验 · 请把两个窗口都截进来")
    root.geometry("1080x420+60+420")
    probe_fonts()

    info = tk.Text(root, height=8, wrap="word", font=("Menlo", 12))
    info.pack(fill="both", expand=True, padx=10, pady=10)
    info.insert("end", f"Tk 版本: {root.tk.call('info', 'patchlevel')}\n")
    info.insert("end", f"Python : {sys.version.split()[0]}\n")
    info.insert("end", f"字体数 : {len(FAMILIES) if FAMILIES else 0}\n")
    try:
        import subprocess
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                           capture_output=True, text=True, timeout=2)
        info.insert("end", f"系统外观: {r.stdout.strip() or 'Light'}\n")
    except Exception as e:
        info.insert("end", f"系统外观: 读取失败 {e}\n")
    info.insert("end", "\n如果你能看清这段文字，说明 Tk 的 Text 控件是正常渲染的。\n")
    info.insert("end", "请把 A、B 两个窗口连同本窗口一起截图。\n")
    info.configure(state="disabled")

    window_a()
    window_b()

    root.lift()
    root.focus_force()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
