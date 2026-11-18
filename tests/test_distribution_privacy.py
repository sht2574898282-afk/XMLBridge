from io import BytesIO
import importlib.util
import marshal
from pathlib import Path
import stat
import struct
import zipfile
import zlib

from PyInstaller.archive.writers import CArchiveWriter

from scripts.audit_distribution_privacy import PrivacyAudit


def zip_bytes(entries):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return stream.getvalue()


def pyz_bytes(value):
    code = compile("build_time_vars = " + repr({"LIBDIR": value}), "_sysconfigdata.py", "exec")
    compressed = zlib.compress(marshal.dumps(code))
    toc = marshal.dumps({"_sysconfigdata__darwin_darwin": (0, 12, len(compressed))})
    return b"PYZ\0" + importlib.util.MAGIC_NUMBER + struct.pack("!i", 12 + len(compressed)) + compressed + toc


def test_private_path_in_plain_data():
    audit = PrivacyAudit()
    audit.scan_blob(b"/Users/private-build-account/library", "plain")
    assert not audit.report()["passed"]


def test_small_nested_standard_library_zip():
    audit = PrivacyAudit()
    audit.scan_blob(zip_bytes([("base_library.zip", zip_bytes([("config.pyc", b"/home/private-account/bin")]))]), "outer.zip")
    assert not audit.report()["passed"]
    assert audit.counts["zip_archives"] == 2


def test_prefixed_valid_zip_cannot_bypass_recursive_scan():
    prefixed = b"launcher-prefix" + zip_bytes([("config.pyc", b"/Users/private-account/lib")])
    assert zipfile.is_zipfile(BytesIO(prefixed))
    audit = PrivacyAudit()
    audit.scan_blob(prefixed, "prefixed.zip")
    assert not audit.report()["passed"]
    assert audit.counts["zip_archives"] == 1


def test_compressed_frozen_sysconfig_is_inspected(tmp_path):
    pyz = tmp_path / "PYZ.pyz"
    pyz.write_bytes(pyz_bytes("/Users/private-build-account/lib"))
    core = tmp_path / "core"
    CArchiveWriter(str(core), [("PYZ.pyz", str(pyz), False, "z")], "libpython3.12.dylib")
    audit = PrivacyAudit()
    audit.scan_blob(zip_bytes([("XMLBridgeCore", core.read_bytes())]), "distribution.zip")
    report = audit.report()
    assert not report["passed"]
    assert audit.counts["carchives"] == audit.counts["pyz_archives"] == audit.counts["pyz_modules"] == 1
    assert "_sysconfigdata__darwin_darwin" in report["findings"][0]["location"]


def test_large_compressed_module_has_no_four_megabyte_scan_cutoff():
    audit = PrivacyAudit()
    audit.scan_blob(pyz_bytes("x" * 4_100_000 + "/Users/private-account/lib"), "modules.pyz")
    assert not audit.report()["passed"]
    assert audit.counts["pyz_modules"] == 1


def test_corrupt_recognized_archive_cannot_pass():
    audit = PrivacyAudit()
    audit.scan_blob(b"PYZ\0not a valid archive", "corrupt.pyz")
    report = audit.report()
    assert not report["passed"] and report["errors"]


def test_missing_frozen_archive_coverage_cannot_pass():
    audit = PrivacyAudit()
    audit.scan_blob(b"ordinary non-application data", "empty")
    assert not audit.report(require_frozen_python=True)["passed"]


def test_zip_symlink_is_inspected_as_link_target():
    entry = zipfile.ZipInfo("Frameworks/base_library.zip")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    audit = PrivacyAudit()
    audit.scan_blob(zip_bytes([(entry, b"../Resources/base_library.zip")]), "app.zip")
    assert audit.report()["passed"]
    assert audit.counts["symlinks"] == 1


def test_private_symlink_target_is_rejected():
    entry = zipfile.ZipInfo("Frameworks/base_library.zip")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    audit = PrivacyAudit()
    audit.scan_blob(zip_bytes([(entry, b"/Users/private-account/base_library.zip")]), "app.zip")
    assert not audit.report()["passed"]


def test_neutral_and_known_upstream_paths_remain_allowed():
    audit = PrivacyAudit()
    audit.scan_blob(pyz_bytes("/Users/Shared/XMLBridgeBuild/python/lib"), "modules.pyz")
    audit.scan_blob(b"/Users/qt/work /Users/runner/work /Users/Barney/Python", "vendor")
    assert audit.report()["passed"]


def test_non_ascii_home_account_is_rejected():
    audit = PrivacyAudit()
    audit.scan_blob("/Users/构建账户/工作目录".encode(), "unicode")
    assert not audit.report()["passed"]
