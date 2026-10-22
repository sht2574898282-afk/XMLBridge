# XMLBridge 第三方来源与声明

## 按安装包区分运行组件

两种架构使用不同的 Qt 版本。它们分别记录在各自安装包附带的声明中，不能把 Apple Silicon 的清单当作 Intel 清单。

| 安装包 | CPython | PySide6 / Shiboken / Qt | 系统要求 |
| --- | --- | --- | --- |
| Apple Silicon（arm64） | 3.12.13 | 6.12.0 | macOS 26 及以上 |
| Intel（x86_64） | 3.12.13 | 6.8.3 | macOS 13 及以上 |

Intel 的完整声明与许可见 Release 中的 `XMLBridge-Intel-ThirdParty-Notices-0.2.0.zip`，对应验证记录为 `XMLBridge-Intel-Validation-0.2.0.json`。以下详细组件清单适用于 Apple Silicon 构建。


本文记录 XMLBridge 0.2.0 构建环境中相关组件的来源、版本和原始声明位置。它不授予额外权利，也不替代各组件的完整许可文本。发布附件 `XMLBridge-ThirdParty-Notices-0.2.0.zip` 中的 `licenses/installed` 保存实际环境中收集的原始许可，`sources.json` 记录来源和未找到本地文本的组件；`licenses/upstream-sources.json` 记录官方上游补充文本的下载地址、版本引用和 SHA-256。本公开仓库不包含项目源码归档。仅安装在开发环境中的组件不一定全部进入 `.app`；以最终包内容为准。

## Apple XML 格式定义

`xmlbridge/schemas/FCPXMLv1_8.dtd` 至 `FCPXMLv1_14.dtd`，以及 `XMEMLv4.dtd`、`XMEMLv5.dtd` 是 Apple XML 格式定义。原文件中的 Apple 版权行和注释应原样保留，不能作为 XMLBridge 自有代码重新标注。

- FCPXML 1.14 文件头包含 `Copyright (c) 2011-2026 Apple Inc. All rights reserved.`；较早文件保留其各自原始年份。
- XMEML 文件保留原始 Apple 版权和版本声明。
- 格式说明来源：[Apple Professional Video Applications](https://developer.apple.com/documentation/professional-video-applications)、[Apple Final Cut Pro XML Interchange Format](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/Index/index_of_book.html)。

这些文件的使用范围以原始文件和 Apple 对应条款为准；本工具不据此宣称获得 Apple 认证或商业分发许可。

## 运行组件

| 组件 | 本次环境版本 | 原始来源与声明位置 |
| --- | --- | --- |
| CPython | 3.12.13，Apple Silicon | 当前解释器 `sys.base_prefix/lib/python3.12/LICENSE.txt`；[Python 3.12 许可全文](https://docs.python.org/3.12/license.html) |
| PySide6 / Qt for Python、Shiboken | 6.12.0 | 安装环境 `pyside6-6.12.0.dist-info/METADATA`、`shiboken6-6.12.0.dist-info/METADATA`；[Qt for Python 许可说明](https://doc.qt.io/qtforpython-6/licenses.html)、[Qt for Python 项目源代码](https://code.qt.io/cgit/pyside/pyside-setup.git/) |
| Qt | 6.12.0 | 与 PySide6 wheel 一起提供；[Qt 许可说明](https://doc.qt.io/qt-6/licensing.html)、[Qt 源代码](https://code.qt.io/cgit/qt/qtbase.git/)、[LGPL v3 全文](https://www.gnu.org/licenses/lgpl-3.0.html) |
| lxml | 6.1.3 | `lxml-6.1.3.dist-info/licenses/LICENSE.txt`、`LICENSES.txt`；[lxml 项目](https://lxml.de/)、[lxml 原始声明](https://github.com/lxml/lxml/blob/master/LICENSES.txt) |

这里的 `*.dist-info` 路径相对于构建环境 `.venv/lib/python3.12/site-packages/`。当前 Qt for Python wheel 的这些 metadata 中有许可标识，但没有随 wheel 找到完整许可文本；因此另从 Qt 官方 `code.qt.io` 的 `v6.12.0` 引用获取原文，保存在随文 `licenses/QtBase/LICENSES`、`licenses/QtForPython/LICENSES` 等目录。包括 LGPL v3、GPL v2 / v3、Qt GPL exception 1.0、BSD、Apache 及上游目录中的其他原始文本；每个文件的精确来源与校验值见 `licenses/upstream-sources.json`。

Qt for Python 文档另列所含第三方组件；通用许可文本目录不替代组件各自源码中的版权声明。保留的上游商业许可引用文件只是原始目录的一部分，不代表本工具取得该商业许可。

lxml 的原始 `LICENSES.txt` 还列出二进制 wheel 涉及的 zlib、iconv、libxml2、libxslt、libexslt，以及部分资源、衍生代码的声明；应保留完整文件，而不是只抄写“BSD”这一标签。

## 构建和检查工具

| 工具 | 本次环境版本 | 原始来源与声明位置 |
| --- | --- | --- |
| PyInstaller | 6.22.3 | `pyinstaller-6.22.3.dist-info/licenses/COPYING.txt`；[PyInstaller 许可与引导程序例外说明](https://pyinstaller.org/en/stable/license.html) |
| pyinstaller-hooks-contrib | 2026.8 | `pyinstaller_hooks_contrib-2026.8.dist-info/licenses/LICENSE`；[项目源代码](https://github.com/pyinstaller/pyinstaller-hooks-contrib) |
| Pillow | 13.0.0 | `pillow-13.0.0.dist-info/licenses/LICENSE`；[Pillow 许可](https://pillow.readthedocs.io/en/stable/about.html#license)；仅用于生成图标 |
| pytest | 9.1.1 | `pytest-9.1.1.dist-info/licenses/LICENSE`；[pytest 许可](https://docs.pytest.org/en/stable/license.html)；用于测试 |

构建、测试的传递依赖还包括 setuptools、packaging、altgraph、macholib、iniconfig、pluggy 和 Pygments。这些组件在安装环境中能找到的原始许可文本保存在随文目录。精确版本清单只说明环境，不保证跨平台逐字节重现构建。

OpenTimelineIO 用作公开适配器和格式行为的研究参考；当前 XMLBridge 不依赖或打包 OpenTimelineIO。Final Cut Pro、Premiere Pro 和 DaVinci Resolve 的名称仅用于描述交接目标和实测环境，其商标归各自权利人所有。
