// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2026 迪儿 Dear
// Author: https://github.com/sht2574898282-afk
import SwiftUI
import AppKit
import Combine
import UniformTypeIdentifiers

struct EditorialProject: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let fps: String
    let duration: String
    let clipCount: Int
    let mediaCount: Int
}
struct EditorialIssue: Decodable, Identifiable, Sendable {
    let id: String
    let code: String
    let message: String
    let severity: String
    let project: String
    let clip: String
    let time: String
    let location: String
    let choices: [String: String]
    let details: String
}
struct EditorialEntry: Decodable, Identifiable, Sendable {
    let id: String
    let path: String
    let name: String
    let family: String
    let version: String
    let inputHash: String
    let projects: [EditorialProject]
    let issues: [EditorialIssue]
    let mediaCount: Int
    let offlineCount: Int
    let blockingCount: Int
    let readable: Bool
    let decisions: [String: String]
    var familyLabel: String { family == "fcpxml" ? "FCPXML" : family == "xmeml" ? "Final Cut 7 XML" : "未知格式" }
    var typeLabel: String { path.lowercased().hasSuffix(".fcpxmld") ? "FCPXML 目录包" : familyLabel }
}
struct CheckValues: Decodable, Sendable {
    let structure: String?
    let timeline: String?
    let host: String?
    let timelineDetail: String?
}
struct EditorialResult: Decodable, Sendable {
    let status: String
    let outputPath: String
    let reportJson: String
    let reportHtml: String
    let checks: CheckValues
    let message: String
    let issues: [EditorialIssue]
    var generated: Bool { !outputPath.isEmpty }
}
struct EngineReply: Decodable, Sendable {
    let entries: [EditorialEntry]?
    let entry: EditorialEntry?
    let result: EditorialResult?
    let error: String?
    let cancelled: Bool?
    let proxyPlan: ProxyPlan?
    let proxyRow: ProxyRow?
}

enum LocalEngine {
    static var executable: URL { Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/XMLBridgeCore") }
    static func call(_ payload: [String: Any], folder: URL) async throws -> EngineReply {
        let request = folder.appendingPathComponent(UUID().uuidString + ".json")
        try JSONSerialization.data(withJSONObject: payload).write(to: request, options: .atomic)
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                defer { try? FileManager.default.removeItem(at: request) }
                do {
                    let process = Process()
                    process.executableURL = executable
                    process.arguments = ["--native-request", request.path]
                    let stdout = Pipe()
                    // Capture stderr in a file so long diagnostics cannot fill a second pipe.
                    let errorURL = folder.appendingPathComponent(UUID().uuidString + ".log")
                    FileManager.default.createFile(atPath: errorURL.path, contents: nil)
                    let stderr = try FileHandle(forWritingTo: errorURL)
                    defer { try? stderr.close(); try? FileManager.default.removeItem(at: errorURL) }
                    process.standardOutput = stdout
                    process.standardError = stderr
                    try process.run()
                    let data = stdout.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    let decoder = JSONDecoder()
                    decoder.keyDecodingStrategy = .convertFromSnakeCase
                    do {
                        continuation.resume(returning: try decoder.decode(EngineReply.self, from: data))
                    } catch {
                        let detail = (try? String(contentsOf: errorURL, encoding: .utf8)) ?? ""
                        throw NSError(domain: "XMLBridge", code: 1, userInfo: [NSLocalizedDescriptionKey:
                            "转换引擎未返回完整结果。" + String(detail.prefix(700))])
                    }
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
}

@MainActor
final class Workbench: ObservableObject {
    static let shared = Workbench()
    @Published var entries: [EditorialEntry] = []
    @Published var focusedID: String?
    @Published var included: Set<String> = []
    @Published var projects: [String: Set<String>] = [:]
    @Published var decisions: [String: [String: String]] = [:]
    @Published var results: [String: EditorialResult] = [:]
    @Published var statuses: [String: String] = [:]
    @Published var family = "fcpxml"
    @Published var version = "1.8"
    @Published var application = "DaVinci Resolve"
    @Published var allowRisky = true
    @Published var outputFolder: URL?
    @Published var busy = false
    @Published var activity = ""
    @Published var alertMessage = ""
    @Published var showAlert = false
    @Published var showResults = false
    @Published var mode = "convert"
    @Published var proxyMediaRoot: URL?
    @Published var proxySuffixes = "S03"
    @Published var proxyPlans: [String: ProxyPlan] = [:]
    @Published var proxyCandidateRow: ProxyRow?
    var jobFolder: URL?
    var quitAfterTask = false
    var cancelRequested = false
    var pendingInputs: [URL] = []
    var current: EditorialEntry? { entries.first { $0.id == focusedID } ?? entries.first }
    var chosen: [EditorialEntry] { entries.filter { included.contains($0.id) } }
    var target: [String: String] { ["family": family, "version": version, "application": application] }
    var versions: [String] { family == "fcpxml" ? (8...14).map { "1.\($0)" } : ["4", "5"] }
    var actionLabel: String {
        guard let entry = current else { return "转换" }
        if entry.family != family { return "转换" }
        let source = Int(entry.version.split(separator: ".").last ?? "") ?? 0
        let destination = Int(version.split(separator: ".").last ?? "") ?? 0
        return destination < source ? "降级" : destination > source ? "升级" : "整理"
    }
    var canExport: Bool {
        !busy && chosen.contains { $0.projects.isEmpty || !(projects[$0.id] ?? []).isEmpty }
    }
    func notify(_ message: String) { alertMessage = message; showAlert = true }
    func start(_ label: String) throws -> URL {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("XMLBridge-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        jobFolder = folder; cancelRequested = false; busy = true; activity = label
        return folder
    }
    func finish() {
        if let folder = jobFolder { try? FileManager.default.removeItem(at: folder) }
        jobFolder = nil; busy = false
        if quitAfterTask { NSApp.terminate(nil) }
        else if !pendingInputs.isEmpty {
            let inputs = pendingInputs; pendingInputs.removeAll(); add(inputs)
        }
    }
    func request(_ action: String, folder: URL) -> [String: Any] {
        ["action": action, "target": target, "allow_risky": allowRisky,
         "cancel_path": folder.appendingPathComponent("cancel").path]
    }
    func cancel() {
        guard let folder = jobFolder else { return }
        cancelRequested = true; activity = "正在取消，已完成的输出会保留…"
        FileManager.default.createFile(atPath: folder.appendingPathComponent("cancel").path, contents: nil)
    }
    func chooseFiles() {
        guard !busy else { return }
        let panel = NSOpenPanel()
        panel.title = "添加剪辑 XML"
        panel.message = "选择 .xml、.fcpxml、.fcpxmld 目录包；也可选文件夹批量扫描"
        panel.prompt = "添加"
        panel.canChooseFiles = true; panel.canChooseDirectories = true
        panel.treatsFilePackagesAsDirectories = false; panel.allowsMultipleSelection = true
        if panel.runModal() == .OK { add(panel.urls) }
    }
    func add(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        if busy { pendingInputs.append(contentsOf: urls); return }
        do {
            let folder = try start("正在分析文件…")
            var payload = request("scan", folder: folder)
            payload["paths"] = urls.map(\.path)
            Task {
                defer { finish() }
                do {
                    let response = try await LocalEngine.call(payload, folder: folder)
                    if let error = response.error { notify(error) }
                    let added = response.entries ?? []
                    for entry in added {
                        if let index = entries.firstIndex(where: { $0.id == entry.id }) { entries[index] = entry }
                        else { entries.append(entry) }
                        projects[entry.id] = Set(entry.projects.map(\.id))
                        included.insert(entry.id); results.removeValue(forKey: entry.id)
                        decisions.removeValue(forKey: entry.id)
                        statuses[entry.id] = entry.readable ? "已分析" : "无法读取"
                    }
                    if let first = added.first { focusedID = first.id }
                    activity = response.cancelled == true ? "分析已取消" : "已分析 \(added.count) 个文件"
                } catch { notify(error.localizedDescription); activity = "分析未完成" }
            }
        } catch { notify(error.localizedDescription) }
    }
    func replan() {
        guard !busy, !entries.isEmpty else { return }
        do {
            let folder = try start("正在更新转换影响…")
            results.removeAll()
            Task {
                defer { finish() }
                do {
                    for index in entries.indices {
                        if cancelRequested { break }
                        let entry = entries[index]
                        var payload = request("plan", folder: folder)
                        payload["path"] = entry.path
                        if !entry.projects.isEmpty { payload["project_ids"] = Array(projects[entry.id] ?? []).sorted() }
                        payload["decisions"] = decisions[entry.id] ?? [:]
                        let response = try await LocalEngine.call(payload, folder: folder)
                        if let error = response.error { throw NSError(domain: "XMLBridge", code: 2, userInfo: [NSLocalizedDescriptionKey: error]) }
                        if let updated = response.entry {
                            entries[index] = updated
                            statuses[entry.id] = updated.readable ? "已分析" : "无法读取"
                        }
                    }
                    activity = cancelRequested ? "分析已取消" : "转换影响已更新"
                } catch { notify(error.localizedDescription) }
            }
        } catch { notify(error.localizedDescription) }
    }
    func changeFamily(_ next: String) {
        family = next; version = next == "fcpxml" ? "1.8" : "5"
        if application == "Final Cut Pro" && next != "fcpxml" { application = "通用" }
        if application == "Adobe Premiere Pro" && next != "xmeml" { application = "通用" }
        decisions.removeAll(); replan()
    }
    func changeApplication(_ next: String) {
        application = next
        if next == "Adobe Premiere Pro" && family != "xmeml" { family = "xmeml"; version = "5" }
        if next == "Final Cut Pro" && family != "fcpxml" { family = "fcpxml"; version = "1.8" }
        decisions.removeAll(); replan()
    }
    func chooseOutput() {
        let panel = NSOpenPanel()
        panel.title = "选择导出位置"; panel.prompt = "使用此文件夹"
        panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK { outputFolder = panel.url }
    }
    func export() {
        guard canExport else { return }
        let queue = chosen
        do {
            let folder = try start("准备导出…")
            for entry in queue { results.removeValue(forKey: entry.id); statuses[entry.id] = "等待导出" }
            Task {
                defer { finish(); showResults = true }
                do {
                    for (index, entry) in queue.enumerated() {
                        if cancelRequested { statuses[entry.id] = "未处理 · 已取消"; continue }
                        if !entry.projects.isEmpty && (projects[entry.id] ?? []).isEmpty {
                            statuses[entry.id] = "未选择项目"; continue
                        }
                        activity = "正在导出 \(index + 1) / \(queue.count)：\(entry.name)"
                        statuses[entry.id] = "正在导出"
                        var payload = request("convert", folder: folder)
                        payload["path"] = entry.path; payload["input_hash"] = entry.inputHash
                        payload["output_dir"] = (outputFolder ?? URL(fileURLWithPath: entry.path).deletingLastPathComponent()).path
                        if !entry.projects.isEmpty { payload["project_ids"] = Array(projects[entry.id] ?? []).sorted() }
                        payload["decisions"] = decisions[entry.id] ?? [:]
                        let response = try await LocalEngine.call(payload, folder: folder)
                        if let result = response.result {
                            results[entry.id] = result
                            statuses[entry.id] = result.status == "success" ? "已生成" : result.generated ? "已生成 · 有问题" : result.status == "cancelled" ? "已取消" : "未生成 · 查看报告"
                        } else {
                            statuses[entry.id] = "导出失败"
                            if let error = response.error { notify(error) }
                        }
                    }
                    let count = queue.filter { results[$0.id]?.generated == true }.count
                    activity = cancelRequested ? "已取消，已生成 \(count) 个文件" : "处理完成，已生成 \(count) / \(queue.count) 个文件"
                } catch { notify(error.localizedDescription); activity = "处理未完成" }
            }
        } catch { notify(error.localizedDescription) }
    }
    func clear() {
        guard !busy else { return }
        entries.removeAll(); included.removeAll(); focusedID = nil
        projects.removeAll(); decisions.removeAll(); results.removeAll(); statuses.removeAll(); activity = ""
        proxyPlans.removeAll()
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        if let icon = NSImage(contentsOf: Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/XMLBridge.icns")) {
            NSApp.applicationIconImage = icon
        }
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if Workbench.shared.busy { Workbench.shared.quitAfterTask = true; Workbench.shared.cancel(); return .terminateCancel }
        return .terminateNow
    }
}

@main
struct XMLBridgeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = Workbench.shared
    init() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if let first = arguments.first, ["--self-test", "--inspect", "--convert", "--native-request", "--legacy-gui"].contains(first) {
            let process = Process(); process.executableURL = LocalEngine.executable
            process.arguments = first == "--legacy-gui" ? Array(arguments.dropFirst()) : arguments
            do { try process.run(); process.waitUntilExit(); exit(process.terminationStatus) }
            catch { fputs("\(error.localizedDescription)\n", stderr); exit(1) }
        }
    }
    var body: some Scene {
        Window("XMLBridge", id: "main") { MainWindow(model: model) }
            .defaultSize(width: 900, height: 640)
            .windowResizability(.contentMinSize)
            .windowToolbarStyle(.unified)
            .commands {
                CommandGroup(after: .newItem) {
                    Button("添加 XML 文件或目录包…", action: model.chooseFiles).keyboardShortcut("o").disabled(model.busy)
                    Button("选择导出位置…", action: model.chooseOutput).disabled(model.busy)
                    Divider()
                    Button("导出选中文件", action: model.export).keyboardShortcut("e").disabled(!model.canExport)
                    Button("取消当前任务", action: model.cancel).disabled(!model.busy)
                }
                CommandGroup(replacing: .help) {
                    Button("XMLBridge 使用说明书") {
                        let manual = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/docs/XMLBridge_使用说明书.pdf")
                        NSWorkspace.shared.open(manual)
                    }
                    Button("许可与作者署名") {
                        let license = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/docs/XMLBridge_NOTICE.txt")
                        NSWorkspace.shared.open(license)
                    }
                    Button("AGPL-3.0 许可全文") {
                        let license = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/docs/XMLBridge_LICENSE.txt")
                        NSWorkspace.shared.open(license)
                    }
                    Button("本版本完整源代码") {
                        if let url = URL(string: "https://github.com/sht2574898282-afk/XMLBridge/tree/v0.3.3") {
                            NSWorkspace.shared.open(url)
                        }
                    }
                    Button("迪儿 Dear 的 GitHub") {
                        if let url = URL(string: "https://github.com/sht2574898282-afk") {
                            NSWorkspace.shared.open(url)
                        }
                    }
                }
            }
    }
}

struct PrimaryButton: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 26, *) { content.buttonStyle(.glassProminent).controlSize(.large) }
        else { content.buttonStyle(.borderedProminent).controlSize(.large) }
    }
}
extension View {
    @ViewBuilder func quietGlass(circle: Bool = false) -> some View {
        if #available(macOS 26, *) {
            if circle { self.glassEffect(.regular, in: Circle()) }
            else { self.glassEffect(.regular, in: Capsule()) }
        } else { self.background(.thinMaterial, in: Capsule()) }
    }
}

struct MainWindow: View {
    @ObservedObject var model: Workbench
    @State private var dropTarget = false
    var body: some View {
        HStack(spacing: 0) {
            if model.entries.count > 1 { queue.frame(width: 218); Divider() }
            Group {
                if let entry = model.current {
                    if model.mode == "proxy" { ProxyRelinkWindow(model: model, entry: entry) }
                    else { ConversionWindow(model: model, entry: entry) }
                }
                else { welcome }
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: model.entries.count > 1 ? 1040 : 840, minHeight: 610)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay {
            if dropTarget { RoundedRectangle(cornerRadius: 20).strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 2, dash: [7,5])).padding(12).allowsHitTesting(false) }
        }
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $dropTarget) { providers in
            guard !model.busy else { return false }
            let group = DispatchGroup(); let lock = NSLock()
            var urls: [URL] = []
            for provider in providers {
                group.enter()
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    if let url { lock.lock(); urls.append(url); lock.unlock() }; group.leave()
                }
            }
            group.notify(queue: .main) { model.add(urls) }
            return !providers.isEmpty
        }
        .onOpenURL { model.add([$0]) }
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("功能", selection: $model.mode) {
                    Text("版本转换").tag("convert")
                    Text("代理回套").tag("proxy")
                }
                .pickerStyle(.segmented)
                .controlSize(.large)
                .font(.system(size: 16, weight: .semibold))
                .frame(width: 340, height: 36)
                .disabled(model.busy)
            }
            ToolbarItemGroup {
                Button(action: model.chooseFiles) { Label("添加文件或目录包", systemImage: "plus") }.disabled(model.busy)
                if !model.entries.isEmpty {
                    Menu {
                        Button("重新分析", action: model.replan)
                        Button("清空文件", action: model.clear)
                    } label: { Label("文件操作", systemImage: "ellipsis.circle") }.disabled(model.busy)
                }
            }
        }
        .alert("XMLBridge", isPresented: $model.showAlert) { Button("知道了", role: .cancel) {} } message: { Text(model.alertMessage) }
        .sheet(isPresented: $model.showResults) { ResultSheet(model: model) }
        .sheet(item: $model.proxyCandidateRow) { row in
            ProxyCandidateSheet(model: model, row: row)
        }
    }
    var welcome: some View {
        VStack {
            Spacer()
            VStack(spacing: 22) {
                if let icon = NSImage(contentsOf: Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/XMLBridge.icns")) {
                    Image(nsImage: icon).resizable().frame(width: 90, height: 90)
                }
                VStack(spacing: 11) {
                    Text("从一个 XML 开始").font(.system(size: 25, weight: .semibold))
                    Text("添加剪辑文件，选择需要交接的版本。").font(.system(size: 13)).foregroundStyle(.secondary)
                }
                Button(action: model.chooseFiles) { Label("添加文件或目录包", systemImage: "plus").padding(.horizontal, 13).padding(.vertical, 6) }
                    .modifier(PrimaryButton()).disabled(model.busy)
                if model.busy { ProgressView(model.activity).controlSize(.small) }
                else { Text("或把文件拖到这个窗口").font(.system(size: 11)).foregroundStyle(.tertiary) }
            }
            Spacer()
            HStack {
                Label("本机处理，原文件保留", systemImage: "lock").font(.system(size: 11)).foregroundStyle(.secondary)
                Spacer()
                if model.busy { Button("取消", action: model.cancel).buttonStyle(.borderless) }
                else { Text("XMLBridge \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")").font(.system(size: 11)).foregroundStyle(.tertiary) }
            }.padding(30)
        }
    }
    var queue: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text("交接文件").font(.headline); Spacer(); Text("\(model.entries.count)").foregroundStyle(.secondary) }.padding(.horizontal, 16).padding(.top, 18)
            List(selection: $model.focusedID) {
                ForEach(model.entries) { entry in
                    HStack(spacing: 9) {
                        Toggle("参与导出", isOn: Binding(get: { model.included.contains(entry.id) }, set: { value in
                            if value { model.included.insert(entry.id) } else { model.included.remove(entry.id) }
                        })).labelsHidden().toggleStyle(.checkbox).disabled(model.busy)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(entry.name).font(.system(size: 12, weight: .medium)).lineLimit(1)
                            Text(model.statuses[entry.id] ?? "已分析").font(.system(size: 10)).foregroundStyle(.secondary)
                        }
                    }.padding(.vertical, 6).tag(entry.id)
                }
            }.listStyle(.sidebar)
            Text("\(model.included.count) 个文件参与导出").font(.system(size: 10)).foregroundStyle(.secondary).padding(16)
        }
    }
}

struct DocumentBadge: View {
    let version: String
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 15).fill(Color.indigo.opacity(0.10)).frame(width: 84, height: 102).rotationEffect(.degrees(-10)).offset(x: -6)
            RoundedRectangle(cornerRadius: 15).fill(Color.indigo.opacity(0.10)).frame(width: 84, height: 102).rotationEffect(.degrees(7)).offset(x: 5)
            RoundedRectangle(cornerRadius: 15).fill(.background).frame(width: 84, height: 108)
                .shadow(color: .black.opacity(0.07), radius: 8, y: 4)
                .overlay { RoundedRectangle(cornerRadius: 15).strokeBorder(Color.indigo.opacity(0.15)) }
            VStack(spacing: 13) {
                Image(systemName: "chevron.left.forwardslash.chevron.right").font(.system(size: 25, weight: .medium))
                Text(version.isEmpty ? "?" : version).font(.system(size: 19, weight: .semibold, design: .rounded))
            }.foregroundStyle(.indigo)
        }.frame(width: 112, height: 122)
    }
}

struct ConversionWindow: View {
    @ObservedObject var model: Workbench
    let entry: EditorialEntry
    @State private var showImpacts = false
    @State private var showProjects = false
    var selectedProjects: [EditorialProject] { entry.projects.filter { model.projects[entry.id]?.contains($0.id) ?? true } }
    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 24) {
                HStack {
                    Label("当前交接", systemImage: "arrow.triangle.swap").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                    Spacer()
                    Button { showProjects = true } label: {
                        Label(entry.projects.count > 1 ? "\(selectedProjects.count) / \(entry.projects.count) 个项目" : entry.projects.first?.name ?? "素材与事件", systemImage: "rectangle.stack")
                            .lineLimit(1)
                    }.buttonStyle(.borderless).font(.system(size: 12)).disabled(model.busy || entry.projects.isEmpty)
                }
                HStack(spacing: 28) {
                    VStack(spacing: 13) {
                        DocumentBadge(version: entry.version)
                        Text(entry.name).font(.system(size: 18, weight: .semibold)).lineLimit(2).truncationMode(.middle).multilineTextAlignment(.center).help(entry.path)
                        Text(entry.typeLabel).font(.system(size: 12)).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity)
                    VStack(spacing: 11) {
                        Image(systemName: "arrow.right").font(.system(size: 18, weight: .medium)).frame(width: 47, height: 47).quietGlass(circle: true)
                        Text(model.actionLabel).font(.system(size: 11)).foregroundStyle(.secondary)
                    }.padding(.bottom, 10)
                    VStack(spacing: 16) {
                        Menu {
                            Button("FCPXML") { model.changeFamily("fcpxml") }
                            Button("Final Cut 7 XML（XMEML）") { model.changeFamily("xmeml") }
                        } label: { Text("目标 \(model.family == "fcpxml" ? "FCPXML" : "Final Cut 7 XML")").font(.system(size: 12)).foregroundStyle(.secondary) }
                        .menuStyle(.borderlessButton).fixedSize()
                        Menu {
                            ForEach(model.versions, id: \.self) { v in Button(v) { model.version = v; model.decisions.removeAll(); model.replan() } }
                        } label: {
                            HStack(spacing: 8) {
                                Text(model.version).font(.system(size: 46, weight: .semibold, design: .rounded)).monospacedDigit()
                                Image(systemName: "chevron.down").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                            }.padding(.horizontal, 20).padding(.vertical, 10)
                        }.menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().quietGlass().accessibilityLabel("目标版本 \(model.version)")
                        Menu {
                            ForEach(["DaVinci Resolve", "Final Cut Pro", "Adobe Premiere Pro", "通用"], id: \.self) { app in
                                Button(app) { model.changeApplication(app) }
                            }
                        } label: { Text(model.application).font(.system(size: 12)) }.menuStyle(.borderlessButton).fixedSize()
                    }.frame(maxWidth: .infinity).disabled(model.busy)
                }.padding(.vertical, 10)
                HStack(spacing: 18) {
                    Label(fpsLabel(selectedProjects), systemImage: "film")
                    Text(durationLabel(selectedProjects))
                    Text("\(selectedProjects.reduce(0) { $0 + $1.clipCount }) 个片段")
                    Text("\(entry.mediaCount) 个素材")
                }.font(.system(size: 11)).foregroundStyle(.secondary)
                Button { showImpacts = true } label: {
                    HStack(spacing: 12) {
                        Image(systemName: entry.issues.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .foregroundStyle(entry.issues.isEmpty ? .green : .orange).font(.system(size: 20)).frame(width: 27)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(entry.issues.isEmpty ? "未发现需要处理的兼容问题" : "\(entry.issues.count) 项内容需要留意").font(.system(size: 13, weight: .medium))
                            Text(entry.issues.isEmpty ? "仍需在目标软件核对实际导入结果。" : entry.blockingCount > 0 ? "\(entry.blockingCount) 项仍需处理，可查看具体原因。" : "问题已记录，当前设置仍可生成文件。")
                                .font(.system(size: 11)).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text("查看影响").font(.system(size: 12)).foregroundStyle(.secondary)
                        Image(systemName: "chevron.right").font(.system(size: 10)).foregroundStyle(.tertiary)
                    }.padding(18).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 15)).contentShape(RoundedRectangle(cornerRadius: 15))
                }.buttonStyle(.plain).disabled(model.busy)
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Toggle("允许有损转换", isOn: Binding(get: { model.allowRisky }, set: { model.allowRisky = $0; model.replan() }))
                            .toggleStyle(.switch).controlSize(.small).font(.system(size: 12, weight: .medium)).fixedSize().disabled(model.busy)
                        Text(model.allowRisky ? "同格式可带问题导出，检查结果如实记录。" : "严格检查，未解决的问题会阻止生成 XML。")
                            .font(.system(size: 11)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("原文件保留").font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }.padding(.horizontal, 42).padding(.top, 22)
            Spacer(minLength: 20)
            if model.busy {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text(model.activity).font(.system(size: 11)).lineLimit(1)
                    Spacer(); Button("取消", action: model.cancel).disabled(model.cancelRequested)
                }.padding(.horizontal, 38).padding(.bottom, 16)
            } else if !model.activity.isEmpty {
                Text(model.activity).font(.system(size: 10)).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 40).padding(.bottom, 12)
            }
            HStack(spacing: 12) {
                Menu {
                    Button("选择文件夹…", action: model.chooseOutput)
                    Button("与源文件放在一起") { model.outputFolder = nil }
                } label: {
                    Label(model.outputFolder?.lastPathComponent ?? "与源文件放在一起", systemImage: "folder").font(.system(size: 12))
                }.menuStyle(.borderlessButton).fixedSize().foregroundStyle(.secondary).disabled(model.busy)
                Spacer()
                if !model.results.isEmpty { Button("查看结果") { model.showResults = true }.buttonStyle(.borderless).disabled(model.busy) }
                Button(action: model.export) {
                    Label(model.entries.count > 1 ? "导出 \(model.included.count) 个文件" : "\(model.actionLabel)并导出", systemImage: "arrow.down.document")
                        .font(.system(size: 13, weight: .semibold)).padding(.horizontal, 10).padding(.vertical, 5)
                }.modifier(PrimaryButton()).disabled(!model.canExport)
            }.padding(.horizontal, 37).padding(.bottom, 25)
        }
        .sheet(isPresented: $showImpacts) { ImpactSheet(model: model, entry: entry) }
        .sheet(isPresented: $showProjects) { ProjectSheet(model: model, entry: entry) }
    }
}

struct ProjectSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var model: Workbench
    let entry: EditorialEntry
    @State private var initialSelection: Set<String> = []
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("选择交接项目").font(.title2.bold())
            Text("\(entry.name) · 全部项目均已扫描").font(.subheadline).foregroundStyle(.secondary)
            ScrollView {
                VStack(spacing: 16) {
                    ForEach(entry.projects) { project in
                        Toggle(isOn: Binding(get: { model.projects[entry.id]?.contains(project.id) ?? false }, set: { selected in
                            if selected { model.projects[entry.id, default: []].insert(project.id) }
                            else { model.projects[entry.id, default: []].remove(project.id) }
                        })) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(project.name)
                                Text("\(fpsLabel([project])) · \(durationLabel([project])) · \(project.clipCount) 个片段").font(.caption).foregroundStyle(.secondary)
                            }
                        }.toggleStyle(.checkbox)
                    }
                }.padding(.vertical, 8)
            }.frame(maxHeight: 330)
            HStack { Text("至少选择一个项目。").font(.caption).foregroundStyle(.secondary); Spacer(); Button("完成") { dismiss() }.modifier(PrimaryButton()) }
        }.padding(30).frame(width: 520)
        .onAppear { initialSelection = model.projects[entry.id] ?? [] }
        .onDisappear { if initialSelection != model.projects[entry.id] ?? [] { model.replan() } }
    }
}

struct ImpactSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var model: Workbench
    let entry: EditorialEntry
    @State private var initialDecisions: [String: String] = [:]
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("交接影响").font(.system(size: 22, weight: .semibold))
            Text("\(entry.name) · \(entry.issues.count) 项问题").font(.system(size: 12)).foregroundStyle(.secondary)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(entry.issues) { issue in
                        VStack(alignment: .leading, spacing: 9) {
                            HStack(alignment: .top) {
                                Text(issue.message).font(.system(size: 13, weight: .semibold)).textSelection(.enabled)
                                Spacer()
                                Text(issue.severity == "error" ? "错误" : issue.severity == "warning" ? "注意" : "提示")
                                    .font(.system(size: 10)).foregroundStyle(issue.severity == "error" ? .orange : .secondary)
                            }
                            let position = [issue.project, issue.clip, issue.time].filter { !$0.isEmpty }.joined(separator: " · ")
                            if !position.isEmpty { Text(position).font(.system(size: 11)).foregroundStyle(.secondary).textSelection(.enabled) }
                            if !issue.details.isEmpty { Text(issue.details).font(.system(size: 11)).foregroundStyle(.secondary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true) }
                            if !issue.choices.isEmpty {
                                Picker("处理", selection: Binding(get: { model.decisions[entry.id]?[issue.id] ?? "automatic" }, set: { value in
                                    if value == "automatic" { model.decisions[entry.id, default: [:]].removeValue(forKey: issue.id) }
                                    else { model.decisions[entry.id, default: [:]][issue.id] = value }
                                })) {
                                    Text(model.allowRisky ? "按已知简化方案处理并记录" : "请选择处理方式").tag("automatic")
                                    ForEach(issue.choices.keys.filter { $0 != "stop" }.sorted(), id: \.self) { action in Text(issue.choices[action] ?? action).tag(action) }
                                    if model.allowRisky && entry.family == model.family { Text("保留原内容并记录（可能不兼容）").tag("preserve") }
                                    Text("停止此文件转换").tag("stop")
                                }.font(.system(size: 11))
                            } else {
                                Text(issue.code == "OFFLINE_MEDIA" ? "保留素材引用，导入后可重新链接。" :
                                     model.allowRisky && entry.family == model.family && entry.readable ? "保留并记录；目标软件可能无法读取此内容。" : "问题将写入报告；无法建立转换规则时不生成 XML。")
                                    .font(.system(size: 10)).foregroundStyle(.secondary)
                            }
                            if !issue.location.isEmpty { Text(issue.location).font(.system(size: 9, design: .monospaced)).foregroundStyle(.tertiary).textSelection(.enabled) }
                        }.padding(.vertical, 16)
                        Divider()
                    }
                    if entry.issues.isEmpty { Text("未发现兼容问题，仍需在目标软件核对实际效果。").padding(.vertical, 30) }
                }
            }.frame(height: 360)
            HStack {
                Text("生成文件与检查通过会分别显示。").font(.system(size: 11)).foregroundStyle(.secondary)
                Spacer(); Button("完成") { dismiss() }.modifier(PrimaryButton())
            }
        }.padding(30).frame(width: 660)
        .onAppear { initialDecisions = model.decisions[entry.id] ?? [:] }
        .onDisappear { if initialDecisions != model.decisions[entry.id] ?? [:] { model.replan() } }
    }
}

struct ResultSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var model: Workbench
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("交接结果").font(.system(size: 22, weight: .semibold))
            Text(model.activity).font(.system(size: 12)).foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    ForEach(model.entries.filter { model.included.contains($0.id) }) { entry in
                        VStack(alignment: .leading, spacing: 12) {
                            HStack {
                                Image(systemName: model.results[entry.id]?.generated == true ? "doc.badge.checkmark" : "doc.badge.ellipsis").foregroundStyle(.blue)
                                Text(entry.name).font(.headline).lineLimit(2)
                                Spacer()
                                Text(model.statuses[entry.id] ?? "未处理").font(.caption).foregroundStyle(.secondary)
                            }
                            if let result = model.results[entry.id] {
                                Text(result.message).font(.system(size: 12)).foregroundStyle(.secondary).textSelection(.enabled)
                                if !result.issues.isEmpty {
                                    Text("交接报告记录了 \(result.issues.count) 项提示与处理决定。").font(.system(size: 11)).foregroundStyle(.secondary)
                                }
                                VStack(spacing: 9) {
                                    check("结构检查", result.checks.structure)
                                    check("时间线核对", result.checks.timeline)
                                    check("目标软件导入", result.checks.host)
                                }.padding(15).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 12))
                                HStack {
                                    if result.generated {
                                        Button("在 Finder 中显示") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result.outputPath)]) }
                                    }
                                    if !result.reportHtml.isEmpty {
                                        Button("打开交接报告") { NSWorkspace.shared.open(URL(fileURLWithPath: result.reportHtml)) }
                                    }
                                }.font(.system(size: 12))
                                if !result.outputPath.isEmpty { Text(result.outputPath).font(.system(size: 10)).foregroundStyle(.secondary).textSelection(.enabled) }
                            }
                        }
                        Divider()
                    }
                }
            }.frame(maxHeight: 420)
            HStack {
                Text("本次未自动执行剪辑软件导入验证。").font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer(); Button("完成") { dismiss() }.modifier(PrimaryButton())
            }
        }.padding(30).frame(width: 640)
    }
    func check(_ label: String, _ value: String?) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value == "passed" ? "通过" : value == "failed" ? "未通过" : "尚未验证")
                .foregroundStyle(value == "passed" ? Color.green : value == "failed" ? Color.orange : Color.secondary)
        }.font(.system(size: 12))
    }
}

func rational(_ text: String) -> Double? {
    let parts = text.replacingOccurrences(of: "s", with: "").split(separator: "/")
    guard let first = parts.first, let numerator = Double(first) else { return nil }
    if parts.count == 1 { return numerator }
    guard parts.count == 2, let denominator = Double(parts[1]), denominator != 0 else { return nil }
    return numerator / denominator
}
func fpsLabel(_ projects: [EditorialProject]) -> String {
    let rates = Array(Set(projects.map(\.fps).filter { !$0.isEmpty })).sorted()
    if rates.count > 1 { return "混合帧率" }
    guard let rate = rates.first, let number = rational(rate) else { return "帧率未知" }
    return String(format: "%.3f", number).replacingOccurrences(of: #"\.?0+$"#, with: "", options: .regularExpression) + " fps"
}
func durationLabel(_ projects: [EditorialProject]) -> String {
    let values = projects.compactMap { rational($0.duration) }
    guard !values.isEmpty, values.count == projects.count else { return "时长未知" }
    let seconds = max(0, values.reduce(0,+))
    guard seconds.isFinite && seconds < 1_000_000_000 else { return "时长未知" }
    let whole = Int(seconds.rounded())
    if whole >= 3600 { return String(format: "%d:%02d:%02d", whole/3600, (whole/60)%60, whole%60) }
    return "\(whole/60) 分 \(whole%60) 秒"
}
