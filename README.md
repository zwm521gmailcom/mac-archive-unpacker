# 解压缩工具

macOS 上双击即可使用的解压和压缩工具。窗口可以拖入文件，也能粘贴密码。程序坞里的名字是「解压缩工具」。

它按文件头识别真实格式，所以扩展名对不上也能解开，例如名叫 `.7z`、实际是 RAR5 的包。解压时会把结果放到压缩包旁边的同名文件夹，并写出 SHA256 清单。压缩则把选中的文件打成一个 zip。

## 支持的格式

识别看文件头，不看扩展名。

| 格式 | 解压 | 压缩 |
| --- | --- | --- |
| RAR、RAR5 | 可以，包括加密包 | 不可以 |
| 7z | 可以，包括加密包 | 不可以 |
| ZIP | 可以，包括加密包 | 可以。密码留空是普通 zip，填写后加密 |
| TAR，以及 `.tar.gz`、`.tgz`、`.tar.bz2`、`.tar.xz`、`.tar.zst`、`.tar.lz` | 可以 | 不可以 |
| GZip、BZip2、XZ、Zstandard、Lzip | 可以 | 不可以 |
| CAB、ARJ、ISO | 可以 | 不可以 |

RAR、RAR5、7z、ZIP、CAB、ARJ、ISO 用打进应用里的 unar 解压。TAR 以及 GZip、BZip2、XZ、Zstandard、Lzip 先用系统的 bsdtar。压缩只用系统的 zip，不能生成 RAR 或 7z。

## 运行环境

macOS 14 或更高版本，以及 Swift 命令行工具。打包时还需要本机已安装 [The Unarchiver 的命令行工具](https://theunarchiver.com/)：

```bash
brew install unar
```

`unar` 和 `lsar` 会被拷进应用，打开应用本身不再依赖 Python。压缩使用系统自带的 `/usr/bin/zip`。tar 系列解压使用系统自带的 `/usr/bin/bsdtar`。

## 打开

在项目目录执行：

```bash
Scripts/package-app.sh
open 解压缩工具.app
```

自检会打一个小 zip、再解开，并确认加密 zip 能用密码解开：

```bash
swift build -c release --package-path App
App/.build/release/ArchiveUnpacker --self-test
```

看到「自检通过」即可。拷到别的 Mac 时，系统可能会拦下未签名的应用。

## 解压

窗口左上角选「解压」。

1. 把压缩包拖进窗口，或点「添加文件」「添加文件夹」。
2. 密码可以留空。留空时先试 `passwords.txt` 里的密码，再试内置常见密码。这一批里某个密码一旦成功，后面的包会先用它。
3. 点「开始解压」。标题栏会显示「第几个 / 总数」和已经写出的大小。
4. 点「停止」或按 Esc 会打断当前这个包。没解完的半截目录会删掉，已经完成的保留。

结果在压缩包旁边的同名文件夹。文件夹里已有内容时，会改用 `名字-2`、`名字-3`。原始压缩包不会被删除。

从密码管理器粘贴时，末尾换行会被去掉。

## 压缩

窗口左上角选「压缩」。

1. 把文件或文件夹拖进来，或点「添加文件」「添加文件夹」。多项会打成同一个 zip。
2. 密码留空则是普通 zip。填写后会加密，之后仍可以用本工具解开。
3. 点「开始压缩」。只有一项时，zip 用该项的名字；多项时叫 `归档.zip`。已经有同名文件时会加上 `-2`。zip 放在这些文件旁边。

## 密码本

`passwords.txt` 一行一个密码，`#` 开头是注释。打包时会放进应用里。界面上填的密码优先于密码本。

## 仓库里的其他文件

`archive_unpacker_gui.py` 和 `unpack_core.py` 是更早的 Python 界面和核心。现在日常使用的是上面的原生应用。Python 测试仍可运行：

```bash
python3 test_unpack.py
```

## 许可

[MIT](LICENSE)
