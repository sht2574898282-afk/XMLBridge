// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2026 迪儿 Dear
// Author: https://github.com/sht2574898282-afk
import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct ProxyCandidate: Decodable, Identifiable, Sendable {
    let path: String
    let name: String
    let fps: String
    let frames: Int?
    let duration: String
    let timecode: String
    let size: String
    let error: String
    var id: String { path }
}

struct ProxyRow: Decodable, Identifiable, Sendable {
    let id: String
    let sourceName: String
    let sourcePath: String
    let sourceFps: String
    let sourceFrames: Int?
    let sourceTimecode: String
    let status: String
    let statusLabel: String
    let adopted: Bool
    let exportable: Bool
    let targetPath: String
    let targetName: String
    let targetStat: [Int64]?
    let reasons: [String]
    let rule: String
    let candidates: [ProxyCandidate]
    var selection: [String: Any] {
        ["source_id": id, "target_path": targetPath, "adopted": adopted,
         "target_stat": targetStat ?? []]
    }
}

struct ProxyPlan: Decodable, Sendable {
    let path: String
    let inputHash: String
    let mediaRoot: String
    let suffixes: [String]
    var rows: [ProxyRow]
    let counts: [String: Int]
    let summary: [String: IntOrString]
    var exportCount: Int { rows.filter(\.exportable).count }
    var pendingCount: Int { rows.filter { !$0.exportable }.count }
}

enum IntOrString: Decodable, Sendable {
    case integer(Int)
    case string(String)
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Int.self) { self = .integer(value) }
        else { self = .string(try container.decode(String.self)) }
    }
}

extension Workbench {
    func chooseProxyRoot() {
        guard !busy else { return }
        let panel = NSOpenPanel()
        panel.title = "选择原片文件夹"; panel.prompt = "使用这个文件夹"
        panel.canChooseFiles = false; panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK {
            proxyMediaRoot = panel.url
            proxyPlans.removeAll()
            activity = "已选择原片文件夹，点击扫描匹配"
        }
    }

    func scanProxy() {
        guard !busy, let entry = current, let mediaRoot = proxyMediaRoot else { return }
        let suffixes = proxySuffixes.replacingOccurrences(of: "，", with: ",").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard !suffixes.isEmpty else { notify("请填写至少一个代理尾缀，例如 S03"); return }
        do {
            let folder = try start("正在匹配代理与原片…")
            var payload = request("proxy_scan", folder: folder)
            payload["path"] = entry.path; payload["input_hash"] = entry.inputHash
            payload["media_root"] = mediaRoot.path; payload["suffixes"] = suffixes
            Task {
                defer { finish() }
                do {
                    let response = try await LocalEngine.call(payload, folder: folder)
                    if let error = response.error { notify(error); activity = "匹配未完成" }
                    else if let plan = response.proxyPlan {
                        proxyPlans[entry.id] = plan
                        activity = "找到 \(plan.rows.count) 个尾缀候选；请采用确认对应的原片"
                    }
                } catch { notify(error.localizedDescription); activity = "匹配未完成" }
            }
        } catch { notify(error.localizedDescription) }
    }

    func chooseProxy(_ row: ProxyRow, target: String, adopted: Bool) {
        guard !busy, let entry = current, let plan = proxyPlans[entry.id] else { return }
        do {
            let folder = try start(adopted ? "正在核对所选原片…" : "正在撤销采用…")
            var payload = request("proxy_choose", folder: folder)
            payload["path"] = entry.path; payload["input_hash"] = plan.inputHash
            payload["source_id"] = row.id; payload["target_path"] = target
            payload["candidate_paths"] = row.candidates.map(\.path)
            payload["adopted"] = adopted; payload["rule"] = row.rule
            Task {
                defer { finish() }
                do {
                    let response = try await LocalEngine.call(payload, folder: folder)
                    if let error = response.error { notify(error) }
                    else if let updated = response.proxyRow,
                            var value = proxyPlans[entry.id], let index = value.rows.firstIndex(where: { $0.id == row.id }) {
                        value.rows[index] = updated; proxyPlans[entry.id] = value
                        activity = updated.exportable ? "已采用：\(updated.targetName)" : updated.statusLabel + "：" + updated.reasons.joined(separator: "；")
                    }
                } catch { notify(error.localizedDescription) }
            }
        } catch { notify(error.localizedDescription) }
    }

    func exportProxy() {
        guard !busy, let entry = current, let plan = proxyPlans[entry.id], plan.exportCount > 0 else { return }
        let panel = NSSavePanel()
        panel.title = "生成原片回套 XML"; panel.prompt = "生成 XML"
        panel.allowedContentTypes = [.xml]
        panel.nameFieldStringValue = entry.name + "_原片回套.xml"
        panel.directoryURL = outputFolder
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let destination = panel.url else { return }
        do {
            let folder = try start("正在生成原片回套 XML…")
            var payload = request("proxy_export", folder: folder)
            payload["path"] = entry.path; payload["input_hash"] = plan.inputHash
            payload["media_root"] = plan.mediaRoot; payload["suffixes"] = plan.suffixes
            payload["selections"] = plan.rows.filter(\.exportable).map(\.selection)
            payload["output_path"] = destination.path
            Task {
                defer { finish() }
                do {
                    let response = try await LocalEngine.call(payload, folder: folder)
                    if let error = response.error { notify(error); activity = "未生成 XML" }
                    else if let result = response.result {
                        results[entry.id] = result
                        included.insert(entry.id)
                        statuses[entry.id] = result.generated ? "已回套 \(plan.exportCount) 个原片" : "未生成"
                        activity = result.message
                    }
                } catch { notify(error.localizedDescription); activity = "生成未完成" }
            }
        } catch { notify(error.localizedDescription) }
    }
}

struct ProxyRelinkWindow: View {
    @ObservedObject var model: Workbench
    let entry: EditorialEntry
    var plan: ProxyPlan? { model.proxyPlans[entry.id] }
    var body: some View {
        VStack(spacing: 0) {
            header
            if entry.family != "xmeml" {
                Spacer()
                Label("代理回套目前支持 Premiere 导出的 Final Cut 7 XML（4 / 5）。", systemImage: "doc.text.magnifyingglass")
                    .font(.system(size: 13)).foregroundStyle(.secondary).padding(32)
                Spacer()
            } else {
                controls
                if let plan {
                    HStack {
                        Text("\(plan.rows.count) 个候选素材").font(.system(size: 12, weight: .medium))
                        Text("已采用 \(plan.exportCount) · 待处理 \(plan.pendingCount)").font(.system(size: 11)).foregroundStyle(.secondary)
                        Spacer()
                        Text("S03 只查找候选，不判断品牌").font(.system(size: 10)).foregroundStyle(.secondary)
                    }.padding(.horizontal, 38).padding(.top, 14).padding(.bottom, 8)
                    if plan.rows.isEmpty {
                        Spacer()
                        Text("没有找到符合当前尾缀规则的素材。可调整尾缀后重新扫描。").font(.system(size: 12)).foregroundStyle(.secondary)
                        Spacer()
                    } else {
                        ScrollView {
                            LazyVStack(spacing: 0) {
                                ForEach(plan.rows) { row in proxyRow(row); Divider().padding(.leading, 40) }
                            }.padding(.horizontal, 24)
                        }
                    }
                } else {
                    Spacer()
                    VStack(spacing: 11) {
                        Image(systemName: "link").font(.system(size: 30)).foregroundStyle(.indigo.opacity(0.7))
                        Text("为剪辑时间线换回原片").font(.system(size: 16, weight: .medium))
                        Text("选择原片文件夹并扫描，再采用确认对应的素材。").font(.system(size: 12)).foregroundStyle(.secondary)
                    }
                    Spacer()
                }
            }
            footer
        }
    }

    var header: some View {
        HStack(spacing: 20) {
            DocumentBadge(version: entry.version).scaleEffect(0.66).frame(width: 76, height: 86)
            VStack(alignment: .leading, spacing: 8) {
                Text("代理回套原片").font(.system(size: 22, weight: .semibold))
                Text(entry.name).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(1).help(entry.path)
                Text("输出新的原片 XML，原文件保留。").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: "arrow.triangle.swap").font(.system(size: 23)).foregroundStyle(.indigo)
        }.padding(.horizontal, 34).padding(.top, 18).padding(.bottom, 10)
    }

    var controls: some View {
        VStack(spacing: 12) {
            HStack {
                Label("原片文件夹", systemImage: "folder").font(.system(size: 12))
                Text(model.proxyMediaRoot?.path ?? "尚未选择").font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle).help(model.proxyMediaRoot?.path ?? "")
                Spacer()
                Button("选择…", action: model.chooseProxyRoot).controlSize(.small)
            }
            HStack(spacing: 10) {
                Text("代理尾缀").font(.system(size: 12))
                TextField("S03, _Proxy", text: $model.proxySuffixes)
                    .textFieldStyle(.roundedBorder).frame(width: 180)
                    .onChange(of: model.proxySuffixes) { _ in model.proxyPlans.removeAll() }
                Text("仅匹配文件名末尾").font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer()
                Button("扫描匹配", action: model.scanProxy).disabled(model.proxyMediaRoot == nil)
            }
        }.padding(16).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 15))
            .padding(.horizontal, 36).disabled(model.busy)
    }

    func proxyRow(_ row: ProxyRow) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Toggle("采用 \(row.sourceName)", isOn: Binding(get: { row.adopted }, set: { selected in
                if !row.targetPath.isEmpty { model.chooseProxy(row, target: row.targetPath, adopted: selected) }
                else if selected { model.proxyCandidateRow = row }
            })).labelsHidden().toggleStyle(.checkbox).padding(.top, 4).disabled(model.busy)
            VStack(alignment: .leading, spacing: 7) {
                HStack {
                    Text(row.sourceName).font(.system(size: 12, weight: .medium)).lineLimit(1)
                    Spacer()
                    Text(row.statusLabel).font(.system(size: 10)).foregroundStyle(row.exportable ? Color.green : Color.secondary)
                }
                if !row.targetPath.isEmpty {
                    Label(row.targetName, systemImage: "arrow.turn.down.right").font(.system(size: 11)).foregroundStyle(.secondary)
                    Text(row.targetPath).font(.system(size: 10)).foregroundStyle(.tertiary).lineLimit(1).truncationMode(.middle).help(row.targetPath)
                } else {
                    Text("\(row.candidates.count) 个原片候选").font(.system(size: 11)).foregroundStyle(.secondary)
                }
                if row.status != "matched", let reason = row.reasons.first {
                    Text(reason).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Button(row.candidates.count > 1 ? "选择候选…" : "选择原片…") { model.proxyCandidateRow = row }
                .controlSize(.small).disabled(model.busy)
        }.padding(.vertical, 14).padding(.horizontal, 12)
    }

    var footer: some View {
        VStack(alignment: .leading, spacing: 11) {
            if model.busy {
                HStack { ProgressView().controlSize(.small); Text(model.activity).font(.system(size: 11)); Spacer(); Button("取消", action: model.cancel).disabled(model.cancelRequested) }
            } else if !model.activity.isEmpty {
                Text(model.activity).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(2)
            }
            if let result = model.results[entry.id], result.generated {
                HStack {
                    Image(systemName: "doc.badge.checkmark").foregroundStyle(.green)
                    Text(URL(fileURLWithPath: result.outputPath).lastPathComponent).font(.system(size: 12, weight: .medium)).lineLimit(1).help(result.outputPath)
                    Spacer()
                    Button("显示 XML") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result.outputPath)]) }.controlSize(.small)
                }
            }
            HStack {
                Text("帧率与一帧差异仅记录，剪辑点不自动调整。").font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer()
                if model.results[entry.id] != nil { Button("查看结果") { model.showResults = true }.buttonStyle(.borderless) }
                Button(action: model.exportProxy) {
                    Label("生成原片回套 XML", systemImage: "arrow.down.document").font(.system(size: 13, weight: .semibold)).padding(.horizontal, 8).padding(.vertical, 5)
                }.modifier(PrimaryButton()).disabled(model.busy || (plan?.exportCount ?? 0) == 0)
            }
        }.padding(.horizontal, 36).padding(.top, 12).padding(.bottom, 24)
    }
}

struct ProxyCandidateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var model: Workbench
    let row: ProxyRow
    @State private var selected: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 17) {
            Text("选择对应原片").font(.system(size: 22, weight: .semibold))
            Text(row.sourceName).font(.system(size: 13, weight: .medium)).textSelection(.enabled)
            Text("XML：\(row.sourceFps) fps · \(row.sourceFrames.map { "\($0) 帧" } ?? "帧数未知") · \(row.sourceTimecode)")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            if row.candidates.isEmpty {
                Text("当前文件夹中没有同名候选，可以手动指定原片。").font(.system(size: 12)).foregroundStyle(.secondary).padding(.vertical, 40)
            } else {
                ScrollView {
                    VStack(spacing: 10) {
                        ForEach(row.candidates) { candidate in
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: selected == candidate.path ? "largecircle.fill.circle" : "circle").foregroundStyle(.indigo).padding(.top, 2)
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(candidate.name).font(.system(size: 13, weight: .medium))
                                    Text(candidate.path).font(.system(size: 11)).foregroundStyle(.secondary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                                    Text(candidate.error.isEmpty ? "\(candidate.fps) fps · \(candidate.duration) · \(candidate.timecode) · \(candidate.size)" : candidate.error)
                                        .font(.system(size: 10)).foregroundStyle(.secondary)
                                }
                                Spacer(minLength: 0)
                                Button("预览") { NSWorkspace.shared.open(URL(fileURLWithPath: candidate.path)) }.controlSize(.small)
                            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                                .background(selected == candidate.path ? Color.indigo.opacity(0.09) : Color.secondary.opacity(0.05), in: RoundedRectangle(cornerRadius: 12))
                                .contentShape(RoundedRectangle(cornerRadius: 12)).onTapGesture { selected = candidate.path }
                                .accessibilityElement(children: .contain)
                                .accessibilityAction(named: Text("选择这个候选")) { selected = candidate.path }
                        }
                    }
                }.frame(maxHeight: 330)
            }
            Text("按日期、机位和卡目录确认来源；同参数不代表同一镜头。帧率和一帧差异不自动修正。").font(.system(size: 11)).foregroundStyle(.secondary)
            HStack {
                Button("手动指定…") {
                    let panel = NSOpenPanel(); panel.title = "指定对应原片"
                    panel.canChooseFiles = true; panel.canChooseDirectories = false
                    if panel.runModal() == .OK, let url = panel.url {
                        model.chooseProxy(row, target: url.path, adopted: true); dismiss()
                    }
                }
                Spacer()
                Button("取消") { dismiss() }
                Button("采用这个原片") {
                    if let selected { model.chooseProxy(row, target: selected, adopted: true); dismiss() }
                }.modifier(PrimaryButton()).disabled(selected == nil || model.busy)
            }
        }.padding(30).frame(width: 690)
            .onAppear { selected = row.targetPath.isEmpty ? nil : row.targetPath }
    }
}
