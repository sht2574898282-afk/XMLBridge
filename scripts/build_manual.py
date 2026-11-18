# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
from pathlib import Path
import json, math, re, os
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dist';OUT.mkdir(exist_ok=True)
FONTDIR=Path(os.environ['XMLBRIDGE_MANUAL_FONT_DIR'])
for n,f in [('CN','HeitiSC-Light.ttf'),('CNB','HeitiSC-Medium.ttf')]:
 pdfmetrics.registerFont(TTFont(n,str(FONTDIR/f)))
W,H=595.2756,841.8898
INK='#181C24';MUTED='#626772';PAPER='#F8F7F3';LINE='#DCDDD8';ACC='#DE5B34';TINT='#F4E6DC';PURPLE='#5751AD';WHITE='#FFFFFF';COOL='#ECECF6';GREEN='#2D7660'
AUTHOR='迪儿 Dear';URL='https://github.com/sht2574898282-afk'
PATH=OUT/'XMLBridge_使用手册_0.3.3.pdf'
c=canvas.Canvas(str(PATH),pagesize=(W,H),pageCompression=1,invariant=1)
c.setTitle('XMLBridge 使用手册');c.setAuthor(AUTHOR);c.setSubject('版本转换 代理回套 许可与署名');c.setCreator('XMLBridge')
page_n=0;TEXT=[]

def color(v):return HexColor(v)
def box(x,y,w,h,fill=WHITE,stroke=None,r=0):
 c.setFillColor(color(fill));c.setStrokeColor(color(stroke or fill));c.setLineWidth(.7)
 if r:c.roundRect(x,H-y-h,w,h,r,stroke=bool(stroke),fill=1)
 else:c.rect(x,H-y-h,w,h,stroke=bool(stroke),fill=1)
def rule(x,y,w,col=LINE):
 c.setStrokeColor(color(col));c.setLineWidth(.6);c.line(x,H-y,x+w,H-y)
def text(s,x,y,size=11,font='CN',fill=INK):
 c.setFont(font,size);c.setFillColor(color(fill));c.drawString(x,H-y-size*.84,s);TEXT.append(s)
def right(s,x,y,size=9,font='CN',fill=MUTED):
 c.setFont(font,size);c.setFillColor(color(fill));c.drawRightString(x,H-y-size*.84,s);TEXT.append(s)
def lines(s,width,size,font):
 result=[]
 for para in s.split('\n'):
  line=''
  for ch in re.findall(r'[A-Za-z0-9_./:+-]+|.',para):
   if pdfmetrics.stringWidth(line+ch,font,size)>width and line and ch not in '。，、；：！？）】》’”':
    result.append(line.rstrip());line=ch.lstrip()
   else:line+=ch
  result.append(line.rstrip())
 return result
def para(s,x,y,w,size=11,leading=18,font='CN',fill=INK):
 for line in lines(s,w,size,font):text(line,x,y,size,font,fill);y+=leading
 return y

def tag(s,x,y,fill=ACC,bg=TINT):
 width=pdfmetrics.stringWidth(s,'CNB',9)+18
 box(x,y,width,21,bg,r=4);text(s,x+9,y+6,9,'CNB',fill);return width

def note(title,body,y,kind='warm'):
 fill=TINT if kind=='warm' else COOL
 box(44,y,W-88,79,fill,r=8)
 box(44,y,3,79,ACC if kind=='warm' else PURPLE,r=1)
 text(title,60,y+13,11,'CNB')
 para(body,60,y+34,W-120,10,16,fill=MUTED)
 return y+95

def page(kicker,title,sub):
 global page_n
 page_n+=1;box(0,0,W,H,PAPER)
 text('XMLBridge',44,28,13,'Helvetica-Bold');right('USER GUIDE  /  0.3.3',W-44,32,8,'Helvetica')
 rule(44,57,W-88)
 text(kicker,44,82,9,'Helvetica-Bold',ACC)
 text(title,44,106,27,'CNB')
 para(sub,44,150,W-88,11,18,fill=MUTED)
 rule(44,H-43,W-88)
 text(AUTHOR,44,H-30,8,'CN');text('XML 版本转换 · 代理回套',120,H-30,8,fill=MUTED)
 right(f'{page_n:02d} / 08',W-44,H-30,8,'Helvetica-Bold',INK)

def finish():c.showPage()
def step(n,title,body,y,w=507):
 box(44,y,29,29,WHITE,LINE,6);text(f'{n:02d}',51,y+8,11,'Helvetica-Bold',ACC)
 text(title,86,y+1,13,'CNB')
 end=para(body,86,y+25,w-42,10.5,17,fill=MUTED)
 return max(end+19,y+66)

def filecard(x,y,w,name,label,accent=PURPLE):
 box(x,y,w,94,WHITE,LINE,9)
 box(x+15,y+17,24,30,COOL,r=3)
 for yy in (25,31,37):rule(x+21,y+yy,12,accent)
 text(name,x+51,y+20,12,'Helvetica-Bold')
 text(label,x+15,y+64,9,'CN',MUTED)

def arrow(x,y,w=27):
 c.setStrokeColor(color(ACC));c.setLineWidth(1.3);c.line(x,H-y,x+w,H-y)
 c.line(x+w,H-y,x+w-5,H-y+4);c.line(x+w,H-y,x+w-5,H-y-4)

# 01 cover
page('FIELD GUIDE','剪辑 XML 使用手册','面向剪辑交接与调色回套。用新的 XML 完成交接，保留原始工程与素材。')
text('XMLBridge',41,216,56,'Helvetica-Bold')
text('把交接关系理清楚',44,282,21,'CNB')
text('从版本转换，到代理连接原片。',44,316,12,fill=MUTED)
tag('版本转换',44,359,PURPLE,COOL);tag('代理回套',132,359)
# Timeline editorial drawing
box(44,408,W-88,188,WHITE,LINE,10)
text('PROXY → ORIGINAL',61,426,8,'Helvetica-Bold',ACC)
filecard(61,449,207,'C0001S03.MP4','剪辑使用的代理')
filecard(319,449,215,'C0001.MOV','核对后采用的原片')
arrow(279,495,26)
for x,w in [(62,112),(178,76),(258,125),(387,146)]:box(x,562,w,14,COOL,r=2)
rule(62,583,470)
text('改写素材引用 · 保留时间线结构',61,607,10,'CN',MUTED)
rule(44,655,W-88)
text('阅读路线',44,678,12,'CNB')
text('02 选择功能      03 版本转换      04 代理回套',44,707,10)
text('05 选择候选      06 理解状态      07 导出检查      08 许可',44,731,10)
finish()

# 02 workflow
page('01 / GET STARTED','先选对要做的事','窗口顶部的两个入口，对应两种交接任务。先选择功能，再添加文件。')
box(44,206,W-88,192,WHITE,LINE,10)
text('XMLBridge',60,223,10,'Helvetica-Bold')
box(148,217,286,35,'#E9E9EF',r=8)
box(152,221,136,27,WHITE,r=5)
text('版本转换',188,228,12,'CNB');text('代理回套',329,228,12,'CN',MUTED)
rule(44,266,W-88)
box(61,285,160,93,PAPER,r=6);text('交接文件',74,299,9,fill=MUTED)
box(70,323,141,36,COOL,r=5);text('剪辑.xml',84,334,11,'CNB')
text('目标格式与版本',242,291,11,'CNB');text('查看交接影响',242,322,10,fill=MUTED)
box(402,344,128,32,PURPLE,r=6);text('转换并导出',434,354,11,'CNB',WHITE)
text('功能入口示意  ·  不含真实工程信息',44,412,9,fill=MUTED)
# two columns genuine comparison
for x,num,title,body in [(44,'A','版本转换','升级、降级或调整交接格式。\n主要结果是指定格式与版本的新 XML。'),(307,'B','代理回套','剪辑使用代理，调色需要原片。\n主要结果是采用原片引用的新 XML。')]:
 box(x,452,244,143,WHITE,LINE,8)
 text(num,x+17,467,25,'Helvetica-Bold',PURPLE if num=='A' else ACC)
 text(title,x+55,472,17,'CNB');para(body,x+17,515,210,10.5,18,fill=MUTED)
note('准备好交接文件', '版本转换可添加 XML、FCPXML 或 FCPXML 目录包；代理回套还需要对应原片。只有 XML 时，无法验证真实素材连接。',628,'cool')
finish()

#03
page('02 / CONVERT','版本转换','为不同软件与版本准备交接文件。源格式、目标格式和目标版本需要分别核对。')
y=205
y=step(1,'添加并选定项目','点击“添加文件或目录包”，或拖入文件。包含多个项目时，选择本次要交接的项目。',y)
y=step(2,'设置目标格式与版本','在右侧选择格式、版本和目标软件。FCPXML 与 Final Cut 7 XML 是两种格式，不能仅凭扩展名判断。',y)
y=step(3,'查看影响并决定处理','点击“查看影响”。重点看嵌套、多机位、变速、字幕及复杂效果；按显示的处理选项作出决定。',y)
y=step(4,'选择输出位置并生成','点击界面显示的升级、降级、整理或转换导出按钮。完成后，从“查看结果”定位新 XML。',y)
note('“允许有损转换”怎样使用','开启后，同格式可以在记录问题后生成结果；检查未通过仍会显示。有些跨格式内容没有映射规则，仍可能无法导出。',y+4)
text('生成文件之后',44,710,12,'CNB')
para('导入目标软件，再对照参考视频核对。结构检查通过只代表已检查的 XML 规则通过。',44,735,W-88,10,17,fill=MUTED)
finish()

#04
page('03 / RECONFORM','代理回套','把代理媒体的引用换成确认对应的原片。当前支持 Premiere 导出的 xmeml 4 或 5。')
y=203
y=step(1,'选择原片目录和代理尾缀','添加 XML，切换到“代理回套”。选择原片文件夹，默认尾缀为 S03；多个约定用逗号分开，例如 S03, _Proxy。',y)
y=step(2,'扫描并检查候选','点击“扫描匹配”。软件会搜索子文件夹；保留拍摄日、机位和卡号层级，方便区分重复文件名。',y)
y=step(3,'明确采用对应原片','核对文件名、位置、时间码等信息，再勾选“采用”。多个候选需要先进入面板选择具体文件。',y)
y=step(4,'生成原片回套 XML','至少采用一个可回套条目后，点击“生成原片回套 XML”，选择新文件保存位置。完成后点击“显示 XML”。',y)
note('S03 是匹配规则，不是相机品牌证明','不能把所有 S03 一律删除。其他相机素材也可能以 S03 结尾；只有明确采用的素材会改写，未采用项保持原引用。',y+4)
text('C0001S03.MP4',44,715,13,'Helvetica-Bold');arrow(225,723,38);text('C0001.MOV',283,715,13,'Helvetica-Bold')
text('命名示例。软件不会重命名磁盘上的原始素材。',44,743,9,fill=MUTED)
finish()

#05
page('04 / PICK THE RIGHT FILE','多个候选怎么选','同名不等于同一镜头。进入“选择候选…”面板，结合参数、来源和画面作判断。')
box(44,203,W-88,70,COOL,r=8)
text('XML 记录',60,218,10,'CNB');text('C0001S03.MP4',143,218,12,'Helvetica-Bold')
text('25 fps     ·     起始时间码 09:12:30:00',60,246,10,fill=MUTED)
for y,card,tc,selected in [(293,'卡 A 中的 C0001.MOV','09:12:30:00',True),(410,'卡 B 中的 C0001.MOV','10:05:12:00',False)]:
 box(44,y,W-88,101,WHITE,PURPLE if selected else LINE,8)
 c.setStrokeColor(color(PURPLE if selected else MUTED));c.setLineWidth(1.1);c.circle(65,H-y-26,6,stroke=1,fill=0)
 if selected:c.setFillColor(color(PURPLE));c.circle(65,H-y-26,3,stroke=0,fill=1)
 text(card,83,y+17,12,'CNB')
 text('25 fps   ·   1920 × 1080   ·   '+tc,83,y+47,10,fill=MUTED)
 text('比较所属拍摄卡、日期与实际画面',83,y+73,9,fill=MUTED)
 box(467,y+15,66,28,COOL,r=5);text('预览',488,y+24,10,'CNB',PURPLE)
text('① 比参数',44,542,13,'CNB');text('② 比来源',221,542,13,'CNB');text('③ 比画面',399,542,13,'CNB')
para('时间码、时长与帧率',44,571,150,10,17,fill=MUTED)
para('机位、拍摄日与卡号',221,571,150,10,17,fill=MUTED)
para('人物、动作与场景',399,571,150,10,17,fill=MUTED)
note('参数完全一样，仍然可能不是同一段素材','用“预览”打开视频，对照参考画面。找不到正确候选时，使用“手动指定…”。确认后点击“采用这个原片”。',620)
text('两份文件确定是同一原片的备份时，选择后续长期保留的一份。',44,737,10,fill=MUTED)
finish()

#06
page('05 / READ THE STATUS','看懂状态与差异','状态说明当前条目能否进入回套输出。对明确采用的原片，帧率和一帧差异只作记录。')
rows=[('待选择采用','候选已找到，等待你核对并勾选采用。',MUTED),('多个候选','进入候选面板，先选定具体原片。',PURPLE),('参数核对通过','已采用且当前核对通过，可进入输出。',GREEN),('可回套·有差异','允许生成；检查提示的帧率或一帧差异。',ACC),('未找到原片','检查目录与尾缀，或手动指定文件。',MUTED),('信息不足','缺少核对依据或参数无法读取，查看具体原因。',MUTED),('需另行处理','有较大差异、声道或画面比例异常等问题。',ACC)]
y=205
for title,body,tone in rows:
 rule(44,y,W-88)
 c.setFillColor(color(tone));c.circle(49,H-y-24,2.6,stroke=0,fill=1)
 text(title,62,y+17,11,'CNB');para(body,202,y+17,340,10.3,17,fill=MUTED);y+=58
rule(44,y,W-88)
note('保留剪辑点之后，仍要核对画面对齐','软件不转换帧率、不自动补帧，也不补偿代理与原片之间的偏移。输出会保留 XML 的入出点、时间线、嵌套与效果节点结构。',641,'cool')
finish()

#07
page('06 / EXPORT AND CHECK','导出后做什么','认准新 XML，再导入剪辑或调色软件。附带报告帮助排查，不能用来导入时间线。')
for y,ext,title,body in [(205,'XML','主要交付文件','导入达芬奇或对应软件，读取新的素材引用。'),(287,'HTML','操作说明与结果','查看已采用素材、未处理项及参数差异。'),(369,'JSON','结构化排查记录','保留对应关系和核对明细，日常通常无需打开。')]:
 box(44,y,65,59,COOL if ext=='XML' else WHITE,LINE,7)
 text(ext,54,y+23,12,'Helvetica-Bold',PURPLE if ext=='XML' else MUTED)
 text(title,126,y+5,13,'CNB');para(body,126,y+31,418,10.5,17,fill=MUTED)
text('开始正式调色前',44,464,17,'CNB')
for i,s in enumerate(['对照参考视频，确认镜头、入出点与离线素材。','核对构图、缩放、变速、转场和音频。','确认使用的是原片，再保存新的调色工程。']):
 box(46,505+i*34,10,10,WHITE,LINE,2);text(s,69,501+i*34,10.5)
note('对外分享前，检查路径与项目信息','输出 XML 会包含素材引用，HTML 与 JSON 会记录完整路径。可能涉及项目名、拍摄目录等信息。根据交接对象决定是否发送附带报告。',628)
text('按钮不可用：先采用一个可回套条目。仍有离线：检查未找到或未采用项。',44,739,9,fill=MUTED)
finish()

#08 license summary
page('07 / LICENSE AND CREDIT','AGPL 许可与作者署名','采用 AGPL-3.0-only。允许商用和收费，同时履行版权声明与对应源码提供义务。')
text(AUTHOR,44,211,27,'CNB')
text(URL,44,253,10,'Helvetica',PURPLE)
c.linkURL(URL,(44,H-269,420,H-250),relative=0)
rule(44,290,W-88)
y=316
for n,title,body in [('01','保留版权与作者声明','按协议保留适用的迪儿 Dear 版权、作者来源、许可与无担保声明。修改后注明修改情况，作者主页作为来源信息保留。'),('02','分发衍生版本时提供源码','对外分发受 AGPL 覆盖的修改版或组合程序时，应按同一许可提供完整对应源码。独立程序仅并列存放不自动受影响。'),('03','网络交互同样提供源码','修改版通过网络提供交互时，也应向这些用户提供对应源码获取方式。AGPL 允许商用与收费；用户素材及制作成果不因此自动适用 AGPL。')]:
 text(n,44,y,17,'Helvetica-Bold',ACC);text(title,86,y,14,'CNB');y=para(body,86,y+28,W-130,10.5,18,fill=MUTED)+24
box(44,668,W-88,80,WHITE,LINE,8)
para('本页是许可摘要，完整条款见 LICENSE，作者与源码入口见 NOTICE。软件“帮助”菜单可直接查看。第三方组件按原许可提供；单纯借鉴思路的独立实现以法律规定为准。',59,684,W-118,9.5,16,fill=MUTED)
finish();c.save()
(OUT/'manual-text.json').write_text(json.dumps(TEXT,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'file':PATH.name,'pages':page_n,'design':'warm_editorial_vector_diagrams','author':AUTHOR},ensure_ascii=False))
