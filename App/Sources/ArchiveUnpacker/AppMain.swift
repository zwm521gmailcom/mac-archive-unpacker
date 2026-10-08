import AppKit
import SwiftUI

@main
enum AppMain {
    static func main() {
        if CommandLine.arguments.contains("--self-test") {
            let code = UnpackEngine.selfTest()
            exit(code)
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = UnpackModel()
    var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 920, height: 680),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "解压缩工具"
        window.contentMinSize = NSSize(width: 720, height: 480)
        window.contentView = NSHostingView(rootView: RootView(model: model))
        window.center()
        window.makeKeyAndOrderFront(nil)
        self.window = window
        installMenu()
        if let icon = NSImage(named: "AppIcon") ?? Bundle.main.image(forResource: "AppIcon") {
            NSApp.applicationIconImage = icon
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    private func installMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "关于解压缩工具", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        let quit = appMenu.addItem(withTitle: "退出解压缩工具", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.keyEquivalentModifierMask = .command

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "编辑")
        editItem.submenu = edit
        edit.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(pastePlain(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        NSApp.mainMenu = main
    }

    @objc func pastePlain(_ sender: Any?) {
        guard let editor = window?.firstResponder as? NSTextView else {
            NSApp.sendAction(#selector(NSText.paste(_:)), to: nil, from: sender)
            return
        }
        let raw = NSPasteboard.general.string(forType: .string) ?? ""
        let cleaned = raw
            .replacingOccurrences(of: "\u{feff}", with: "")
            .components(separatedBy: .newlines)
            .joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }
        editor.insertText(cleaned, replacementRange: editor.selectedRange())
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}
