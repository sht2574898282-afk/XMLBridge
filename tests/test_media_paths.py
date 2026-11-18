from pathlib import PureWindowsPath

import pytest

from xmlbridge.core import resolve_uri
from xmlbridge.media_paths import foreign_absolute_reference, local_path_text


@pytest.mark.parametrize("uri,expected", [
    ("file:///C:/Media/%E4%B8%AD%E6%96%87%20shot.mov", r"C:\Media\中文 shot.mov"),
    ("file://localhost/D%3A/素材/shot.mov", r"D:\素材\shot.mov"),
    (r"D:\素材\100% real.mov", r"D:\素材\100% real.mov"),
    ("D:/素材/shot.mov", r"D:\素材\shot.mov"),
    ("file://studio/Media/shot%20one.mov", r"\\studio\Media\shot one.mov"),
    (r"\\studio\Media\shot.mov", r"\\studio\Media\shot.mov"),
    ("../素材/shot.mov", r"..\素材\shot.mov"),
])
def test_windows_media_reference(uri, expected):
    actual = local_path_text(uri, windows=True)
    assert actual == expected
    assert PureWindowsPath(actual).is_absolute() == PureWindowsPath(expected).is_absolute()


def test_macos_absolute_and_relative_references_unchanged(tmp_path):
    xml = tmp_path / "project.fcpxml"
    assert resolve_uri("素材/100% real.mov", xml) == tmp_path / "素材/100% real.mov"
    assert resolve_uri("file:///Volumes/Media/shot%20one.mov", xml) == type(tmp_path)("/Volumes/Media/shot one.mov")
    assert local_path_text("file://studio/share/shot.mov", windows=False) is None


@pytest.mark.parametrize("value", ["https://example.invalid/a.mov", "file://user@host/share/a.mov", "file://host:445/share/a.mov"])
def test_remote_protocol_and_credential_hosts_are_not_local_paths(value):
    assert local_path_text(value, windows=True) is None


def test_foreign_absolute_paths_are_never_mapped_to_a_local_drive():
    assert foreign_absolute_reference("file:///Volumes/Media/shot.mov",windows=True)
    assert local_path_text("file:///Volumes/Media/shot.mov",windows=True) is None
    assert local_path_text("file:///C:/Media/shot.mov",windows=False) is None
    assert not foreign_absolute_reference("file:///C:/Media/shot.mov",windows=True)
    assert not foreign_absolute_reference("file:///Volumes/Media/shot.mov",windows=False)


def test_windows_file_uri_is_preserved_by_mac_same_and_cross_format_export(tmp_path):
    from helpers import make_fcpxml, read_output, write_tree
    from xmlbridge.core import inspect, plan_conversion, convert
    from xmlbridge.models import Target
    from lxml import etree
    uri="file:///D:/Media/shot%20one.mov"
    tree=make_fcpxml("1.9",src=uri,frame_duration="1/25s",duration="30s",tc_format="NDF")
    for marker in tree.xpath("//marker"):marker.set("duration","1/25s")
    source=write_tree(tmp_path/"source.fcpxml",tree)
    for target in [Target("fcpxml","1.8"),Target("xmeml","5")]:
        result=convert(plan_conversion(inspect(source),target,allow_risky=True),{},tmp_path/target.family,allow_risky=True)
        assert result.output_path
        output=read_output(result.output_path)
        assert uri in output.xpath("//asset/@src | //file/pathurl/text()")
