# Intel 构建与验证

Intel 版本保留 XMLBridge 0.2.0 的原生界面与转换功能，输出独立的 x86_64 应用。最低系统目标为 macOS 13；使用 Qt for Python 6.8.3，避免 Qt 6.12 实际要求 macOS 14.4 的限制。运行交付应用不需要 Python。

## 构建环境

使用 Intel Mac，或已经可以运行 Rosetta 的 Apple Silicon Mac。需要 Xcode 命令行工具、`uv` 及网络；不要在 ARM 虚拟环境中安装 Intel 依赖。

```sh
uv python install cpython-3.12.13-macos-x86_64-none --no-bin
uv venv .venv-intel --python cpython-3.12.13-macos-x86_64-none
uv pip install --python .venv-intel/bin/python -r requirements-intel.lock
uv pip install --python .venv-intel/bin/python --no-deps --no-build-isolation -e .
QT_QPA_PLATFORM=offscreen .venv-intel/bin/python -m pytest -q
XMLBRIDGE_BUILD_ARCH=x86_64 zsh scripts/build_macos.sh dist-intel
```

`docs-intel` 保存这次 Intel 依赖对应的声明、原始许可文本和来源记录。常规 ARM 构建继续使用 `.venv` 与 `docs`。两种架构分别使用 `build/arm64`、`build/x86_64`，不共用中间二进制文件。

构建变量：`XMLBRIDGE_BUILD_ARCH` 选择 `arm64` 或 `x86_64`；`XMLBRIDGE_BUILD_PYTHON` 可指定匹配架构的 Python；`XMLBRIDGE_BUILD_DOCS` 可指定文档目录。默认系统目标取原生界面要求与实际 Qt 二进制要求中的较高者；`XMLBRIDGE_MACOS_MIN` 可显式指定，但整包检查会拒绝低于依赖要求的值。

## 构建门槛

构建脚本检查 Python 架构，编译匹配的 SwiftUI 启动程序，再检查包内全部 Mach-O 二进制的目标架构、最低系统版本、包外符号链接和非系统绝对动态库路径，最后签名并运行自检。检查结果写入对应架构的 `bundle-audit.json`。

Apple Silicon 上执行 Intel 代码、通过回归测试和实际转换，可证明这些场景在 Rosetta 下运行。Intel 真机、macOS 13 实机，以及剪辑软件中的真实导入仍需分别验收，不能由构建结果推定。
