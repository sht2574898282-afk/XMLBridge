from pathlib import Path
import copy
import json
import shutil
import subprocess

import pytest
from lxml import etree

from helpers import make_xmeml
from xmlbridge.native_bridge import handle
from xmlbridge.proxy_relink import Document, digest, preserved_digest


@pytest.fixture(scope="module")
def footage(tmp_path_factory):
    root = tmp_path_factory.mktemp("proxy-media")
    original = root / "卡A" / "C0001.MOV"
    original.parent.mkdir()
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=size=320x180:rate=25",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-frames:v", "75",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", "-shortest",
                    "-timecode", "01:00:00:00", str(original)], check=True)
    duplicate = root / "卡B" / "C0001.MOV"
    duplicate.parent.mkdir()
    shutil.copy2(original, duplicate)
    # A non-Sony original legitimately contains S03 in its real name.
    other = root / "OTHER_S03.MOV"
    shutil.copy2(original, other)
    shutil.copy2(original, root / "OTHER_.MOV")
    return root


@pytest.fixture
def proxy_request(tmp_path, footage):
    tree = make_xmeml(src="file:///offline/C0001S03.MP4")
    file = tree.find(".//file")
    file.find("name").text = "C0001S03.MP4"
    file.find("duration").text = "75"
    for node in tree.xpath("//rate"):
        node.find("timebase").text = "25"; node.find("ntsc").text = "FALSE"
    tc = etree.SubElement(file, "timecode")
    tc.append(copy.deepcopy(file.find("rate")))
    for k, v in (("string", "01:00:00:00"), ("frame", "90000"), ("displayformat", "NDF")):
        etree.SubElement(tc, k).text = v
    media = etree.SubElement(file, "media")
    sample = etree.SubElement(etree.SubElement(media, "video"), "samplecharacteristics")
    for k,v in (("width", "320"), ("height", "180"), ("pixelaspectratio", "square"), ("fielddominance", "none")):
        etree.SubElement(sample, k).text = v
    etree.SubElement(etree.SubElement(media,"audio"),"channelcount").text="2"
    clip = tree.find(".//clipitem")
    for k,v in (("duration","75"),("start","0"),("end","25"),("in","10"),("out","35")):
        clip.find(k).text=v
    other = copy.deepcopy(clip);other.set("id","non-sony-clip")
    other_file=other.find("file");other_file.set("id","non-sony-file")
    other_file.find("name").text="OTHER_S03.MOV"
    other_file.find("pathurl").text=(footage/"OTHER_S03.MOV").as_uri()
    other.find("start").text="25";other.find("end").text="50"
    clip.getparent().append(other)
    xml = tmp_path/"剪辑.xml"
    tree.write(str(xml),encoding="UTF-8",xml_declaration=True)
    return {"path":str(xml),"input_hash":digest(xml),"media_root":str(footage),"suffixes":["S03"]}


def choose(proxy_request, row, target, adopted=True):
    return handle({**proxy_request,"action":"proxy_choose","source_id":row["id"],"rule":row["rule"],
                   "candidate_paths":[c["path"] for c in row["candidates"]],
                   "target_path":str(target),"adopted":adopted})["proxy_row"]


def test_native_candidate_list_shows_both_cards_and_metadata(proxy_request):
    plan=handle({**proxy_request,"action":"proxy_scan"})["proxy_plan"]
    assert len(plan["rows"])==2
    row=plan["rows"][0]
    assert row["status"]=="ambiguous"
    assert not row["adopted"] and not row["exportable"]
    assert len(row["candidates"])==2
    assert {Path(c["path"]).parent.name for c in row["candidates"]}=={"卡A","卡B"}
    assert all(c["fps"]=="25" and c["frames"]==75 and c["timecode"]=="01:00:00:00" for c in row["candidates"])
    assert plan["rows"][1]["status"]=="needs_scope"


def test_native_choose_export_creates_real_xml_and_preserves_non_sony(proxy_request,tmp_path):
    plan=handle({**proxy_request,"action":"proxy_scan"})["proxy_plan"]
    row=choose(proxy_request,plan["rows"][0],plan["rows"][0]["candidates"][1]["path"])
    assert row["adopted"] and row["exportable"]
    out=tmp_path/"输出.xml"
    result=handle({**proxy_request,"action":"proxy_export","output_path":str(out),
                   "selections":[{"source_id":row["id"],"target_path":row["target_path"],
                                  "target_stat":row["target_stat"],"adopted":True}]})["result"]
    assert result["output_path"]==str(out) and out.exists()
    before,after=Document(proxy_request["path"]),Document(out)
    assert preserved_digest(before.tree)==preserved_digest(after.tree)
    assert after.sources[0].name=="C0001.MOV"
    assert after.sources[1].uri==before.sources[1].uri
    assert etree.tostring(after.sources[1].definitions[0])==etree.tostring(before.sources[1].definitions[0])
    assert digest(proxy_request["path"])==proxy_request["input_hash"]


def test_revoke_selection_disables_export(proxy_request):
    row=handle({**proxy_request,"action":"proxy_scan"})["proxy_plan"]["rows"][0]
    chosen=choose(proxy_request,row,row["candidates"][0]["path"])
    revoked=choose(proxy_request,chosen,chosen["target_path"],False)
    assert not revoked["adopted"] and not revoked["exportable"]


def test_export_refuses_changed_media_after_selection(proxy_request,tmp_path):
    row=handle({**proxy_request,"action":"proxy_scan"})["proxy_plan"]["rows"][0]
    chosen=choose(proxy_request,row,row["candidates"][0]["path"])
    with pytest.raises(ValueError,match="核对后已改变"):
        handle({**proxy_request,"action":"proxy_export","output_path":str(tmp_path/"no.xml"),
                "selections":[{"source_id":chosen["id"],"target_path":chosen["target_path"],"target_stat":[0,0,0],"adopted":True}]})


def test_xml_change_and_cancel_are_checked(proxy_request,tmp_path):
    with pytest.raises(ValueError,match="已改变"):
        handle({**proxy_request,"action":"proxy_scan","input_hash":"stale"})
    cancel=tmp_path/"cancel";cancel.touch()
    assert handle({**proxy_request,"action":"proxy_scan","cancel_path":str(cancel)})["cancelled"]
