import AppKit
import SwiftUI
import UniformTypeIdentifiers

enum WorkMode: String {
    case extract
    case compress
}

struct ArchiveJob: Identifiable, Hashable {
    let id = UUID()
    var url: URL
    var kind: String
    var status: String
}

final class UnpackModel: ObservableObject {
    @Published var mode: WorkMode = .extract
    @Published var jobs: [ArchiveJob] = []
    @Published var selected: Set<UUID> = []
    @Published var password = ""
    @Published var log = "把压缩包拖到窗口里，或点「添加文件」。留空密码时会按内置密码本自动尝试。"
    @Published var busy = false
    @Published var activity = ""
    @Published var progress = ""
    @Published var activeID: UUID?
    @Published var stopping = false
    private var session: UnpackCancel?

    func setMode(_ mode: WorkMode) {
        guard !busy, mode != self.mode else { return }
        self.mode = mode
        jobs.removeAll()
        selected.removeAll()
        log = mode == .extract
            ? "把压缩包拖到窗口里，或点「添加文件」。留空密码时会按内置密码本自动尝试。"
            : "把要压缩的文件或文件夹拖进来。留空密码会生成普通 zip，填写后会加密。"
    }

    func add(urls: [URL]) {
        if mode == .compress {
            addSources(urls)
            return
        }
        let archives = UnpackEngine.expand(urls: urls, recursive: false)
        var known = Set(jobs.map(\.url.path))
        var added = 0
        for url in archives where known.insert(url.path).inserted {
            let kind = UnpackEngine.sniff(url: url).title
            jobs.append(ArchiveJob(url: url, kind: kind, status: "等待"))
            added += 1
        }
        if !busy {
            jobs.sort {
                $0.url.lastPathComponent.localizedStandardCompare($1.url.lastPathComponent) == .orderedAscending
            }
        }
        if added == 0 {
            appendLog("没有发现可识别的压缩包。")
        } else {
            appendLog("加入 \(added) 个压缩包。")
        }
    }

    func removeSelected() {
        jobs.removeAll { selected.contains($0.id) }
        selected.removeAll()
    }

    func clear() {
        guard !busy else { return }
        jobs.removeAll()
        selected.removeAll()
    }

    func addSources(_ urls: [URL]) {
        let fm = FileManager.default
        var known = Set(jobs.map(\.url.path))
        var added = 0
        for url in urls {
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else { continue }
            guard known.insert(url.path).inserted else { continue }
            jobs.append(ArchiveJob(url: url, kind: isDir.boolValue ? "文件夹" : "文件", status: "等待"))
            added += 1
        }
        if !busy {
            jobs.sort {
                $0.url.lastPathComponent.localizedStandardCompare($1.url.lastPathComponent) == .orderedAscending
            }
        }
        if added == 0 {
            appendLog("没有可压缩的文件。")
        } else {
            appendLog("加入 \(added) 项，将打成一个 zip。")
        }
    }

    func start() {
        if mode == .compress {
            startCompress()
            return
        }
        guard !busy, !jobs.isEmpty else { return }
        busy = true
        stopping = false
        progress = "1/\(jobs.count)"
        let snapshot = jobs
        let password = password
        let total = snapshot.count
        let session = UnpackCancel()
        self.session = session
        DispatchQueue.global(qos: .userInitiated).async {
            var preferred: String?
            var plainFirst = false
            var halted = false
            for (offset, job) in snapshot.enumerated() {
                if session.isStopped {
                    halted = true
                    break
                }
                let step = "\(offset + 1)/\(total)"
                let name = job.url.lastPathComponent
                DispatchQueue.main.async {
                    self.mark(job.id, "解压中")
                    self.progress = step
                    self.activeID = job.id
                    if !self.stopping {
                        self.showActivity("\(step) \(name) · 开始")
                    }
                }
                let outcome = UnpackEngine.extract(
                    archive: job.url,
                    password: password,
                    preferred: preferred,
                    plainFirst: plainFirst,
                    step: step,
                    cancel: session
                ) { event in
                    DispatchQueue.main.async {
                        switch event {
                        case .log(let line):
                            self.appendLog(line)
                        case .activity(let line):
                            guard !self.stopping else { return }
                            self.activeID = job.id
                            self.showActivity("\(step) \(name) · \(line)")
                        }
                    }
                }
                if outcome.cancelled, !outcome.ok {
                    halted = true
                    DispatchQueue.main.async {
                        self.mark(job.id, "已停止")
                        self.appendLog("已停止：\(name)")
                    }
                    break
                }
                if outcome.ok {
                    if outcome.unlockedWithoutPassword {
                        plainFirst = true
                        preferred = nil
                    } else if let used = outcome.passwordUsed {
                        plainFirst = false
                        preferred = used
                    }
                }
                DispatchQueue.main.async {
                    self.mark(job.id, outcome.ok ? "完成" : "失败")
                    self.appendLog(outcome.ok ? "完成：\(outcome.detail)" : "失败：\(outcome.detail)")
                    if let dest = outcome.destination {
                        self.appendLog("输出：\(dest.path)")
                    }
                }
                if outcome.cancelled {
                    halted = true
                    break
                }
            }
            DispatchQueue.main.async {
                self.busy = false
                self.stopping = false
                self.session = nil
                self.progress = ""
                self.activeID = nil
                self.showActivity("")
                self.appendLog(halted ? "已停止，剩下的不再解压。" : "全部处理结束。")
            }
        }
    }

    func startCompress() {
        guard !busy, !jobs.isEmpty else { return }
        busy = true
        stopping = false
        progress = "1/1"
        let sources = jobs.map(\.url)
        let ids = jobs.map(\.id)
        let password = password
        let session = UnpackCancel()
        self.session = session
        DispatchQueue.global(qos: .userInitiated).async {
            DispatchQueue.main.async {
                for id in ids { self.mark(id, "压缩中") }
                self.showActivity("正在压缩")
            }
            let outcome = UnpackEngine.compress(sources: sources, password: password, cancel: session) { event in
                DispatchQueue.main.async {
                    switch event {
                    case .log(let line):
                        self.appendLog(line)
                    case .activity(let line):
                        guard !self.stopping else { return }
                        self.showActivity(line)
                    }
                }
            }
            DispatchQueue.main.async {
                let status = outcome.cancelled && !outcome.ok ? "已停止" : (outcome.ok ? "完成" : "失败")
                for id in ids { self.mark(id, status) }
                if outcome.cancelled, !outcome.ok {
                    self.appendLog("已停止。")
                } else {
                    self.appendLog(outcome.ok ? "完成：\(outcome.detail)" : "失败：\(outcome.detail)")
                    if let dest = outcome.destination {
                        self.appendLog("输出：\(dest.path)")
                    }
                }
                self.busy = false
                self.stopping = false
                self.session = nil
                self.progress = ""
                self.activeID = nil
                self.showActivity("")
            }
        }
    }

    func stop() {
        guard busy, !stopping else { return }
        stopping = true
        showActivity("正在停止…")
        session?.request()
    }

    func revealSelection() {
        let urls = jobs.filter { selected.contains($0.id) }.map(\.url)
        if urls.isEmpty, let first = jobs.first {
            NSWorkspace.shared.activateFileViewerSelecting([first.url])
        } else if !urls.isEmpty {
            NSWorkspace.shared.activateFileViewerSelecting(urls)
        }
    }

    private func mark(_ id: UUID, _ status: String) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        jobs[index].status = status
    }

    private func appendLog(_ line: String) {
        log += "\n" + line
    }

    private func showActivity(_ text: String) {
        activity = text
        NSApp.mainWindow?.title = text.isEmpty ? "解压缩工具" : "解压缩工具 · \(text)"
    }
}

struct PasswordField: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    func makeNSView(context: Context) -> PasteTextField {
        let field = PasteTextField()
        field.placeholderString = placeholder
        field.isBezeled = true
        field.bezelStyle = .roundedBezel
        field.isEditable = true
        field.isSelectable = true
        field.usesSingleLineMode = true
        field.cell?.isScrollable = true
        field.cell?.wraps = false
        field.lineBreakMode = .byClipping
        field.delegate = context.coordinator
        field.stringValue = text
        field.setContentHuggingPriority(.defaultLow, for: .horizontal)
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }

    func updateNSView(_ field: PasteTextField, context: Context) {
        context.coordinator.text = $text
        field.isEnabled = context.environment.isEnabled
        if field.currentEditor() == nil, field.stringValue != text {
            field.stringValue = text
        }
    }

    final class Coordinator: NSObject, NSTextFieldDelegate, NSTextViewDelegate {
        var text: Binding<String>
        init(text: Binding<String>) { self.text = text }

        func controlTextDidChange(_ obj: Notification) {
            guard let field = obj.object as? NSTextField else { return }
            text.wrappedValue = field.stringValue
        }

        func textDidChange(_ notification: Notification) {
            guard let view = notification.object as? NSTextView else { return }
            text.wrappedValue = view.string
        }

        func textView(_ textView: NSTextView, shouldChangeTextIn range: NSRange, replacementString: String?) -> Bool {
            guard let replacement = replacementString else { return true }
            let cleaned = replacement
                .replacingOccurrences(of: "\u{feff}", with: "")
                .components(separatedBy: .newlines)
                .joined()
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if cleaned == replacement { return true }
            guard !cleaned.isEmpty else { return false }
            textView.insertText(cleaned, replacementRange: range)
            return false
        }
    }
}

final class PasteTextField: NSTextField {
    override func becomeFirstResponder() -> Bool {
        let ok = super.becomeFirstResponder()
        if let editor = currentEditor() as? NSTextView, let delegate = delegate as? NSTextViewDelegate {
            editor.delegate = delegate
        }
        return ok
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        let key = event.charactersIgnoringModifiers ?? ""
        if flags == .command, key == "v" {
            pastePlainText()
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

    func pastePlainText() {
        let raw = NSPasteboard.general.string(forType: .string) ?? ""
        let cleaned = raw
            .replacingOccurrences(of: "\u{feff}", with: "")
            .components(separatedBy: .newlines)
            .joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }
        if let editor = currentEditor() as? NSTextView {
            editor.insertText(cleaned, replacementRange: editor.selectedRange())
        } else {
            stringValue = cleaned
            NotificationCenter.default.post(name: NSControl.textDidChangeNotification, object: self)
        }
    }
}

struct RootView: View {
    @ObservedObject var model: UnpackModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Picker("模式", selection: Binding(get: { model.mode }, set: { model.setMode($0) })) {
                    Text("解压").tag(WorkMode.extract)
                    Text("压缩").tag(WorkMode.compress)
                }
                .pickerStyle(.segmented)
                .frame(width: 140)
                .disabled(model.busy)
                Button("添加文件") { pick(files: true) }
                Button("添加文件夹") { pick(files: false) }
                Button("移除") { model.removeSelected() }
                    .disabled(model.selected.isEmpty || model.busy)
                Button("清空") { model.clear() }
                    .disabled(model.busy || model.jobs.isEmpty)
                Spacer()
                if model.busy {
                    Button(model.stopping ? "正在停止…" : "停止") { model.stop() }
                        .keyboardShortcut(.cancelAction)
                        .disabled(model.stopping)
                } else {
                    Button(model.mode == .compress ? "开始压缩" : "开始解压") { model.start() }
                        .keyboardShortcut(.defaultAction)
                        .disabled(model.jobs.isEmpty)
                }
            }
            HStack {
                Text("密码")
                PasswordField(
                    text: $model.password,
                    placeholder: model.mode == .compress ? "留空则不加密" : "留空则自动尝试密码本"
                )
                    .disabled(model.busy)
                    .frame(height: 24)
            }
            ScrollViewReader { proxy in
                List(model.jobs, selection: $model.selected) { job in
                    HStack {
                        Text(job.url.lastPathComponent)
                            .lineLimit(1)
                        Spacer()
                        Text(job.kind)
                            .foregroundStyle(.secondary)
                            .frame(width: 72, alignment: .trailing)
                        Text(job.status)
                            .frame(width: 64, alignment: .trailing)
                    }
                    .id(job.id)
                }
                .frame(minHeight: 220)
                .onChange(of: model.activeID) { _, id in
                    guard let id else { return }
                    proxy.scrollTo(id, anchor: .center)
                }
            }
            if model.busy {
                Text(model.activity.isEmpty ? (model.mode == .compress ? "正在压缩…" : "正在解压…") : model.activity)
                    .font(.headline)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .background(Color.accentColor.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
            }
            ScrollViewReader { proxy in
                ScrollView {
                    Text(model.log)
                        .font(.system(.body, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .padding(8)
                    Color.clear.frame(height: 1).id("log-end")
                }
                .frame(minHeight: 160)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
                .onChange(of: model.log) { _, _ in
                    proxy.scrollTo("log-end", anchor: .bottom)
                }
            }
            HStack {
                Button("在访达中显示所选") { model.revealSelection() }
                    .disabled(model.jobs.isEmpty)
                Spacer()
                Text(model.mode == .compress ? "打成一个 zip，放在这些文件旁边" : "输出在压缩包旁边的同名文件夹")
                    .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .dropDestination(for: URL.self) { urls, _ in
            model.add(urls: urls)
            return true
        }
    }

    private func pick(files: Bool) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseFiles = files
        panel.canChooseDirectories = !files
        panel.prompt = "添加"
        guard panel.runModal() == .OK else { return }
        model.add(urls: panel.urls)
    }
}
