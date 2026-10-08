import CryptoKit
import Darwin
import Foundation

enum ArchiveKind: String {
    case rar5, rar, sevenz, zip, gzip, bzip2, xz, zstd, lzip, cab, arj, iso, tar, unknown, empty

    var title: String {
        switch self {
        case .rar5: return "RAR5"
        case .rar: return "RAR"
        case .sevenz: return "7z"
        case .zip: return "ZIP"
        case .gzip: return "GZip"
        case .bzip2: return "BZip2"
        case .xz: return "XZ"
        case .zstd: return "Zstandard"
        case .lzip: return "Lzip"
        case .cab: return "CAB"
        case .arj: return "ARJ"
        case .iso: return "ISO"
        case .tar: return "TAR"
        case .unknown: return "未知"
        case .empty: return "空文件"
        }
    }

    var isTarFamily: Bool {
        switch self {
        case .tar, .gzip, .bzip2, .xz, .zstd, .lzip: return true
        default: return false
        }
    }
}

struct ExtractOutcome {
    var ok: Bool
    var detail: String
    var destination: URL?
    var passwordUsed: String?
    var unlockedWithoutPassword: Bool
    var cancelled = false
}

/// 停止按钮和正在跑的解压进程之间的开关。
final class UnpackCancel: @unchecked Sendable {
    private let lock = NSLock()
    private var stopped = false
    private var process: Process?

    var isStopped: Bool {
        lock.lock()
        defer { lock.unlock() }
        return stopped
    }

    func request() {
        lock.lock()
        stopped = true
        let proc = process
        lock.unlock()
        guard let proc, proc.isRunning else { return }
        proc.terminate()
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 0.5) {
            if proc.isRunning {
                kill(proc.processIdentifier, SIGKILL)
            }
        }
    }

    func attach(_ proc: Process) {
        lock.lock()
        process = proc
        let shouldStop = stopped
        lock.unlock()
        if shouldStop, proc.isRunning {
            proc.terminate()
        }
    }

    func detach(_ proc: Process) {
        lock.lock()
        if process === proc {
            process = nil
        }
        lock.unlock()
    }
}

enum UnpackNote {
    case log(String)
    case activity(String)
}

enum CompressFormat: String, CaseIterable {
    case zip, sevenz, tarGz, tarBz2, tarXz

    var title: String {
        switch self {
        case .zip: return "ZIP"
        case .sevenz: return "7z"
        case .tarGz: return "tar.gz"
        case .tarBz2: return "tar.bz2"
        case .tarXz: return "tar.xz"
        }
    }

    var fileExtension: String {
        switch self {
        case .zip: return "zip"
        case .sevenz: return "7z"
        case .tarGz: return "tar.gz"
        case .tarBz2: return "tar.bz2"
        case .tarXz: return "tar.xz"
        }
    }

    var allowsPassword: Bool { self == .zip }
}

enum UnpackEngine {
    private static let defaults = [
        "oldmanemu.net", "www.oldmanemu.net", "oldmanemu",
        "123456", "1234", "12345678", "0000", "password",
        "www.emu-zone.org", "www.emu618.com", "emu618"
    ]

    static func sniff(url: URL) -> ArchiveKind {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return .unknown }
        defer { try? handle.close() }
        let head = (try? handle.read(upToCount: 512)) ?? Data()
        if head.isEmpty { return .empty }
        let signatures: [(Data, ArchiveKind)] = [
            (Data([0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x01, 0x00]), .rar5),
            (Data([0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x00]), .rar),
            (Data([0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c]), .sevenz),
            (Data([0x50, 0x4b, 0x03, 0x04]), .zip),
            (Data([0x50, 0x4b, 0x05, 0x06]), .zip),
            (Data([0x50, 0x4b, 0x07, 0x08]), .zip),
            (Data([0x1f, 0x8b]), .gzip),
            (Data([0x42, 0x5a, 0x68]), .bzip2),
            (Data([0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00]), .xz),
            (Data([0x28, 0xb5, 0x2f, 0xfd]), .zstd),
            (Data("LZIP".utf8), .lzip),
            (Data("MSCF".utf8), .cab),
            (Data([0x41, 0x52, 0x4a, 0x60]), .arj)
        ]
        for (sig, kind) in signatures where head.starts(with: sig) {
            return kind
        }
        if head.count > 265, head[257..<262] == Data("ustar".utf8) {
            return .tar
        }
        if let iso = try? FileHandle(forReadingFrom: url) {
            defer { try? iso.close() }
            try? iso.seek(toOffset: 0x8001)
            if (try? iso.read(upToCount: 5)) == Data("CD001".utf8) {
                return .iso
            }
        }
        return .unknown
    }

    static func isArchive(_ url: URL) -> Bool {
        let kind = sniff(url: url)
        return kind != .unknown && kind != .empty
    }

    static func outputDirectory(for archive: URL) -> URL {
        let parent = archive.deletingLastPathComponent()
        let stem = outputStem(archive.lastPathComponent)
        var dest = parent.appendingPathComponent(stem, isDirectory: true)
        var index = 2
        let fm = FileManager.default
        while fm.fileExists(atPath: dest.path),
              ((try? fm.contentsOfDirectory(atPath: dest.path))?.isEmpty == false) {
            dest = parent.appendingPathComponent("\(stem)-\(index)", isDirectory: true)
            index += 1
        }
        return dest
    }

    static func passwords(user: String) -> [String] {
        var list: [String] = []
        let typed = user.trimmingCharacters(in: .whitespacesAndNewlines)
        if !typed.isEmpty { list.append(typed) }
        if let file = passwordFileURL(),
           let text = try? String(contentsOf: file, encoding: .utf8) {
            for line in text.split(whereSeparator: \.isNewline) {
                let item = line.trimmingCharacters(in: .whitespaces)
                if item.isEmpty || item.hasPrefix("#") { continue }
                list.append(item)
            }
        }
        list.append(contentsOf: defaults)
        var seen = Set<String>()
        return list.filter { seen.insert($0).inserted }
    }

    /// 同一批里先试已经成功的密码，再试密码本，无密码放在最后。
    /// 否则每个加密包都会先空跑一轮 unar，日志会停在「尝试密码」很久。
    static func passwordTries(book: [String], preferred: String?, plainFirst: Bool) -> [String?] {
        var ordered: [String] = []
        func push(_ raw: String?) {
            guard let raw else { return }
            let item = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if item.isEmpty || ordered.contains(item) { return }
            ordered.append(item)
        }
        if !plainFirst {
            push(preferred)
        }
        for item in book {
            push(item)
        }
        if plainFirst {
            return [nil] + ordered.map { Optional($0) }
        }
        if ordered.isEmpty {
            return [nil]
        }
        return ordered.map { Optional($0) } + [nil]
    }

    static func extract(
        archive: URL,
        password: String,
        preferred: String?,
        plainFirst: Bool,
        step: String,
        cancel: UnpackCancel? = nil,
        note: @escaping (UnpackNote) -> Void
    ) -> ExtractOutcome {
        let kind = sniff(url: archive)
        guard kind != .unknown, kind != .empty else {
            return ExtractOutcome(ok: false, detail: "认不出压缩格式", destination: nil, passwordUsed: nil, unlockedWithoutPassword: false)
        }
        let dest = outputDirectory(for: archive)
        let tries = passwordTries(book: passwords(user: password), preferred: preferred, plainFirst: plainFirst)
        let head = step.isEmpty ? "" : "\(step) "
        note(.log("\(head)\(archive.lastPathComponent) → \(dest.lastPathComponent)（\(kind.title)）"))
        note(.activity("准备解压"))

        if kind.isTarFamily, let bsdtar = tool("bsdtar") {
            if cancel?.isStopped == true {
                cleanup(dest)
                return cancelledOutcome()
            }
            note(.log("  尝试 bsdtar …"))
            note(.activity("正在用 bsdtar 解压"))
            let extracted = runExtract(bin: bsdtar, args: ["-xf", archive.path, "-C", dest.path], dest: dest, cancel: cancel, onBytes: { bytes in
                note(.activity("已解出 \(formatBytes(bytes))"))
            })
            if extracted, fileCount(dest) > 0 {
                finish(dest: dest, note: note, cancel: cancel)
                return ExtractOutcome(
                    ok: true,
                    detail: "bsdtar 完成",
                    destination: dest,
                    passwordUsed: nil,
                    unlockedWithoutPassword: true,
                    cancelled: cancel?.isStopped == true
                )
            }
            if cancel?.isStopped == true {
                cleanup(dest)
                return cancelledOutcome()
            }
            cleanup(dest)
        }

        guard let unar = tool("unar") else {
            cleanup(dest)
            return ExtractOutcome(ok: false, detail: "应用里没有 unar，无法解这个格式", destination: nil, passwordUsed: nil, unlockedWithoutPassword: false)
        }

        var last = ""
        for pw in tries {
            if cancel?.isStopped == true {
                cleanup(dest)
                return cancelledOutcome()
            }
            let label = pw.map { "unar 密码 \($0)" } ?? "unar 无密码"
            note(.log("  尝试 \(label) …"))
            note(.activity(label))
            var args = ["-f", "-D", "-o", dest.path]
            if let pw { args += ["-p", pw] }
            args.append(archive.path)
            let result = run(bin: unar, args: args, cwd: dest, cancel: cancel, onBytes: { bytes in
                note(.activity("已解出 \(formatBytes(bytes))"))
            })
            if result.code == 0, fileCount(dest) > 0 {
                finish(dest: dest, note: note, cancel: cancel)
                let detail = pw.map { "unar 完成（密码 \($0)）" } ?? "unar 完成"
                return ExtractOutcome(
                    ok: true,
                    detail: detail,
                    destination: dest,
                    passwordUsed: pw,
                    unlockedWithoutPassword: pw == nil,
                    cancelled: cancel?.isStopped == true
                )
            }
            if cancel?.isStopped == true {
                cleanup(dest)
                return cancelledOutcome()
            }
            last = result.text
            if looksLikePasswordError(last) {
                note(.log(pw == nil ? "    需要密码" : "    密码不对"))
            } else {
                note(.log("    失败 \(shorten(last))"))
            }
            cleanup(dest)
            if !last.isEmpty, !looksLikePasswordError(last) {
                break
            }
        }
        let detail = last.isEmpty ? "解压失败" : shorten(last)
        return ExtractOutcome(ok: false, detail: detail, destination: nil, passwordUsed: nil, unlockedWithoutPassword: false)
    }

    static func compress(
        sources: [URL],
        password: String,
        format: CompressFormat = .zip,
        cancel: UnpackCancel? = nil,
        note: @escaping (UnpackNote) -> Void
    ) -> ExtractOutcome {
        let existing = sources.filter { FileManager.default.fileExists(atPath: $0.path) }
        guard !existing.isEmpty else {
            return ExtractOutcome(ok: false, detail: "没有可压缩的文件", destination: nil, passwordUsed: nil, unlockedWithoutPassword: false)
        }
        let typed = password.trimmingCharacters(in: .whitespacesAndNewlines)
        if !typed.isEmpty, !format.allowsPassword {
            return ExtractOutcome(ok: false, detail: "\(format.title) 不能加密码，请留空，或改用 ZIP", destination: nil, passwordUsed: nil, unlockedWithoutPassword: false)
        }
        guard let input = zipInput(for: existing) else {
            return ExtractOutcome(ok: false, detail: "这些文件分散在不同文件夹，无法集中", destination: nil, passwordUsed: nil, unlockedWithoutPassword: false)
        }
        defer { input.cleanup?() }
        let dest = availableArchive(for: existing, format: format)
        note(.log("压缩 \(existing.count) 项 → \(dest.lastPathComponent)"))
        note(.activity(typed.isEmpty ? "正在压缩" : "正在加密压缩"))
        let result = compressProcess(format: format, input: input, dest: dest, password: typed, cancel: cancel) { bytes in
            note(.activity("已写入 \(formatBytes(bytes))"))
        }
        if cancel?.isStopped == true {
            try? FileManager.default.removeItem(at: dest)
            return cancelledOutcome()
        }
        if result.code == 0, fileSize(dest) > 0 {
            let detail = typed.isEmpty ? "已生成 \(format.title)" : "已生成加密 \(format.title)"
            return ExtractOutcome(ok: true, detail: detail, destination: dest, passwordUsed: typed.isEmpty ? nil : typed, unlockedWithoutPassword: typed.isEmpty)
        }
        try? FileManager.default.removeItem(at: dest)
        let detail = result.text.isEmpty ? "压缩失败" : shorten(result.text)
        return ExtractOutcome(ok: false, detail: detail, destination: nil, passwordUsed: nil, unlockedWithoutPassword: false)
    }

    private struct ZipInput {
        var directory: URL
        var names: [String]
        var keepLinks: Bool
        var cleanup: (() -> Void)?
    }

    private static func zipInput(for sources: [URL]) -> ZipInput? {
        guard let parent = sources.first?.deletingLastPathComponent() else { return nil }
        let sameParent = sources.allSatisfy { $0.deletingLastPathComponent().path == parent.path }
        if sameParent {
            return ZipInput(directory: parent, names: sources.map(\.lastPathComponent), keepLinks: true, cleanup: nil)
        }
        let fm = FileManager.default
        let temp = fm.temporaryDirectory.appendingPathComponent("zip-\(UUID().uuidString)", isDirectory: true)
        do {
            try fm.createDirectory(at: temp, withIntermediateDirectories: true)
            var names: [String] = []
            var used = Set<String>()
            for source in sources {
                var name = source.lastPathComponent
                var index = 2
                while !used.insert(name).inserted {
                    let base = source.deletingPathExtension().lastPathComponent
                    let ext = source.pathExtension
                    name = ext.isEmpty ? "\(base)-\(index)" : "\(base)-\(index).\(ext)"
                    index += 1
                }
                try fm.createSymbolicLink(at: temp.appendingPathComponent(name), withDestinationURL: source)
                names.append(name)
            }
            return ZipInput(directory: temp, names: names, keepLinks: false, cleanup: { try? fm.removeItem(at: temp) })
        } catch {
            try? fm.removeItem(at: temp)
            return nil
        }
    }

    private static func compressProcess(
        format: CompressFormat,
        input: ZipInput,
        dest: URL,
        password: String,
        cancel: UnpackCancel?,
        onBytes: @escaping (Int64) -> Void
    ) -> (code: Int32, text: String) {
        switch format {
        case .zip:
            var args = ["-r"]
            if input.keepLinks { args.append("-y") }
            if !password.isEmpty { args += ["-P", password] }
            args.append(dest.path)
            args += input.names
            return run(bin: "/usr/bin/zip", args: args, cwd: input.directory, cancel: cancel, measure: { fileSize(dest) }, onBytes: onBytes)
        case .sevenz, .tarGz, .tarBz2, .tarXz:
            let args = ["-a", "-c", "-L", "-f", dest.path] + input.names
            return run(bin: "/usr/bin/bsdtar", args: args, cwd: input.directory, cancel: cancel, measure: { fileSize(dest) }, onBytes: onBytes)
        }
    }

    private static func availableArchive(for sources: [URL], format: CompressFormat) -> URL {
        let parent = sources[0].deletingLastPathComponent()
        let stem = sources.count == 1 ? sources[0].lastPathComponent : "归档"
        func named(_ suffix: String) -> URL {
            parent.appendingPathComponent(stem + suffix)
        }
        let ext = "." + format.fileExtension
        var dest = named(ext)
        var index = 2
        while FileManager.default.fileExists(atPath: dest.path) {
            dest = named("-\(index)" + ext)
            index += 1
        }
        return dest
    }

    private static func fileSize(_ url: URL) -> Int64 {
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
        return Int64(size)
    }

    private static func cancelledOutcome() -> ExtractOutcome {
        ExtractOutcome(ok: false, detail: "已停止", destination: nil, passwordUsed: nil, unlockedWithoutPassword: false, cancelled: true)
    }

    static func expand(urls: [URL], recursive: Bool) -> [URL] {
        var found: [URL] = []
        let fm = FileManager.default
        for url in urls {
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else { continue }
            if isDir.boolValue {
                if recursive {
                    if let walker = fm.enumerator(at: url, includingPropertiesForKeys: [.isRegularFileKey]) {
                        for case let item as URL in walker {
                            if item.lastPathComponent.hasPrefix(".") { continue }
                            if isArchive(item) { found.append(item) }
                        }
                    }
                } else if let children = try? fm.contentsOfDirectory(at: url, includingPropertiesForKeys: nil) {
                    found.append(contentsOf: children.filter(isArchive))
                }
            } else if isArchive(url) {
                found.append(url)
            }
        }
        var seen = Set<String>()
        return found.filter { seen.insert($0.path).inserted }
    }

    static func selfTest() -> Int32 {
        let first = passwordTries(book: ["oldmanemu.net", "123456"], preferred: nil, plainFirst: false)
        let reused = passwordTries(book: ["oldmanemu.net", "123456"], preferred: "123456", plainFirst: false)
        let plain = passwordTries(book: ["oldmanemu.net"], preferred: "oldmanemu.net", plainFirst: true)
        if first != ["oldmanemu.net", "123456", nil]
            || reused[0] != "123456"
            || reused[reused.count - 1] != nil
            || plain[0] != nil {
            fputs("密码顺序不对\n", stderr)
            return 1
        }
        let stopwatch = UnpackCancel()
        let started = Date()
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) {
            stopwatch.request()
        }
        _ = run(bin: "/bin/sleep", args: ["30"], cwd: FileManager.default.temporaryDirectory, cancel: stopwatch)
        if !stopwatch.isStopped || Date().timeIntervalSince(started) > 5 {
            fputs("停止失败\n", stderr)
            return 1
        }
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent("unpacker-self-\(UUID().uuidString)")
        let src = root.appendingPathComponent("src")
        let zip = root.appendingPathComponent("sample.zip")
        do {
            try fm.createDirectory(at: src, withIntermediateDirectories: true)
            try Data("你好".utf8).write(to: src.appendingPathComponent("你好.txt"))
            let packed = run(bin: "/usr/bin/ditto", args: ["-c", "-k", src.path, zip.path], cwd: root)
            guard packed.code == 0, sniff(url: zip) == .zip else {
                fputs("打包或识别失败\n", stderr)
                return 1
            }
            guard tool("unar") != nil else {
                fputs("找不到 unar\n", stderr)
                return 1
            }
            let outcome = extract(
                archive: zip,
                password: "",
                preferred: "oldmanemu.net",
                plainFirst: false,
                step: "1/1"
            ) { _ in }
            let manifest = outcome.destination?.appendingPathComponent(".unpack-SHA256SUMS.txt")
            let hashed = manifest.map { fm.fileExists(atPath: $0.path) } ?? false
            if !outcome.ok || outcome.passwordUsed != "oldmanemu.net" || !hashed {
                try? fm.removeItem(at: root)
                fputs("解压自检失败 \(outcome.detail)\n", stderr)
                return 1
            }
            let made = compress(sources: [src], password: "") { _ in }
            guard made.ok, let madeZip = made.destination, sniff(url: madeZip) == .zip else {
                try? fm.removeItem(at: root)
                fputs("压缩自检失败 \(made.detail)\n", stderr)
                return 1
            }
            let gz = compress(sources: [src], password: "", format: .tarGz) { _ in }
            let seven = compress(sources: [src], password: "", format: .sevenz) { _ in }
            guard gz.ok, let gzURL = gz.destination, sniff(url: gzURL) == .gzip,
                  seven.ok, let sevenURL = seven.destination, sniff(url: sevenURL) == .sevenz else {
                try? fm.removeItem(at: root)
                fputs("tar.gz 或 7z 压缩失败 \(gz.detail) \(seven.detail)\n", stderr)
                return 1
            }
            let secretFile = src.appendingPathComponent("你好.txt")
            let locked = compress(sources: [secretFile], password: "p@ss") { _ in }
            guard locked.ok, let lockedZip = locked.destination, let unar = tool("unar") else {
                try? fm.removeItem(at: root)
                fputs("加密压缩失败 \(locked.detail)\n", stderr)
                return 1
            }
            let secretOut = root.appendingPathComponent("secret-out")
            let opened = run(bin: unar, args: ["-f", "-D", "-o", secretOut.path, "-p", "p@ss", lockedZip.path], cwd: secretOut)
            let found = fm.fileExists(atPath: secretOut.appendingPathComponent("你好.txt").path)
            try? fm.removeItem(at: root)
            if opened.code != 0 || !found {
                fputs("加密包打不开 \(opened.text)\n", stderr)
                return 1
            }
            fputs("自检通过\n", stderr)
            return 0
        } catch {
            fputs("自检异常 \(error)\n", stderr)
            return 1
        }
    }

    private static func finish(dest: URL, note: @escaping (UnpackNote) -> Void, cancel: UnpackCancel?) {
        if cancel?.isStopped == true { return }
        note(.activity("正在整理文件名"))
        let fixed = fixNames(in: dest)
        if fixed > 0 { note(.log("  修复了 \(fixed) 个乱码文件名")) }
        if cancel?.isStopped == true { return }
        note(.activity("正在计算校验"))
        if let manifest = writeManifest(in: dest, cancel: cancel) {
            note(.log("  已写出 \(manifest)"))
        }
    }

    private static func tool(_ name: String) -> String? {
        let fm = FileManager.default
        var candidates: [String] = []
        if let exe = Bundle.main.executableURL?.deletingLastPathComponent() {
            candidates.append(exe.appendingPathComponent(name).path)
        }
        if let res = Bundle.main.resourceURL {
            candidates.append(res.appendingPathComponent(name).path)
        }
        if name == "bsdtar" { candidates.append("/usr/bin/bsdtar") }
        candidates.append("/opt/homebrew/bin/\(name)")
        candidates.append("/usr/local/bin/\(name)")
        return candidates.first { fm.isExecutableFile(atPath: $0) }
    }

    private static func passwordFileURL() -> URL? {
        if let bundled = Bundle.main.url(forResource: "passwords", withExtension: "txt") {
            return bundled
        }
        let beside = URL(fileURLWithPath: CommandLine.arguments[0])
            .deletingLastPathComponent()
            .appendingPathComponent("passwords.txt")
        if FileManager.default.fileExists(atPath: beside.path) { return beside }
        return nil
    }

    private static func outputStem(_ filename: String) -> String {
        let low = filename.lowercased()
        let compounds = [".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz", ".tgz", ".tbz2", ".txz", ".tzst"]
        var stem = filename
        for ext in compounds where low.hasSuffix(ext) {
            stem = String(filename.dropLast(ext.count))
            break
        }
        if stem == filename {
            stem = (filename as NSString).deletingPathExtension
        }
        let bad = CharacterSet(charactersIn: "/\\:*?\"<>|\n\r\t")
        let cleaned = stem.unicodeScalars.map { bad.contains($0) ? "_" : Character($0) }
        let trimmed = String(cleaned).trimmingCharacters(in: CharacterSet(charactersIn: " ."))
        return trimmed.isEmpty ? "extracted" : trimmed
    }

    private static func runExtract(
        bin: String,
        args: [String],
        dest: URL,
        cancel: UnpackCancel? = nil,
        onBytes: ((Int64) -> Void)? = nil
    ) -> Bool {
        try? FileManager.default.createDirectory(at: dest, withIntermediateDirectories: true)
        return run(bin: bin, args: args, cwd: dest, cancel: cancel, onBytes: onBytes).code == 0
    }

    private static func run(
        bin: String,
        args: [String],
        cwd: URL,
        cancel: UnpackCancel? = nil,
        measure: (() -> Int64)? = nil,
        onBytes: ((Int64) -> Void)? = nil
    ) -> (code: Int32, text: String) {
        try? FileManager.default.createDirectory(at: cwd, withIntermediateDirectories: true)
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: bin)
        proc.arguments = args
        proc.currentDirectoryURL = cwd
        proc.standardInput = FileHandle.nullDevice
        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        let lock = NSLock()
        var chunks: [Data] = []
        let group = DispatchGroup()
        func drain(_ handle: FileHandle) {
            group.enter()
            DispatchQueue.global().async {
                while true {
                    let data = handle.availableData
                    if data.isEmpty { break }
                    lock.lock()
                    chunks.append(data)
                    lock.unlock()
                }
                group.leave()
            }
        }
        drain(out.fileHandleForReading)
        drain(err.fileHandleForReading)
        cancel?.attach(proc)
        defer { cancel?.detach(proc) }
        if cancel?.isStopped == true {
            try? out.fileHandleForWriting.close()
            try? err.fileHandleForWriting.close()
            group.wait()
            return (1, "")
        }
        let gate = NSLock()
        var stopped = false
        let timer: DispatchSourceTimer? = {
            guard let onBytes else { return nil }
            let source = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
            source.schedule(deadline: .now() + .seconds(1), repeating: 1)
            source.setEventHandler {
                gate.lock()
                let done = stopped
                gate.unlock()
                if done { return }
                onBytes(measure?() ?? folderBytes(cwd))
            }
            source.resume()
            return source
        }()
        defer {
            gate.lock()
            stopped = true
            gate.unlock()
            timer?.cancel()
        }
        do { try proc.run() } catch {
            return (1, error.localizedDescription)
        }
        proc.waitUntilExit()
        gate.lock()
        stopped = true
        gate.unlock()
        timer?.cancel()
        group.wait()
        lock.lock()
        let data = chunks.reduce(into: Data()) { $0.append($1) }
        lock.unlock()
        let text = String(data: data, encoding: .utf8) ?? ""
        return (proc.terminationStatus, text)
    }

    private static func folderBytes(_ root: URL) -> Int64 {
        guard let walker = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]
        ) else { return 0 }
        var total: Int64 = 0
        for case let item as URL in walker {
            let values = try? item.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
            if values?.isRegularFile == true {
                total += Int64(values?.fileSize ?? 0)
            }
        }
        return total
    }

    private static func formatBytes(_ bytes: Int64) -> String {
        if bytes >= 1_000_000_000 {
            return String(format: "%.1f GB", Double(bytes) / 1_000_000_000)
        }
        if bytes >= 1_000_000 {
            return String(format: "%.0f MB", Double(bytes) / 1_000_000)
        }
        return "\(bytes) B"
    }

    private static func fileCount(_ root: URL) -> Int {
        guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey]) else {
            return 0
        }
        var count = 0
        for case let item as URL in walker {
            if item.lastPathComponent == ".DS_Store" { continue }
            count += 1
        }
        return count
    }

    private static func cleanup(_ dest: URL) {
        try? FileManager.default.removeItem(at: dest)
    }

    private static func looksLikePasswordError(_ text: String) -> Bool {
        let lower = text.lowercased()
        return ["password", "wrong password", "encrypted", "密码", "checksum"].contains { lower.contains($0) }
    }

    private static func shorten(_ text: String) -> String {
        let line = text.split(whereSeparator: \.isNewline).last.map(String.init) ?? text
        if line.count <= 180 { return line }
        return String(line.prefix(180))
    }

    private static func writeManifest(in root: URL, cancel: UnpackCancel?) -> String? {
        guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey]) else {
            return nil
        }
        var lines: [String] = []
        for case let item as URL in walker {
            if cancel?.isStopped == true { return nil }
            let name = item.lastPathComponent
            if name == ".DS_Store" || name.hasPrefix(".unpack-") { continue }
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: item.path, isDirectory: &isDir), isDir.boolValue { continue }
            guard let hex = sha256File(item, cancel: cancel) else {
                if cancel?.isStopped == true { return nil }
                continue
            }
            let rel = item.path.replacingOccurrences(of: root.path + "/", with: "")
            lines.append("\(hex)  \(rel)")
        }
        let file = root.appendingPathComponent(".unpack-SHA256SUMS.txt")
        let body = lines.sorted().joined(separator: "\n")
        try? body.write(to: file, atomically: true, encoding: .utf8)
        return file.lastPathComponent
    }

    private static func sha256File(_ url: URL, cancel: UnpackCancel?) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            if cancel?.isStopped == true { return nil }
            guard let chunk = try? handle.read(upToCount: 1024 * 1024), !chunk.isEmpty else { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func fixNames(in root: URL) -> Int {
        let fm = FileManager.default
        guard let walker = fm.enumerator(at: root, includingPropertiesForKeys: nil) else { return 0 }
        let urls = (walker.allObjects as? [URL] ?? []).sorted { $0.path.count > $1.path.count }
        var fixed = 0
        for url in urls {
            let name = url.lastPathComponent
            guard let repaired = repairedName(name), repaired != name else { continue }
            let dest = url.deletingLastPathComponent().appendingPathComponent(repaired)
            if fm.fileExists(atPath: dest.path) { continue }
            if (try? fm.moveItem(at: url, to: dest)) != nil { fixed += 1 }
        }
        return fixed
    }

    private static func repairedName(_ name: String) -> String? {
        let nfc = name.precomposedStringWithCanonicalMapping
        if cjkRatio(nfc) > 0.1 { return nil }
        guard let raw = encodeCP437(nfc) else { return nil }
        if let text = String(data: raw, encoding: .utf8), text != nfc, cjkRatio(text) >= 0.3 {
            return text
        }
        let encodings: [CFStringEncodings] = [
            .GB_18030_2000, .shiftJIS, .big5, .EUC_JP, .EUC_KR
        ]
        for enc in encodings {
            let ns = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(enc.rawValue)))
            guard let text = String(data: raw, encoding: ns), text != nfc, cjkRatio(text) >= 0.3 else { continue }
            return text
        }
        return nil
    }

    private static func encodeCP437(_ text: String) -> Data? {
        let ns = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.dosLatinUS.rawValue)))
        return text.data(using: ns)
    }

    private static func cjkRatio(_ text: String) -> Double {
        let letters = text.filter { !$0.isWhitespace && !"./\\_- ".contains($0) }
        if letters.isEmpty { return 0 }
        let cjk = letters.filter { ch in
            guard let s = ch.unicodeScalars.first else { return false }
            let v = s.value
            return (0x3040...0x9FFF).contains(v) || (0xAC00...0xD7AF).contains(v)
        }
        return Double(cjk.count) / Double(letters.count)
    }
}
