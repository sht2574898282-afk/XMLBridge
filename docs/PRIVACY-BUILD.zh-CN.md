# 分发构建与隐私检查

构建用 CPython 必须安装在不含个人用户名的目录。PyInstaller 会冻结 `_sysconfigdata`，其中的构建前缀也会进入分发文件，普通明文扫描无法发现压缩模块里的路径。

两种架构分别创建独立环境，使用 `requirements-arm64.lock` 或 `requirements-intel.lock`。以下为 Apple Silicon 示例；Intel 将架构改为 `x86_64` 并使用 Intel 清单。

```sh
uv python install --install-dir /Users/Shared/XMLBridgeBuild/python --no-bin cpython-3.12.13-macos-aarch64-none
uv venv /Users/Shared/XMLBridgeBuild/venv-arm64 --python /Users/Shared/XMLBridgeBuild/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12
uv pip install --python /Users/Shared/XMLBridgeBuild/venv-arm64/bin/python -r requirements-arm64.lock
uv pip install --python /Users/Shared/XMLBridgeBuild/venv-arm64/bin/python --no-deps --no-build-isolation -e .
XMLBRIDGE_BUILD_ARCH=arm64 XMLBRIDGE_BUILD_PYTHON=/Users/Shared/XMLBridgeBuild/venv-arm64/bin/python zsh scripts/build_macos.sh dist-arm64
```

构建脚本先检查原始 Python 构建配置，保留完整 `sysconfig`，不通过排除模块规避检查。编译完成后核对全部 Mach-O 架构、最低系统要求与动态库路径，并对签名后的应用及最终 ZIP 分别扫描。ZIP 不保留资源叉、扩展属性、隔离标记或 ACL。

隐私检查递归读取 ZIP、符号链接目标、PyInstaller CArchive 与 PYZ 中的每个模块，检查解压后的字节；不执行归档内代码。不能解析的已识别归档、缺失的冻结 Python 覆盖，以及非白名单用户目录都会使检查失败。检查结果写入对应架构的 `privacy-audit.json` 和 `distribution-privacy-audit.json`；ZIP 通过后才成为正式输出。

默认仅放行中性共享目录和已知上游构建账户路径。添加放行项必须核实来源，不得将发布者账户加入白名单。

最低系统要求取实际打包依赖的最大值，包括 Qt 主库、Python 绑定与 Shiboken。现有 Apple Silicon / Qt 6.12.0 构建需要 macOS 26；Intel / Qt 6.8.3 构建目标仍为 macOS 13。版本号相同或 wheel 的平台标签不能代替 Mach-O 逐文件检查。
