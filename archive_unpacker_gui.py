#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archive_unpacker_gui.py —— 拖拽式解压缩工具（macOS / Tkinter）

界面自上而下：
  ① 依赖状态条（缺 unar / 7zz 时一键 brew 安装）
  ② 工具栏：添加文件 / 添加文件夹 / 移除 / 清空
  ③ 压缩包队列：真实格式（扩展名骗人也会标出）、加密、输出目录、状态
  ④ 设置：密码、输出位置、勾选项
  ⑤ 日志与进度

支持把文件或文件夹直接拖到队列区域；拖不进来时点击队列即可选择文件。
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unpack_core as U  # noqa: E402

APP_TITLE = "解压缩工具 · Archive Unpacker"


# ------------------------------------------------ 外观：交给 macOS 系统风格
# 原则：用系统 aqua 主题 + 系统 UI 字体 + macOS 语义色，
#       浅色/深色外观由系统决定，本程序不自己画一套配色。

def sys_color(root, name: str, fallback: str) -> str:
    """取 macOS 语义色；不支持时退回指定颜色（例如 Tk 8.5）。"""
    try:
        root.winfo_rgb(name)
        return name
    except tk.TclError:
        return fallback


def system_appearance_dark() -> bool:
    """
    读系统外观：深色返回 True，读不到就当浅色。
    可用环境变量 UNPACKER_FORCE_APPEARANCE=light|dark 强制覆盖（测试用）。
    """
    forced = os.environ.get("UNPACKER_FORCE_APPEARANCE", "").lower()
    if forced in ("light", "dark"):
        return forced == "dark"
    try:
        import subprocess
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                           capture_output=True, text=True, timeout=2)
        return r.returncode == 0 and "Dark" in r.stdout
    except Exception:
        return False


# 状态色：跟随外观给两套（红/绿/橙在深浅底上都够清晰）
STATUS_COLORS = {
    False: {"ok": "#0b7a33", "warn": "#9a5b00", "err": "#c02a2a",
            "run": "#0b5fbe", "muted": "#6d737b"},
    True:  {"ok": "#4fd07a", "warn": "#f0b354", "err": "#ff7b72",
            "run": "#6ab0ff", "muted": "#9aa2ac"},
}


class Job:
    __slots__ = ("path", "kind", "desc", "ext_kind", "encrypted", "dest",
                 "status", "detail", "result", "iid")

    def __init__(self, path: str):
        self.path = path
        self.kind, self.desc = U.sniff(path)
        self.ext_kind = U.sniff_ext(path)[0]
        self.encrypted = None          # 惰性探测
        self.dest = ""
        self.status = "待解压"
        self.detail = ""
        self.result = None
        self.iid = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def format_text(self) -> str:
        """真实格式；扩展名与真实格式不符时明确提示。"""
        real = U.EXT_HINT.get(self.kind, "")
        if self.kind in ("unknown", "empty"):
            return f"⚠ {self.desc}"
        if self.ext_kind != self.kind:
            fake = os.path.splitext(self.name)[1] or "(无扩展名)"
            return f"{self.desc}（真 {real}，扩展名 {fake} 不符）"
        return self.desc


# ---------------------------------------------------------------- 主界面

class App:
    def __init__(self, root: tk.Tk, on_batch_done=None, silent: bool = False):
        self.root = root
        self.jobs: list[Job] = []
        self.cancel_evt = threading.Event()
        self.worker: threading.Thread | None = None
        self.msgq: queue.Queue = queue.Queue()
        # 自动化测试用钩子（正常运行时为 None）
        self.on_batch_done = on_batch_done
        # 静默模式：不弹任何对话框（无人值守测试用，否则会卡在「解压完成」上）
        self.silent = silent

        root.title(APP_TITLE)
        root.geometry("1100x830")
        root.minsize(940, 680)

        try:
            self.tk_ver = float(str(root.tk.call("info", "patchlevel")).split(".")[0])
        except Exception:
            self.tk_ver = 8.5
        self.dark = system_appearance_dark()
        self.S = dict(STATUS_COLORS[self.dark])
        self.C = {
            "bg": sys_color(root, "systemWindowBackgroundColor", "#f0f0f0"),
            "fg": sys_color(root, "systemTextColor", "#000000"),
            "log_bg": sys_color(root, "systemWindowBackgroundColor", "#ffffff"),
            "log_fg": sys_color(root, "systemTextColor", "#000000"),
            "sel": sys_color(root, "systemSelectedTextBackgroundColor", "#b3d7ff"),
            "sel_fg": sys_color(root, "systemSelectedTextColor", "#000000"),
        }
        self._setup_fonts()
        self._setup_style()
        self._build_ui()
        self._refresh_deps()
        self._setup_dnd()
        self._activate_window()

        self.root.after(80, self._pump)
        self.log("就绪。把压缩包（或整个文件夹）拖进队列，或点「添加文件 / 添加文件夹」。")
        self.log("提示：扩展名可以是假的——本工具按文件头识别真实格式。")

    # ---------------------------------------------------------- 样式

    def _setup_fonts(self) -> None:
        """
        用 macOS 系统 UI 字体（.AppleSystemUIFont），字号按系统习惯取小一号。
        Tk 9 支持 'system' / '.AppleSystemUIFont'；老版本退回 Helvetica。
        """
        try:
            fam = "system" if self.tk_ver >= 9.0 else "Helvetica Neue"
            probe = tkfont.Font(root=self.root, family=fam, size=13)
            actual = probe.actual("family")
            if not actual:
                raise tk.TclError("无效字体")
        except tk.TclError:
            fam, actual = "Helvetica Neue", "Helvetica Neue"
        self.font_family = actual
        self.f_ui = tkfont.Font(root=self.root, family=fam, size=13)
        self.f_ui_sm = tkfont.Font(root=self.root, family=fam, size=12)
        self.f_ui_bold = tkfont.Font(root=self.root, family=fam, size=13, weight="bold")
        self.f_title = tkfont.Font(root=self.root, family=fam, size=16, weight="bold")
        mono = "Menlo"
        try:
            if not tkfont.Font(root=self.root, family=mono, size=12).actual("family"):
                mono = "Courier"
        except tk.TclError:
            mono = "Courier"
        self.f_mono = tkfont.Font(root=self.root, family=mono, size=11)

    def _setup_style(self) -> None:
        """
        用系统 aqua 主题：控件外观、颜色全部由 macOS 决定，深浅外观自动适配。
        这里只调字体、行高和一点内边距，不强行改颜色。
        Tk 9 的 aqua 支持深色模式；Tk 8.5 不支持，会退回自带配色。
        """
        st = ttk.Style(self.root)
        self.theme = "aqua" if "aqua" in st.theme_names() else st.theme_use()
        try:
            st.theme_use("aqua")
        except tk.TclError:
            pass
        # 让所有控件默认用系统 UI 字体
        # 注意：nametofont(..., root=) 是 Python 3.10+ 才有的参数，
        # 系统 Python 3.9 会 TypeError，所以逐个兼容处理。
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkTooltipFont"):
            try:
                try:
                    f = tkfont.nametofont(name, root=self.root)
                except TypeError:
                    f = tkfont.nametofont(name)
                f.configure(family=self.font_family, size=13)
            except (tk.TclError, TypeError):
                pass
        st.configure(".", font=self.f_ui)
        st.configure("TLabel", font=self.f_ui)
        st.configure("TButton", font=self.f_ui, padding=(8, 3))
        st.configure("TCheckbutton", font=self.f_ui)
        st.configure("TRadiobutton", font=self.f_ui)
        st.configure("TEntry", font=self.f_ui, padding=2)
        st.configure("TLabelframe", font=self.f_ui)
        st.configure("TLabelframe.Label", font=self.f_ui)
        st.configure("Treeview", font=self.f_ui_sm, rowheight=24)
        st.configure("Treeview.Heading", font=self.f_ui_sm)
        st.configure("Title.TLabel", font=self.f_title)
        st.configure("Hint.TLabel", font=self.f_ui_sm,
                     foreground=self.S["muted"])
        st.configure("Warn.TLabel", font=self.f_ui_sm, foreground=self.S["warn"])
        st.configure("Ok.TLabel", font=self.f_ui_sm, foreground=self.S["ok"])
        st.configure("Big.TButton", font=self.f_ui, padding=(12, 5))
        st.configure("Go.TButton", font=self.f_ui_bold, padding=(16, 6))

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}
        # 纵向伸缩只给队列区与日志区；队列权重更高，日志保留足够高度
        self.root.rowconfigure(2, weight=3, minsize=240)
        self.root.rowconfigure(7, weight=1, minsize=170)
        self.root.columnconfigure(0, weight=1)
        C, S = self.C, self.S

        # ① 标题 + 依赖状态
        head = ttk.Frame(self.root, style="TFrame")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(head, text="🧰 " + APP_TITLE, style="Title.TLabel").pack(side="left")
        self.dep_label = ttk.Label(head, text="检测依赖中…", style="Hint.TLabel")
        self.dep_label.pack(side="right")
        self.dep_btn = ttk.Button(head, text="一键安装缺失依赖", style="TButton",
                                  command=self._install_deps)
        self.dep_btn.state(["disabled"])
        self.dep_btn.pack(side="right", padx=8)

        # ② 工具栏
        bar = ttk.Frame(self.root, style="TFrame")
        bar.grid(row=1, column=0, sticky="ew", padx=12)
        ttk.Button(bar, text="＋ 添加文件", style="Big.TButton",
                   command=self.add_files).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="＋ 添加文件夹（批量）", style="Big.TButton",
                   command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="移除所选", style="TButton",
                   command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="清空队列", style="TButton",
                   command=self.clear_queue).pack(side="left", padx=6)
        ttk.Button(bar, text="预览内容", style="TButton",
                   command=self.preview).pack(side="left", padx=6)
        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="文件夹含子目录", variable=self.recursive_var,
                        style="TCheckbutton").pack(side="left", padx=10)

        # ③ 队列
        mid = ttk.Frame(self.root, style="TFrame")
        mid.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 0))
        cols = ("format", "enc", "dest", "status", "detail")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings",
                                 selectmode="extended", height=9, style="Treeview")
        self.tree.heading("#0", text="压缩包")
        self.tree.heading("format", text="真实格式")
        self.tree.heading("enc", text="加密")
        self.tree.heading("dest", text="输出目录")
        self.tree.heading("status", text="状态")
        self.tree.heading("detail", text="详情")
        self.tree.column("#0", width=290, minwidth=160)
        self.tree.column("format", width=250, minwidth=150)
        self.tree.column("enc", width=60, minwidth=50, anchor="center")
        self.tree.column("dest", width=250, minwidth=120)
        self.tree.column("status", width=110, minwidth=80, anchor="center")
        self.tree.column("detail", width=260, minwidth=120)
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview,
                           style="TScrollbar")
        hs = ttk.Scrollbar(mid, orient="horizontal", command=self.tree.xview,
                           style="TScrollbar")
        self.tree.configure(yscroll=vs.set, xscroll=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        self.tree.tag_configure("ok", foreground=S["ok"])
        self.tree.tag_configure("fail", foreground=S["err"])
        self.tree.tag_configure("warn", foreground=S["warn"])
        self.tree.tag_configure("run", foreground=S["run"])
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-2>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_double_click)

        self.drop_hint = ttk.Label(
            self.root, style="Hint.TLabel",
            text="拖拽提示：可直接把压缩包/文件夹拖到上面的列表；若系统不支持拖拽，点「添加文件 / 添加文件夹」即可。双击某行可打开它的输出目录。")
        self.drop_hint.grid(row=3, column=0, sticky="ew", padx=12)

        # ④ 设置区
        cfg = ttk.LabelFrame(self.root, text="设置", padding=10, style="TLabelframe")
        cfg.grid(row=4, column=0, sticky="ew", padx=12, pady=8)
        cfg.columnconfigure(4, weight=1)

        ttk.Label(cfg, text="密码：", style="TLabel").grid(row=0, column=0, sticky="w")
        self.pw_var = tk.StringVar(value="")
        self.pw_entry = ttk.Entry(cfg, textvariable=self.pw_var, width=26,
                                  show="•", style="TEntry")
        self.pw_entry.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.show_pw = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg, text="显示", variable=self.show_pw, style="TCheckbutton",
                        command=self._toggle_pw).grid(row=0, column=2, sticky="w")
        ttk.Label(cfg, text="留空则自动尝试 passwords.txt 与常见密码",
                  style="Hint.TLabel").grid(row=0, column=3, columnspan=3,
                                            sticky="w", padx=8)

        ttk.Label(cfg, text="输出位置：", style="TLabel").grid(row=1, column=0,
                                                              sticky="w", pady=(8, 0))
        self.out_mode = tk.StringVar(value="beside")
        ttk.Radiobutton(cfg, text="与压缩包同目录（每个包一个子目录）",
                        variable=self.out_mode, value="beside",
                        style="TRadiobutton").grid(row=1, column=1, columnspan=2,
                                                   sticky="w", pady=(8, 0))
        ttk.Radiobutton(cfg, text="指定目录：", variable=self.out_mode,
                        value="custom", style="TRadiobutton").grid(
            row=1, column=3, sticky="w", pady=(8, 0))
        self.out_var = tk.StringVar(value=os.path.expanduser("~/Downloads"))
        ttk.Entry(cfg, textvariable=self.out_var, style="TEntry").grid(
            row=1, column=4, sticky="ew", pady=(8, 0), padx=(0, 6))
        ttk.Button(cfg, text="选择…", style="TButton",
                   command=self.choose_out).grid(row=1, column=5, pady=(8, 0))
        cfg.columnconfigure(4, weight=1)

        ttk.Label(cfg, text="选项：", style="TLabel").grid(row=2, column=0,
                                                          sticky="w", pady=(8, 0))
        self.verify_var = tk.BooleanVar(value=True)
        self.fixname_var = tk.BooleanVar(value=True)
        self.open_var = tk.BooleanVar(value=True)
        opts = ttk.Frame(cfg, style="TFrame")
        opts.grid(row=2, column=1, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Checkbutton(opts, text="解压后生成 SHA256/MD5 校验清单",
                        variable=self.verify_var,
                        style="TCheckbutton").pack(side="left")
        ttk.Checkbutton(opts, text="自动修复中日文乱码文件名",
                        variable=self.fixname_var,
                        style="TCheckbutton").pack(side="left", padx=12)
        ttk.Checkbutton(opts, text="完成后在访达中打开输出目录",
                        variable=self.open_var,
                        style="TCheckbutton").pack(side="left")

        # ⑤ 动作 + 进度
        act = ttk.Frame(self.root, style="TFrame")
        act.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.go_btn = ttk.Button(act, text="▶ 开始解压", style="Go.TButton",
                                 command=self.start)
        self.go_btn.pack(side="left")
        self.cancel_btn = ttk.Button(act, text="■ 取消", style="TButton",
                                     command=self.cancel)
        self.cancel_btn.state(["disabled"])
        self.cancel_btn.pack(side="left", padx=8)
        ttk.Button(act, text="复制等效命令", style="TButton",
                   command=self.copy_command).pack(side="left", padx=8)
        self.status_label = ttk.Label(act, text="队列 0 个", style="Hint.TLabel")
        self.status_label.pack(side="right")

        self.prog = ttk.Progressbar(self.root, mode="determinate", maximum=100,
                                    style="TProgressbar")
        self.prog.grid(row=6, column=0, sticky="ew", padx=12)

        # ⑥ 日志
        logf = ttk.LabelFrame(self.root, text="日志", padding=6, style="TLabelframe")
        logf.grid(row=7, column=0, sticky="nsew", padx=12, pady=(6, 12))
        self.log_text = tk.Text(logf, height=12, wrap="none", font=self.f_mono,
                                background=C["log_bg"], foreground=C["log_fg"],
                                insertbackground=C["log_fg"], relief="flat",
                                borderwidth=0, highlightthickness=0,
                                selectbackground=C["sel"],
                                selectforeground=C["sel_fg"])
        lvs = ttk.Scrollbar(logf, orient="vertical", command=self.log_text.yview,
                            style="TScrollbar")
        lhs = ttk.Scrollbar(logf, orient="horizontal", command=self.log_text.xview,
                            style="TScrollbar")
        self.log_text.configure(yscrollcommand=lvs.set, xscrollcommand=lhs.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lvs.grid(row=0, column=1, sticky="ns")
        lhs.grid(row=1, column=0, sticky="ew")
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)
        self.log_text.tag_configure("err", foreground=S["err"])
        self.log_text.tag_configure("ok", foreground=S["ok"])
        self.log_text.tag_configure("warn", foreground=S["warn"])
        self.log_text.tag_configure("info", foreground=S["run"])
        self.log_text.configure(state="disabled")

        self.root.bind("<Command-Return>", lambda e: self.start())
        self.root.bind("<Command-o>", lambda e: self.add_files())

    # ---------------------------------------------------------- 依赖

    def _refresh_deps(self) -> None:
        report = U.backend_report()
        self.dep_report = report
        have = [i["tool"] for i in report if i["ok"]]
        miss = U.missing_brew_packages()
        self.unpack_ready = bool(U.find_tool("unar") or U.find_tool("7zz")
                                 or U.find_tool("unrar") or U.find_tool("bsdtar"))
        txt = "可用后端：" + ("、".join(have) if have else "无")
        if miss:
            txt += "　⚠ 可补装：" + " ".join(miss)
            self.dep_label.configure(text=txt, style="Warn.TLabel")
            self.dep_btn.state(["!disabled"])
            self.log("依赖检查：" + txt, "warn")
            self.log("  安装命令：" + U.brew_install_command(miss))
        else:
            self.dep_label.configure(text=txt + "　✓ 依赖齐全", style="Ok.TLabel")
            self.dep_btn.state(["disabled"])
            self.log("依赖检查：" + txt + " ✓")

    def _install_deps(self) -> None:
        pkgs = U.missing_brew_packages()
        if not pkgs:
            self._refresh_deps()
            return
        cmd = U.brew_install_command(pkgs)
        if not U.find_tool("brew"):
            messagebox.showwarning(
                "未找到 Homebrew",
                "系统里没有 brew。请先安装 Homebrew（https://brew.sh），\n"
                "或者在终端手动安装：" + " ".join(pkgs))
            return
        if not messagebox.askokcancel("安装依赖",
                                      f"将执行：\n\n{cmd}\n\n继续？"):
            return
        self.log(f"$ {cmd}", "info")

        def work():
            try:
                p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1)
                for line in p.stdout:
                    self.log("  " + line.rstrip())
                rc = p.wait()
                self.log(f"brew 退出码 {rc}", "ok" if rc == 0 else "err")
            except Exception as e:
                self.log(f"安装失败：{e}", "err")
            self.msgq.put(("redeps", None))

        self.dep_btn.state(["disabled"])
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- 拖拽

    def _activate_window(self) -> None:
        """
        主动把窗口抬到最前并取得焦点。
        不这样做时，macOS 上 Tk 8.5 的窗口可能只画出进度条/滚动条，
        其余控件要等用户点一下才重绘（看起来就是「一片空白」）。
        """
        try:
            self.root.update_idletasks()
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.root.after(150, self.root.lift)
            self.root.after(150, self.root.focus_force)
        except tk.TclError:
            pass

    def _setup_dnd(self) -> None:
        """尽力启用拖拽：先用 macOS 原生 OpenDocument，再退到 Tk 的 dnd。"""
        self.dnd_ok = False
        # 方案 A：macOS 原生（Finder 拖到 Dock/窗口图标或 tk 窗口）
        try:
            self.root.tk.call("tk::mac::OpenDocument", self.root._w, ())
            self.root.createcommand("::tk::mac::OpenDocument", self._mac_open)
            self.dnd_ok = True
            self.log("拖拽：已启用 macOS 原生文件拖放（拖到本窗口图标或列表上）。")
        except tk.TclError:
            pass
        # 方案 B：Tk 内置 dnd（同应用内/部分平台有效）
        try:
            import tkinter.dnd as dnd

            def drop_handler(event):
                data = getattr(event, "data", "")
                paths = self._split_dnd(data)
                if paths:
                    self.add_paths(paths)

            def enter(event):
                return "copy"

            self.tree.dnd_bind("<<Drop>>", drop_handler)
            self.tree.dnd_bind("<<DropEnter>>", enter)
            self.dnd_ok = True
        except Exception:
            pass
        if not self.dnd_ok:
            self.drop_hint.configure(
                text="当前 Tk 不支持拖拽：请点「添加文件 / 添加文件夹」。（双击行可打开输出目录）")

    def _mac_open(self, *paths) -> None:
        cleaned = [p for p in paths if isinstance(p, str) and os.path.exists(p)]
        if cleaned:
            self.add_paths(cleaned)

    def _split_dnd(self, data: str) -> list[str]:
        if not data:
            return []
        raw = data.replace("file://", "").replace("\r", " ").replace("\n", " ")
        parts = [p for chunk in raw.split() for p in [chunk]]
        # 处理带空格的路径：优先整体判断
        cands = [data.replace("file://", "").strip()]
        cands += parts
        out = []
        for c in cands:
            c = c.strip("{}").strip()
            if c and os.path.exists(c):
                out.append(c)
        return out

    # ---------------------------------------------------------- 队列操作

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择压缩包（可多选，扩展名不影响识别）",
            initialdir=os.path.expanduser("~/Downloads"))
        if paths:
            self.add_paths(list(paths))

    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="选择包含压缩包的文件夹（批量解压）",
                                    initialdir=os.path.expanduser("~/Downloads"))
        if d:
            self.add_paths([d])

    def add_paths(self, paths: list[str]) -> None:
        self.log(f"展开输入：{len(paths)} 项")
        found = U.expand_inputs(paths, recursive=self.recursive_var.get(),
                                log=lambda s: self.log(s))
        added = 0
        existing = {j.path for j in self.jobs}
        for fp in found:
            if fp in existing:
                continue
            job = Job(fp)
            self.jobs.append(job)
            self._insert_row(job)
            added += 1
        self.log(f"新增 {added} 个压缩包（队列共 {len(self.jobs)} 个）",
                 "ok" if added else "warn")
        self._update_status()
        if added:
            self._probe_async([j for j in self.jobs if j.encrypted is None])

    def _insert_row(self, job: Job) -> None:
        job.iid = self.tree.insert(
            "", "end", text="  " + job.name,
            values=(job.format_text, "?", job.dest or "—", job.status, job.detail),
            tags=())
        if not job.dest:
            job.dest = self._dest_for(job)
            self.tree.set(job.iid, "dest", job.dest)

    def _dest_for(self, job: Job) -> str:
        """
        计算输出目录，两重避让：
          1. 磁盘上已存在且非空 → 加序号，绝不覆盖
          2. 同批队列里已被别的任务占用 → 也加序号
             （例如 归档.tar.gz 与 归档.tar.bz2 会推导出同一个目录名）
        """
        folder = U.output_stem(job.name)
        base = (os.path.dirname(job.path) if self.out_mode.get() == "beside"
                else (self.out_var.get().strip() or os.path.expanduser("~/Downloads")))
        dest = os.path.join(base, folder)
        taken = {j.dest for j in self.jobs if j is not job and j.dest}

        def occupied(p: str) -> bool:
            if p in taken:
                return True
            try:
                return os.path.exists(p) and bool(os.listdir(p))
            except OSError:
                return False

        if occupied(dest):
            i = 2
            while occupied(f"{dest} ({i})"):
                i += 1
            dest = f"{dest} ({i})"
        return dest

    def refresh_dests(self) -> None:
        for j in self.jobs:
            if j.status in ("解压中", "完成"):
                continue
            j.dest = self._dest_for(j)
            self.tree.set(j.iid, "dest", j.dest)

    def choose_out(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_var.set(d)
            self.out_mode.set("custom")
            self.refresh_dests()

    def remove_selected(self) -> None:
        for iid in self.tree.selection():
            for j in list(self.jobs):
                if j.iid == iid and j.status not in ("解压中",):
                    self.jobs.remove(j)
                    self.tree.delete(iid)
        self._update_status()

    def clear_queue(self) -> None:
        if any(j.status == "解压中" for j in self.jobs):
            messagebox.showinfo("正在解压", "请先等待或点「取消」。")
            return
        self.tree.delete(*self.tree.get_children())
        self.jobs.clear()
        self.prog["value"] = 0
        self._update_status()

    def preview(self) -> None:
        sel = self._selected_jobs()
        if not sel:
            messagebox.showinfo("预览", "请先在队列中选中一个压缩包。")
            return
        for job in sel[:3]:
            self.log(f"— 预览 {job.name}（{job.desc}）", "info")
            try:
                entries = U.archive_entries(job.path, job.kind, self.pw_var.get() or None)
            except Exception as e:
                self.log(f"  读取失败：{e}", "err")
                continue
            if not entries:
                self.log("  （无法列出，可能加密或格式不支持）", "warn")
            for e in entries[:40]:
                self.log("  " + e)
            if len(entries) > 40:
                self.log(f"  …共 {len(entries)} 项")

    def _selected_jobs(self) -> list[Job]:
        sels = set(self.tree.selection())
        return [j for j in self.jobs if j.iid in sels]

    def _on_double_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        job = next((j for j in self.jobs if j.iid == row), None)
        if job and job.dest and os.path.isdir(job.dest):
            subprocess.Popen(["open", job.dest])

    def copy_command(self) -> None:
        sels = self._selected_jobs() or self.jobs[:1]
        if not sels:
            return
        job = sels[0]
        pw = self.pw_var.get()
        unar = U.find_tool("unar") or "unar"
        cmd = f'{unar} -f -D -o "{job.dest}"'
        if pw:
            cmd += f' -p "{pw}"'
        cmd += f' "{job.path}"'
        self.root.clipboard_clear()
        self.root.clipboard_append(cmd)
        self.log("已复制等效命令：\n  " + cmd, "info")

    # ---------------------------------------------------------- 加密探测

    def _probe_async(self, jobs: list[Job]) -> None:
        def work():
            for j in jobs:
                if j.encrypted is not None:
                    continue
                j.encrypted = U.is_encrypted(j.path, j.kind)
                self.msgq.put(("enc", j))
            self.msgq.put(("probe_done", None))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- 执行

    def _info(self, title: str, text: str) -> None:
        """信息提示：静默模式下只写日志，避免无人值守时卡在对话框上。"""
        self.log(f"{title}：{text}".replace("\n", " "))
        if not self.silent:
            messagebox.showinfo(title, text)

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "上一批还没结束，请稍候或点「取消」。")
            return
        if not self.jobs:
            self._info("队列为空", "请先添加压缩包。")
            return
        todo = [j for j in self.jobs if j.status != "完成"]
        if not todo:
            self._info("都已完成", "队列里的包都已解压成功；如需重解，请先「清空队列」再添加。")
            return
        self.refresh_dests()
        self.cancel_evt.clear()
        self.go_btn.state(["disabled"])
        self.cancel_btn.state(["!disabled"])
        self.prog.configure(maximum=len(todo), value=0)
        self.t_start = time.time()
        self.worker = threading.Thread(target=self._run_batch, args=(todo,), daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        self.cancel_evt.set()
        self.log("已请求取消，正在结束当前进程…", "warn")

    def _run_batch(self, todo: list[Job]) -> None:
        try:
            self._run_batch_inner(todo)
        except U.Cancelled:
            self.msgq.put(("log", ("批处理已取消", "warn")))
        except Exception:
            import traceback
            tb = traceback.format_exc()
            self.msgq.put(("log", ("批处理异常，已中止：\n" + tb, "err")))
            self.msgq.put(("log", ("（这是程序缺陷，请把上面的堆栈反馈给作者）", "warn")))
            self.msgq.put(("done", (0, len(todo), 0, 0, 0.0)))
            if self.on_batch_done is not None:
                self.msgq.put(("batch_done", None))

    def _run_batch_inner(self, todo: list[Job]) -> None:
        self.msgq.put(("log", (f"开始批处理：{len(todo)} 个包", "info")))
        pw = self.pw_var.get().strip()
        passwords = U.load_passwords(user_password=pw or None)
        if pw:
            self.msgq.put(("log", (f"密码候选：界面密码优先，共 {len(passwords)} 个", "info")))
        total_files = total_bytes = 0
        ok_n = fail_n = 0

        for idx, job in enumerate(todo, 1):
            if self.cancel_evt.is_set():
                self.msgq.put(("job", (job, "已取消", "用户取消", "warn")))
                continue
            self.msgq.put(("progress", idx - 1))
            self.msgq.put(("job", (job, "解压中", "启动后端…", "run")))
            self.msgq.put(("log", (f"[{idx}/{len(todo)}] {job.name}  →  {job.dest}", "info")))

            # 磁盘空间预检（需要大小的 1.2 倍）
            need = os.path.getsize(job.path) * 1.2
            try:
                free = shutil.disk_usage(os.path.dirname(job.dest) or "/").free
                if free < need:
                    self.msgq.put(("job", (job, "空间不足",
                                           f"需要约 {U.human_size(need)}，剩余 {U.human_size(free)}", "fail")))
                    fail_n += 1
                    continue
            except OSError:
                pass

            os.makedirs(job.dest, exist_ok=True)
            try:
                res = U.extract_archive(
                    job.path, job.dest, kind=job.kind, passwords=passwords,
                    log=lambda s: self.msgq.put(("log", (s, ""))),
                    cancel=self.cancel_evt)
            except U.Cancelled:
                self.msgq.put(("job", (job, "已取消", "用户取消", "warn")))
                U.cleanup_partial(job.dest, log=lambda s: self.msgq.put(("log", (s, ""))))
                continue

            job.result = res
            if not res["ok"]:
                fail_n += 1
                self.msgq.put(("job", (job, "失败", (res["error"] or "")[:300], "fail")))
                U.cleanup_partial(job.dest, log=lambda s: self.msgq.put(("log", (s, ""))))
                continue

            ok_n += 1
            total_files += res["files"]
            total_bytes += res["bytes"]
            detail = (f"{res['files']} 个文件 / {U.human_size(res['bytes'])}"
                      f" / {res['backend']}"
                      + (f" · 密码 {res['password']}" if res["password"] else ""))
            self.msgq.put(("job", (job, "完成", detail, "ok")))

            # 文件名乱码修复
            if self.fixname_var.get():
                try:
                    n = U.fix_mojibake_tree(
                        job.dest, log=lambda s: self.msgq.put(("log", (s, ""))),
                        cancel=self.cancel_evt)
                    if n:
                        self.msgq.put(("log", (f"  修复乱码文件名 {n} 处", "ok")))
                except U.Cancelled:
                    pass
                except Exception as e:
                    self.msgq.put(("log", (f"  乱码修复异常：{e}", "warn")))

            # 校验清单
            if self.verify_var.get():
                try:
                    m = U.verify_and_manifest(
                        job.dest, log=lambda s: self.msgq.put(("log", (s, ""))),
                        cancel=self.cancel_evt)
                    if m["hashed"]:
                        self.msgq.put(("job", (job, "完成",
                                               detail + f" · 已校验 {m['hashed']} 项", "ok")))
                except U.Cancelled:
                    pass
                except Exception as e:
                    self.msgq.put(("log", (f"  校验失败：{e}", "warn")))

            # 自动打开首个输出目录
            if self.open_var.get() and idx == 1:
                try:
                    subprocess.Popen(["open", job.dest])
                except Exception:
                    pass
            self.msgq.put(("progress", idx))

        self.msgq.put(("progress", len(todo)))
        self.msgq.put(("done", (ok_n, fail_n, total_files, total_bytes,
                                time.time() - self.t_start)))
        if self.on_batch_done is not None:
            self.msgq.put(("batch_done", None))

    # ---------------------------------------------------------- 消息泵

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "log":
                    self.log(*payload)
                elif kind == "job":
                    job, status, detail, tag = payload
                    job.status, job.detail = status, detail
                    if job.iid and self.tree.exists(job.iid):
                        self.tree.set(job.iid, "status", status)
                        self.tree.set(job.iid, "detail", detail)
                        self.tree.set(job.iid, "dest", job.dest)
                        self.tree.item(job.iid, tags=(tag,) if tag else ())
                elif kind == "enc":
                    job = payload
                    if job.iid and self.tree.exists(job.iid):
                        self.tree.set(job.iid, "enc",
                                      "🔒 是" if job.encrypted else "否")
                        self.tree.set(job.iid, "format", job.format_text)
                elif kind == "redeps":
                    self._refresh_deps()
                elif kind == "progress":
                    self.prog["value"] = payload
                elif kind == "done":
                    ok_n, fail_n, files, nbytes, secs = payload
                    self.go_btn.state(["!disabled"])
                    self.cancel_btn.state(["disabled"])
                    self._update_status()
                    self.log(f"===== 完成：成功 {ok_n}，失败 {fail_n}；"
                             f"共 {files} 个文件 / {U.human_size(nbytes)}；"
                             f"耗时 {secs:.1f}s =====",
                             "ok" if fail_n == 0 else "warn")
                    if fail_n == 0 and not self.silent:
                        messagebox.showinfo("解压完成",
                                            f"成功 {ok_n} 个压缩包\n"
                                            f"{files} 个文件 / {U.human_size(nbytes)}")
                elif kind == "batch_done":
                    # 自动化测试的退出钩子：延时到事件队列排空后触发
                    # （这个 Tk 构建里 quit() 不保证让 mainloop 返回，测试脚本
                    #   可在钩子里直接打结果并强制退出）
                    if self.on_batch_done is not None:
                        self.root.after(300, self.on_batch_done)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    # ---------------------------------------------------------- 杂项

    def _toggle_pw(self) -> None:
        self.pw_entry.configure(show="" if self.show_pw.get() else "•")

    def _update_status(self) -> None:
        done = sum(1 for j in self.jobs if j.status == "完成")
        fail = sum(1 for j in self.jobs if j.status in ("失败", "空间不足"))
        self.status_label.configure(
            text=f"队列 {len(self.jobs)} 个　完成 {done}　失败 {fail}")

    def log(self, text: str, tag: str = "") -> None:
        self.log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {text}\n", tag or ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def _screenshot(root: tk.Tk, path: str) -> str:
    """
    自截界面图（调试用）：把窗口抬到最前，用 screencapture 抓它的窗口区域。
    返回生成的文件路径。
    """
    import subprocess
    root.lift()
    root.attributes("-topmost", True)
    root.update_idletasks()
    root.after(50, lambda: None)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", path], check=False)
    root.attributes("-topmost", False)
    return path


def main() -> int:
    if sys.version_info < (3, 8):
        print("需要 Python 3.8+")
        return 2
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print("无法启动图形界面（Tk 不可用）：", e)
        print("可先运行命令行自检：python3 unpack_core.py")
        return 1
    app = App(root)

    # 调试开关：--shot 文件 [秒数]  截图后自动退出；--geom 打印控件几何
    if "--shot" in sys.argv:
        i = sys.argv.index("--shot")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else "/tmp/dsh_gui.png"
        delay = int(float(sys.argv[i + 2]) * 1000) if len(sys.argv) > i + 2 else 2500

        def shoot():
            _screenshot(root, out)
            print(f"[shot] 已保存 {out}", flush=True)
            os._exit(0)

        root.after(delay, shoot)
        root.mainloop()
        return 0

    if "--checkstyle" in sys.argv:
        def lum(hexcol: str) -> float:
            hexcol = hexcol.lstrip("#")
            r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (0, 2, 4))
            f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

        def contrast(a: str, b: str) -> float:
            la, lb = lum(a), lum(b)
            hi, lo = max(la, lb), min(la, lb)
            return (hi + 0.05) / (lo + 0.05)

        def audit():
            def rgb(name_or_hex: str):
                if name_or_hex.startswith("#"):
                    h = name_or_hex.lstrip("#")
                    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
                r, g, b = root.winfo_rgb(name_or_hex)
                return (r / 65535, g / 65535, b / 65535)

            def lum(c) -> float:
                f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
                return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

            def contrast(a: str, b: str) -> float:
                la, lb = lum(rgb(a)), lum(rgb(b))
                hi, lo = max(la, lb), min(la, lb)
                return (hi + 0.05) / (lo + 0.05)

            C, S = app.C, app.S
            log_bg = C["log_bg"]
            pairs = [
                ("正文（系统色）", C["fg"], C["bg"]),
                ("日志正文", C["log_fg"], log_bg),
                ("日志-成功", S["ok"], log_bg),
                ("日志-警告", S["warn"], log_bg),
                ("日志-错误", S["err"], log_bg),
                ("日志-信息", S["run"], log_bg),
                ("次要说明", S["muted"], C["bg"]),
            ]
            bad = 0
            print(f"Tk {root.tk.call('info', 'patchlevel')}  主题={app.theme}  "
                  f"外观={'深色' if app.dark else '浅色'}  字体={app.font_family}",
                  flush=True)
            print(f"系统语义色：bg={C['bg']} fg={C['fg']} 日志底={C['log_bg']}", flush=True)
            for name, fg, bg in pairs:
                cr = contrast(fg, bg)
                ok = cr >= 3.0
                bad += 0 if ok else 1
                print(f"  {'✓' if ok else '✗'} {name:<14} {fg} / {bg}  对比度 {cr:.2f}:1",
                      flush=True)
            ok = C["fg"] != C["bg"] and C["log_fg"] != C["log_bg"]
            bad += 0 if ok else 1
            print(f"  {'✓' if ok else '✗'} 前景与背景不同色（防止「一片黑」）", flush=True)
            os._exit(1 if bad else 0)

        root.after(1200, audit)
        root.mainloop()
        return 0

    if "--geom" in sys.argv:
        def dump_geom():
            print(f"窗口 {root.winfo_width()}x{root.winfo_height()}", flush=True)

            def walk(w, depth=0):
                print("  " * depth
                      + f"{w.winfo_class():<12} {w.winfo_width():>5}x{w.winfo_height():<5}"
                        f" ({w.winfo_x()},{w.winfo_y()}) mapped={bool(w.winfo_ismapped())}",
                      flush=True)
                for c in w.winfo_children():
                    walk(c, depth + 1)
            walk(root)
            os._exit(0)

        root.after(2500, dump_geom)
        root.mainloop()
        return 0

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
