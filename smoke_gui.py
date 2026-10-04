#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_gui.py —— 无人值守 GUI 冒烟测试

用法：
  python3 smoke_gui.py                              # 只建界面，3 秒后退出
  python3 smoke_gui.py <压缩包或目录> [密码] [输出目录]   # 真正跑一遍批量解压

它会把界面里的日志回显到终端，方便无人值守验证。

实现备注：本机 Tk 8.5 的 root.quit() 不保证让 mainloop 返回，
所以批处理结束后由 App 的 on_batch_done 钩子打印结果并 os._exit 强制退出。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))


def load_app_module():
    spec = importlib.util.spec_from_file_location(
        "auge", os.path.join(HERE, "archive_unpacker_gui.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["auge"] = mod
    spec.loader.exec_module(mod)
    return mod


def check_ui_drawn(root) -> int:
    """
    回归护栏：确认界面真的被布局/绘制出来了。
    曾经出现过「窗口只有进度条和滚动条、其余一片空白，脚本却报通过」的情况，
    所以这里逐项断言关键控件有真实尺寸且已 mapped。
    """
    print("----- 界面渲染自检 -----", flush=True)
    bad = 0
    checks = [
        ("窗口尺寸", lambda: root.winfo_width() > 400 and root.winfo_height() > 300),
        ("顶层区块数 >= 8（头部/工具栏/队列/提示/设置/动作/进度/日志）",
         lambda: len(root.winfo_children()) >= 8),
        ("各区块都已布局（无 1x1 空白块）",
         lambda: all(c.winfo_ismapped() and c.winfo_height() > 15
                     for c in root.winfo_children())),
    ]
    for name, fn in checks:
        ok = bool(fn())
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name}", flush=True)

    named = [("队列列表", app_tree(root)), ("日志框", app_log(root)),
             ("进度条", app_widget(root, "TProgressbar"))]
    for name, w in named:
        if w is None:
            print(f"  ✗ 找不到{name}", flush=True)
            bad += 1
            continue
        ok = bool(w.winfo_ismapped()) and w.winfo_width() > 20 and w.winfo_height() > 10
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name} 已绘制 "
              f"({w.winfo_width()}x{w.winfo_height()})", flush=True)
    return bad


def app_widget(root, cls: str):
    def find(w):
        if w.winfo_class() == cls:
            return w
        for c in w.winfo_children():
            r = find(c)
            if r is not None:
                return r
        return None
    return find(root)


def app_tree(root):
    return app_widget(root, "Treeview")


def app_log(root):
    return app_widget(root, "Text")


def main() -> int:
    archive = sys.argv[1] if len(sys.argv) > 1 else None
    password = sys.argv[2] if len(sys.argv) > 2 else ""
    outdir = sys.argv[3] if len(sys.argv) > 3 else os.path.join("/tmp", "gui_smoke_out")

    mod = load_app_module()
    root = tk.Tk()

    state = {"t0": time.time(), "rc": 2}

    def finish():
        """批处理钩子：打印结果并强制退出。"""
        bad = check_ui_drawn(root)
        print("----- 界面日志回放 -----", flush=True)
        print(app.log_text.get("1.0", "end").strip(), flush=True)
        print("----- 队列终态 -----", flush=True)
        rc = 1 if bad else 0
        for j in app.jobs:
            print(f"  {j.name}: {j.status} | {j.detail}", flush=True)
            if j.status != "完成":
                rc = 1
        print(f"[smoke] 用时 {time.time() - state['t0']:.1f}s  "
              f"{'通过' if rc == 0 else '存在失败项'}", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(rc)

    app = mod.App(root, on_batch_done=finish, silent=True)
    app.open_var.set(False)              # 测试时不要弹访达
    app.verify_var.set(True)
    app.fixname_var.set(True)

    if not archive:
        print("[smoke] 界面构建完成，2.5 秒后做渲染自检…", flush=True)

        def verify_only():
            bad = check_ui_drawn(root)
            print(f"[smoke] 渲染自检 {'通过' if bad == 0 else f'失败（{bad} 项）'}",
                  flush=True)
            os._exit(0 if bad == 0 else 1)

        root.after(2500, verify_only)
        root.mainloop()
        return 0

    app.out_mode.set("custom")
    app.out_var.set(outdir)
    app.pw_var.set(password)
    app.add_paths([archive])
    for j in app.jobs:                  # 省掉探测子进程，直接进入解压
        j.encrypted = bool(password)
    app.refresh_dests()
    print(f"[smoke] 队列 {len(app.jobs)} 个；输出目录 {outdir}", flush=True)
    app.start()

    # 兜底：万一钩子没触发（例如后端卡死），15 分钟后强退
    root.after(900_000, lambda: os._exit(3))
    root.mainloop()
    return state["rc"]


if __name__ == "__main__":
    sys.exit(main())
