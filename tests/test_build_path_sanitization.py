from scripts.audit_distribution_privacy import PrivacyAudit
from scripts.sanitize_frozen_build_paths import sanitize_module


def sample_path():
    # Synthetic components, never a developer's actual temporary directory.
    return b"/".join((b"", b"private", b"var", b"folders", b"xx", b"a" * 30, b"T", b"build", b"python"))


def test_neutralization_preserves_marshaled_string_lengths_and_unrelated_data():
    raw = b"prefix\x00" + sample_path() + b"/lib\x00suffix\xff"
    clean, count = sanitize_module(raw)
    assert count == 1 and len(clean) == len(raw)
    assert clean.startswith(b"prefix\x00/opt/XMLBridgeBuild/")
    assert clean.endswith(b"/T/build/python/lib\x00suffix\xff")
    assert b"a" * 30 not in clean


def test_clean_metadata_is_unchanged():
    raw = b"/opt/XMLBridgeBuild/python/lib /usr/lib /System/Library"
    assert sanitize_module(raw) == (raw, 0)


def test_privacy_gate_rejects_temporary_build_paths():
    audit = PrivacyAudit()
    audit.scan_blob(sample_path(), "synthetic-config")
    assert not audit.report()["passed"]
    clean, _ = sanitize_module(sample_path())
    audit = PrivacyAudit()
    audit.scan_blob(clean, "neutral-config")
    assert audit.report()["passed"]
