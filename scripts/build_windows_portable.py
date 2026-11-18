# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Assemble a relocatable x64 Windows test build without shipping project sources.

Build tools: CPython 3.12, ziglang 0.15.2, Pillow, LIEF 0.17.6, pefile.
The Windows CPython and wheels must already exist; no code is fetched or run by
this assembler. Native Windows execution remains a separate acceptance step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

import lief
import pefile
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def resources(executable: Path, icon: Path, manifest: Path):
    binary = lief.PE.parse(executable)
    root = lief.PE.ResourceDirectory()
    types = {}
    def add(kind, ident, payload):
        if kind not in types:
            directory = lief.PE.ResourceDirectory()
            directory.id = kind
            types[kind] = directory
        identity = lief.PE.ResourceDirectory()
        identity.id = ident
        data = lief.PE.ResourceData()
        data.id = 1033
        data.content = payload
        identity.add_child(data)
        types[kind].add_child(identity)
    ico = icon.read_bytes()
    reserved,kind,count = struct.unpack_from("<HHH",ico)
    assert reserved==0 and kind==1
    group = bytearray(struct.pack("<HHH",0,1,count))
    for index in range(count):
        width,height,colors,reserved,planes,bits,size,offset = struct.unpack_from("<BBBBHHII",ico,6+16*index)
        add(3,index+1,ico[offset:offset+size])
        group += struct.pack("<BBBBHHIH",width,height,colors,reserved,planes,bits,size,index+1)
    add(14,1,bytes(group))
    add(24,1,manifest.read_bytes())
    for kind in sorted(types):
        root.add_child(types[kind])
    binary.set_resources(root)
    config = lief.PE.Builder.config_t()
    config.resources = True
    binary.write(executable,config)
    parsed = pefile.PE(executable)
    assert parsed.FILE_HEADER.Machine==0x8664 and parsed.OPTIONAL_HEADER.Subsystem==2
    assert {entry.id for entry in parsed.DIRECTORY_ENTRY_RESOURCE.entries}>={3,14,24}
    assert parsed.OPTIONAL_HEADER.AddressOfEntryPoint==binary.optional_header.addressof_entrypoint


def audit_pe(root: Path):
    binaries = sorted(p for p in root.rglob("*")if p.suffix.lower()in {".exe",".dll",".pyd"})
    available = {p.name.lower()for p in binaries}
    external = set()
    for path in binaries:
        pe = pefile.PE(path,fast_load=True)
        assert pe.FILE_HEADER.Machine==0x8664,path.name
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        for entry in list(getattr(pe,"DIRECTORY_ENTRY_IMPORT",[]))+list(getattr(pe,"DIRECTORY_ENTRY_DELAY_IMPORT",[])):
            name = entry.dll.decode().lower()
            if name not in available:
                external.add(name)
        pe.close()
    # These are Windows 10/11 system components; third-party DLLs must ship.
    system = set("kernel32.dll user32.dll advapi32.dll shell32.dll ole32.dll oleaut32.dll ws2_32.dll gdi32.dll comdlg32.dll version.dll winmm.dll imm32.dll setupapi.dll dwmapi.dll uxtheme.dll d3d11.dll dxgi.dll dxguid.dll dwrite.dll d2d1.dll opengl32.dll ntdll.dll msvcrt.dll bcrypt.dll crypt32.dll secur32.dll authz.dll iphlpapi.dll userenv.dll netapi32.dll wtsapi32.dll winspool.drv shlwapi.dll propsys.dll rpcrt4.dll cfgmgr32.dll powrprof.dll comctl32.dll dnsapi.dll wintrust.dll wldap32.dll mpr.dll normaliz.dll hid.dll oleacc.dll urlmon.dll usp10.dll msi.dll dbghelp.dll imagehlp.dll winhttp.dll msimg32.dll wininet.dll dwmapi.dll dhcpcsvc.dll ncrypt.dll kernelbase.dll dxva2.dll d3d9.dll cabinet.dll d3d12.dll d3dcompiler_47.dll uiautomationcore.dll".split())
    missing = sorted(n for n in external if n not in system and not n.startswith(("api-ms-win-","ext-ms-win-")))
    if missing:
        raise RuntimeError("Unbundled non-system DLLs: "+", ".join(missing))
    return {"x64_pe_files":len(binaries),"non_system_dependencies_missing":[],"windows_system_imports":sorted(external)}


def audit_private_paths(root: Path):
    # Known publisher identity is rejected in all plaintext/bytecode/archive data.
    identities = {Path.home().name, str(Path.home())}
    patterns = {value.encode(encoding)for value in identities for encoding in ("utf-8","utf-16-le","utf-16-be")}
    checked = 0
    def check(data,location):
        nonlocal checked
        checked+=1
        if any(pattern.lower()in data.lower()for pattern in patterns):
            raise RuntimeError("Private build identity in "+location)
    for path in sorted(root.rglob("*")):
        if path.is_file():
            raw=path.read_bytes();check(raw,path.relative_to(root).as_posix())
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path)as z:
                    for item in z.infolist():
                        check(z.read(item),item.filename)
    return {"publisher_identity_matches":0,"byte_streams_checked":checked,"scope":"Known publisher home/name, UTF-8/UTF-16, every file and ZIP member; upstream build paths may remain."}


def build(runtime: Path,wheels: Path,destination: Path):
    assert sys.version_info[:2]==(3,12),"Bytecode and bundled Python must both be 3.12"
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xmlbridge-windows-",dir=ROOT/"build")as temp:
        temp=Path(temp);app=temp/"XMLBridge";app.mkdir()
        shutil.copy2(ROOT/"LICENSE", app/"LICENSE")
        shutil.copy2(ROOT/"NOTICE", app/"NOTICE")
        target=app/"runtime";target.mkdir()
        for name in ["python.exe","pythonw.exe","python3.dll","python312.dll","vcruntime140.dll","vcruntime140_1.dll","LICENSE.txt"]:
            shutil.copy2(runtime/name,target/name)
        shutil.copytree(runtime/"DLLs",target/"DLLs",ignore=shutil.ignore_patterns("_test*","_ctypes_test*"))
        shutil.copytree(runtime/"Lib",target/"Lib",ignore=shutil.ignore_patterns("__pycache__","test","tests","idlelib","tkinter","turtledemo","ensurepip","site-packages"))
        packages=target/"Lib/site-packages";packages.mkdir()
        for name in ["lxml","shiboken6"]:
            shutil.copytree(wheels/name,packages/name,ignore=shutil.ignore_patterns("__pycache__","*.lib","*.pyi"))
        pyside=packages/"PySide6";pyside.mkdir()
        for path in (wheels/"PySide6").iterdir():
            if path.suffix in {".py",".json"} or path.name in {"QtCore.pyd","QtGui.pyd","QtWidgets.pyd","Qt6Core.dll","Qt6Gui.dll","Qt6Widgets.dll","pyside6.abi3.dll"} or path.name.startswith(("msvcp","vcruntime","concrt")):
                shutil.copy2(path,pyside/path.name)
        for name in ["support"]:
            shutil.copytree(wheels/"PySide6"/name,pyside/name,ignore=shutil.ignore_patterns("__pycache__"))
        for relative in ["platforms/qwindows.dll","platforms/qoffscreen.dll","styles/qmodernwindowsstyle.dll","imageformats/qico.dll","imageformats/qjpeg.dll"]:
            path=pyside/"plugins"/relative;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(wheels/"PySide6/plugins"/relative,path)
        for path in wheels.glob("*.dist-info"):
            shutil.copytree(path,packages/path.name)
        own=packages/"xmlbridge";own.mkdir()
        modules=["__init__","__main__","models","core","interchange","media_paths","gui","native_bridge","windows_gui","windows_launcher"]
        for name in modules:
            py_compile.compile(str(ROOT/"xmlbridge"/(name+".py")),cfile=str(own/(name+".pyc")),dfile="xmlbridge/"+name+".py",doraise=True)
        shutil.copytree(ROOT/"xmlbridge/schemas",own/"schemas")
        (own/"assets").mkdir()
        shutil.copy2(ROOT/"xmlbridge/assets/XMLBridge.png",own/"assets/XMLBridge.png")

        executable=app/"XMLBridge.exe"
        subprocess.run([sys.executable,"-m","ziglang","cc","-target","x86_64-windows-gnu","-municode","-O2","-s","-Wl,--subsystem,windows",str(ROOT/"scripts/windows/launcher.c"),"-o",str(executable)],check=True)
        icon=temp/"XMLBridge.ico"
        Image.open(ROOT/"xmlbridge/assets/XMLBridge.png").save(icon,sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
        resources(executable,icon,ROOT/"scripts/windows/app.manifest")
        (app/"自检.cmd").write_bytes(('@echo off\r\nchcp 65001 >nul\r\n"%~dp0runtime\\python.exe" -I -B -m xmlbridge.windows_launcher --self-test-json "%~dp0自检结果.json"\r\nif errorlevel 1 (echo 自检失败，请保留错误信息。) else (type "%~dp0自检结果.json")\r\npause\r\n').encode("utf-8"))
        (app/"使用说明.txt").write_text('''XMLBridge 0.2.0 · Windows 64 位测试版 2

本次修订：修正目标版本下拉列表字体过大、菜单过高；统一菜单字号与行高，提高辅助文字可读性；新生成的 HTML 交接报告采用浅色布局。旧报告不会自动改写，请重新导出生成浅色报告。

适用：Windows 10（1809 及以上）/Windows 11 x64（Intel/AMD 64 位电脑）。本包不是 Windows ARM 原生版本。
使用：完整解压 ZIP，然后双击 XMLBridge.exe。不要在 ZIP 中直接运行，也不要只复制 EXE。无需安装 Python，无需管理员权限。

添加文件：点击“添加文件”，或拖入 XML / FCPXML。目录包请选择“添加目录包或文件夹”，选择整个 .fcpxmld 文件夹。
转换：选择目标格式、版本、软件；“查看影响”可查看每项问题。默认允许有损转换，同格式可带问题输出，检查未通过仍会如实记录。
输出：默认与源文件放在一起；可改导出文件夹。原件保留，结果文件与 HTML/JSON 报告放在独立子文件夹。目标软件导入需要你实际检查。

建议依次试：普通降级；中文路径；.fcpxmld 目录包；正常和损坏文件混合；项目选择；取消；将结果导入 Resolve/Premiere 核对。
可先运行“自检.cmd”，确认内置 Qt、格式规则和 1.8→1.9 转换可用。自检结果生成在本目录。
Windows 盘符与 UNC 素材引用已适配。Mac 的 /Volumes 等素材路径不会自动猜测为某个 Windows 盘符，跨机媒体仍需重新链接。

验证状态：Mac 上的 Qt 界面/核心回归与 Windows 二进制/包结构静态检查已执行；尚未做 Windows 真机运行、Windows 剪辑软件导入或本包 Windows 实际启动验收，请在你的机器上测试。
本包未进行 Windows 代码签名。若系统或安全软件拦截，请保留提示截图并反馈，不需要关闭安全软件。
启动错误记录：%LOCALAPPDATA%\\XMLBridge\\Logs\\startup-error.log（仅在本机生成，不会自动上传）。反馈前请去掉路径、项目名等私人内容。
本工具按 AGPL-3.0-only 提供，允许商用与收费，并应履行对应源码与法律声明等义务。许可全文见根目录 LICENSE，作者及源码取得方式见根目录 NOTICE。Python 字节码不替代对应源码。

第三方声明：runtime/LICENSE.txt、runtime/Lib/site-packages 中各组件的 dist-info/licenses，以及 ThirdParty 中的格式定义声明。Qt/PySide/Shiboken 为 6.8.3，lxml 为 6.1.3，CPython 为 3.12.13。
''',encoding="utf-8-sig")
        notices=app/"ThirdParty";notices.mkdir()
        for component in ["QtBase","QtForPython"]:
            shutil.copytree(ROOT/"docs-intel/licenses"/component,notices/component)
        (notices/"NOTICE.txt").write_text('XMLBridge Windows preview: CPython 3.12.13, Qt/PySide6/Shiboken 6.8.3, lxml 6.1.3.\nQt libraries are dynamically loaded; their binaries can be replaced by compatible builds.\nQt/PySide sources: https://code.qt.io/cgit/qt/qtbase.git/?h=v6.8.3 and https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.8.3\nThe complete LGPL/GPL license texts are in the adjacent directories; bundled wheel metadata preserves individual declarations.\nApple FCPXML/XMEML DTD copyright headers are retained in runtime/Lib/site-packages/xmlbridge/schemas. Format documentation: https://developer.apple.com/documentation/professional-video-applications\nCPython source/build distribution: https://github.com/astral-sh/python-build-standalone\nThis notice does not grant additional rights or claim vendor certification.\n',encoding="utf-8")
        record={"product":"XMLBridge","version":"0.2.0-windows-preview2","architecture":"x86_64","windows_target":"10/11 x64","runtime":"CPython 3.12.13 (python-build-standalone)","qt":"6.8.3","lxml":"6.1.3","ui":"Mac-style Qt workbench, compact popup typography, light HTML report","windows_launch":"not_run","windows_nle_import":"not_run","windows_codesign":"unsigned","pe_checks":audit_pe(app),"privacy_checks":audit_private_paths(app)}
        test_log=ROOT/"build/windows-tests.log"
        if test_log.is_file():
            matched=re.search(r"(\d+) passed in ([\d.]+)s",test_log.read_text())
            if matched:
                record["local_regression"]={"platform":sys.platform,"tests_passed":int(matched[1]),"seconds":float(matched[2]),"windows_runtime_acceptance":False}
        (app/"验证记录.json").write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        files=sorted(p for p in app.rglob("*")if p.is_file())
        (app/"文件校验.txt").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.relative_to(app).as_posix()+"\n"for p in files),encoding="utf-8")
        zip_path=destination/"XMLBridge-0.2.0-Windows-x64-preview2.zip"
        pending=destination/(zip_path.name+".pending")
        with zipfile.ZipFile(pending,"w",zipfile.ZIP_DEFLATED,compresslevel=6)as z:
            for path in sorted(p for p in app.rglob("*")if p.is_file()):
                info=zipfile.ZipInfo("XMLBridge/"+path.relative_to(app).as_posix(),date_time=(2026,10,22,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED
                info.create_system=0
                z.writestr(info,path.read_bytes())
        with zipfile.ZipFile(pending)as z:
            assert z.testzip()is None
            for entry in z.infolist():
                expected=(temp/entry.filename).read_bytes()
                assert z.read(entry)==expected
                assert not entry.extra
        pending.replace(zip_path)
        sha=hashlib.sha256(zip_path.read_bytes()).hexdigest()
        (destination/"SHA256SUMS.txt").write_text(sha+"  "+zip_path.name+"\n")
        (destination/"验证记录.json").write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n")
        shutil.copy2(app/"使用说明.txt",destination/"使用说明.txt")
        print(json.dumps({"archive":str(zip_path),"bytes":zip_path.stat().st_size,"sha256":sha,"checks":record},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--runtime",type=Path,required=True)
    parser.add_argument("--wheels",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    build(args.runtime.resolve(),args.wheels.resolve(),args.output.resolve())
