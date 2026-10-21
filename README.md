<p align="center"><img src="logo.png" width="112" alt="XMLBridge 图标"></p>

<h1 align="center">XMLBridge</h1>
<p align="center">影视 XML 版本迁移与工程交接 · macOS 本地应用</p>

<p align="center"><a href="https://github.com/sht2574898282-afk/XMLBridge/releases/tag/v0.2.0">下载安装包</a> · <a href="USAGE.zh-CN.md">使用指南</a> · <a href="CAPABILITIES.zh-CN.md">支持与验证范围</a> · <a href="https://github.com/sht2574898282-afk/XMLBridge/issues">反馈问题</a></p>

![XMLBridge 原生转换界面](conversion.png)

XMLBridge 帮助剪辑师和工作室分析剪辑 XML、迁移格式版本，并在已支持的范围内交接可编辑时间线。文件在本机处理，导出新文件及报告，原文件保持不变。

本仓库用于发布安装包、产品说明与问题反馈，**项目源码不公开**。GitHub 自动生成的 “Source code” 下载项是本仓库中的说明资料；安装应用请选择下方对应芯片的 `macOS-arm64` 或 `macOS-x86_64` ZIP。

## 下载与安装

当前版本：**0.2.0 预览版**。

1. 进入 [Releases 下载页](https://github.com/sht2574898282-afk/XMLBridge/releases/tag/v0.2.0)，M 系列下载 `XMLBridge-0.2.0-macOS-arm64.zip`；Intel 下载 `XMLBridge-0.2.0-macOS-x86_64.zip`。
2. 解压，将 `XMLBridge.app` 拖入“应用程序”文件夹，然后打开。
3. 如需完整操作说明，下载同页的中文 PDF 或可编辑 Word 说明书。

| 项目 | 当前要求与状态 |
| --- | --- |
| 芯片 | Apple Silicon（M 系列）与 Intel（x86_64）分别下载 |
| 系统 | Apple Silicon 包的依赖要求 macOS 14.4 及以上；Intel 包目标 macOS 13 及以上。当前实机环境 macOS 27.0.1，其他系统尚未实机验收 |
| 运行环境 | 无需安装 Python |
| 签名与公证 | 使用本地临时签名，尚未经过 Developer ID 签名及 Apple 公证 |

首次打开可能出现“无法验证开发者”提示。确认下载来自本仓库、校验值一致后，可在尝试打开应用后，进入“系统设置 → 隐私与安全性 → 仍要打开”。具体说明见 [Apple 官方帮助](https://support.apple.com/zh-cn/102445)。若系统提示含恶意内容或文件损坏，应停止并反馈，重新核对下载文件。

## 能做什么

| 输入或转换 | 范围 |
| --- | --- |
| FCPXML 版本迁移 | 1.8–1.14 版本识别、升级与降级 |
| 旧式 Final Cut XML | XMEML 4 / 5 识别与版本迁移 |
| 跨格式交接 | FCPXML ↔ XMEML 的基础剪辑与已支持参数；复杂结构有明确限制 |
| 文件与目录包 | `.xml`、`.fcpxml`、`.fcpxmld`，支持拖入、系统选择窗口和批量添加 |
| 项目选择 | 扫描文件内项目或序列，选择需要导出的对象 |
| 影响与报告 | 显示兼容问题和处理决定；输出 HTML / JSON 报告 |

添加文件 → 选择项目、目标格式和版本 → 查看影响 → 导出 → 在目标剪辑软件中核对。

默认开启“允许有损转换”：已知简化会应用并报告，同格式的未知内容会保留并报告；结构检查未通过也可以生成文件，结果明确标为有问题。关闭开关可使用严格模式。

损坏 XML、无效版本、无效项目选择、分析后改变的源文件，以及跨格式缺少可靠映射的内容仍会停止。**文件生成、结构检查、时间线核对和目标软件导入是不同状态。** 工具不会将本次输出自动标为已通过宿主软件验证。

## 交接前需要知道

- 曲线变速、复杂转场、嵌套、多机位、第三方效果、复杂字幕、色彩与特殊音频路由，不能一概视为可跨格式保留。完整范围见 [能力说明](CAPABILITIES.zh-CN.md)。
- 外部媒体保留引用，工具不会自动收集全部素材、转码或渲染；离线素材需要在目标软件重新链接。
- 输出报告可能包含媒体路径、项目名称和时间码。反馈问题时请先去除客户或其他不宜公开的信息。

## Intel 版本验证

Intel 包已在 Apple Silicon 的 Rosetta 下完成 409 项回归测试、29 个二进制架构与系统目标检查，以及打包自检和界面转换核对。**Intel 真机、macOS 13 实机与本 Intel 构建的剪辑软件导入尚未验收。** 详情见 Release 的 Intel 使用说明和验证记录。

## 验证状态

0.2.0 已完成 409 项 Python 回归测试、打包自检，以及原生界面的文件选择、目录包降级和批量部分失败验证。该版本新增的有损导出结果尚未重新执行剪辑软件导入验收。

历史样例测试覆盖 DaVinci Resolve 21.1.1 的部分基础交接，以及 Final Cut Pro 11.2 的指定 FCPXML 1.9 样例。Final Cut Pro 12.4 与 Premiere 尚未完成对应真实导入验证。历史样例通过不代表任意工程通过；详情见 [支持与验证范围](CAPABILITIES.zh-CN.md)。

## 反馈

请通过 [Issues](https://github.com/sht2574898282-afk/XMLBridge/issues) 提供：应用版本、macOS 版本、输入格式版本、目标格式版本、目标剪辑软件版本、复现步骤，以及去除敏感信息后的报告或最小样例。

第三方组件、格式定义及其许可见 [第三方声明](THIRD_PARTY_NOTICES.md) 和应用内附带文本。Final Cut Pro、Premiere Pro 与 DaVinci Resolve 的名称用于描述交接目标与测试环境，不表示厂商认证。
