#!/bin/bash
# 双击启动「解压缩工具」图形界面
# 用法：在访达里双击本文件即可；若提示无权限，先执行 chmod +x 启动解压缩工具.command

cd "$(dirname "$0")" || exit 1

# 优先用较新的 Tk：
#   系统 /usr/bin/python3 带的是 Tk 8.5（2009 年），在 macOS 26/27 上会出现
#   「文字与背景同色、控件画不出来」；Homebrew 的 python3.12 带 Tk 9.1，明显更可靠。
CANDIDATES="
/opt/homebrew/bin/python3.13
/opt/homebrew/bin/python3.12
/opt/homebrew/bin/python3.11
/usr/local/bin/python3.13
/usr/local/bin/python3.12
/usr/local/bin/python3.11
/usr/bin/python3
"

PY=""
PY_TK=""
for cand in $CANDIDATES $(command -v python3 2>/dev/null); do
  [ -n "$cand" ] && [ -x "$cand" ] || continue
  ver=$("$cand" -c 'import tkinter;print(tkinter.TkVersion)' 2>/dev/null) || continue
  if [ -z "$PY" ]; then
    # 第一顺位只能先记下，等看有没有更好的
    PY="$cand"; PY_TK="$ver"
  fi
  # 找到 8.6 及以上就定了
  case "$ver" in
    8.5|8.4|8.3|8.2|8.1|8.0|7.*|"") ;;
    *) PY="$cand"; PY_TK="$ver"; break ;;
  esac
done

if [ -z "$PY" ]; then
  echo "未找到可用的 Python 3 + Tkinter。"
  echo "建议执行：brew install python-tk@3.12"
  echo "（也可以只用命令行自检：python3 unpack_core.py）"
  read -r -p "按回车关闭…" _
  exit 1
fi

echo "使用 Python: $PY  (Tk $PY_TK)"

# 把终端提到前台并激活：
# macOS 上未被激活的 Tk 窗口可能只画出部分控件，点一下才全部重绘。
osascript -e 'tell application "Terminal" to activate' >/dev/null 2>&1 || true

exec "$PY" archive_unpacker_gui.py
