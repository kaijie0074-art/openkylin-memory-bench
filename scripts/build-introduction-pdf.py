#!/usr/bin/env python3
"""Build the R1 introduction PDF from the editable competition document."""
import html
import json
import re
from pathlib import Path

from reportlab.graphics import renderSVG
from reportlab.graphics.charts.spider import SpiderChart
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/pdf/openKylin-Memory-Bench-R1-作品介绍与技术方案.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists():
    raise SystemExit("Preserve existing PDF; choose a new revision")
TMP = ROOT / ".runtime/delivery-R1-20261004/pdf"
TMP.mkdir(parents=True, exist_ok=True)
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
pdfmetrics.registerFont(TTFont("CJK", FONT))
pdfmetrics.registerFont(TTFont("LatinBold", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"))
NAVY = colors.HexColor("#16324A")
TEAL = colors.HexColor("#087F8C")
ORANGE = colors.HexColor("#C76A22")
GRAY = colors.HexColor("#516373")
LIGHT = colors.HexColor("#EDF4F7")
S = {
    "body": ParagraphStyle(
        "body",
        fontName="CJK",
        fontSize=10.3,
        leading=17,
        spaceAfter=10,
        wordWrap="CJK",
        textColor=NAVY,
    ),
    "title": ParagraphStyle(
        "title",
        fontName="CJK",
        fontSize=22,
        leading=31,
        spaceAfter=22,
        textColor=NAVY,
        wordWrap="CJK",
    ),
    "subtitle": ParagraphStyle(
        "subtitle",
        fontName="CJK",
        fontSize=11.5,
        leading=19,
        textColor=GRAY,
        spaceAfter=16,
        wordWrap="CJK",
    ),
    "cell": ParagraphStyle(
        "cell", fontName="CJK", fontSize=9.3, leading=14, wordWrap="CJK", textColor=NAVY
    ),
    "small": ParagraphStyle(
        "small",
        fontName="CJK",
        fontSize=8.5,
        leading=13,
        wordWrap="CJK",
        textColor=GRAY,
        spaceAfter=8,
    ),
    "cover": ParagraphStyle(
        "cover", fontName="LatinBold", fontSize=36, leading=45, textColor=NAVY, spaceAfter=24
    ),
    "tag": ParagraphStyle(
        "tag", fontName="LatinBold", fontSize=11, leading=15, textColor=TEAL, spaceAfter=12
    ),
}


def fmt(s):
    s = html.escape(s.strip()).replace("&lt;br/&gt;", "<br/>")
    s = re.sub(r"\*\*(.*?)\*\*", r'<font color="#087F8C">\1</font>', s)
    s = re.sub(r"`([^`]+)`", r'<font color="#087F8C">\1</font>', s)
    s = re.sub(r"\[([^\]]+)\]\((https://[^)]+)\)", r'<a href="\2" color="#087F8C">\1</a>', s)
    s = s.replace("→", " → ")
    return s


def para(t, style="body"):
    return Paragraph(fmt(t), S[style])


abilities = ["长期保持", "记忆调用", "动态更新", "相近区分", "边界识别", "任务复用"]
a = [6 / 9, 4 / 9, 7 / 9, 0, 5 / 9, 1]
b = [7 / 9, 6 / 9, 1 / 9, 0, 3 / 9, 1]
radar = Drawing(470, 225)
sp = SpiderChart()
sp.x = 75
sp.y = 16
sp.width = 190
sp.height = 190
sp.data = [[v] * 6 for v in [1, 0.75, 0.5, 0.25]] + [a, b]
sp.labels = abilities
sp.spokeLabels.fontName = "CJK"
sp.spokeLabels.fontSize = 9
sp.spokeLabels.fillColor = NAVY
sp.spokes.strokeColor = colors.HexColor("#CCDCE4")
sp.spokes.labelRadius = 1.14
for i in range(4):
    sp.strands[i].strokeColor = colors.HexColor("#D3E0E7")
    sp.strands[i].strokeWidth = 0.6
for i, col in [(4, TEAL), (5, ORANGE)]:
    sp.strands[i].strokeColor = col
    sp.strands[i].strokeWidth = 2
    sp.strands[i].symbol = "Circle"
    sp.strands[i].symbolSize = 3
radar.add(sp)
for y, label, col in [(175, "OpenClaw", TEAL), (151, "Hermes Agent", ORANGE)]:
    radar.add(Line(355, y, 375, y, strokeColor=col, strokeWidth=2))
    radar.add(String(382, y - 3, label, fontName="CJK", fontSize=9, fillColor=NAVY))
for v in [0.25, 0.5, 0.75, 1]:
    radar.add(
        String(174, 111 + 95 * v, f"{int(v * 100)}%", fontName="CJK", fontSize=6.5, fillColor=GRAY)
    )
renderSVG.drawToFile(radar, str(TMP / "radar.svg"))

text = (ROOT / "competition/作品介绍与技术方案.md").read_text()
parts = re.split(r"^## (\d+\. .+)$", text, flags=re.MULTILINE)
sections = [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]
assert len(sections) == 12

story = [
    Spacer(1, 55),
    para("ENGINEERING VALIDATION / V3", "tag"),
    para("openKylin<br/>Memory Bench", "cover"),
    para("面向 openKylin 的智能体跨会话记忆<br/>自动评测工具", "title"),
    para("2026 上海开源软件应用创新大赛<br/>开源 AI 工具赛道 · openKylin 企业赛题", "subtitle"),
]
metrics = []
for n, l in [("60", "原创任务"), ("108", "真实留出运行"), ("324", "A/B/C 评分")]:
    metrics.append(
        [
            Paragraph(
                n,
                ParagraphStyle(
                    "n" + n, fontName="LatinBold", fontSize=26, leading=34, textColor=TEAL
                ),
            ),
            para(l, "small"),
        ]
    )
t = Table(
    [[Table([[cell] for cell in m], colWidths=[145]) for m in metrics]], colWidths=[164, 164, 164]
)
t.setStyle(
    TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D2E0E7")),
            ("TOPPADDING", (0, 0), (-1, -1), 16),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
        ]
    )
)
story.extend(
    [
        Spacer(1, 16),
        t,
        Spacer(1, 25),
        para(
            "真实 openKylin 3.0 ARM64 桌面实测。两款智能体采用固定容器；同一运行证据支持三种评分与六维比较。"
        ),
        para(
            "工程条件已冻结；语义评分尚未经过独立人工验证。<br/>原版已发送，主办方收件未核实；本修订更正复现入口与材料状态。",
            "small",
        ),
        Spacer(1, 15),
        para("材料日期 2026-10-04 · 自研代码 Apache-2.0 · 原创数据 CC BY 4.0", "small"),
    ]
)


def extra_table(rows, widths):
    tb = Table(
        [[para(cell, "cell") for cell in row] for row in rows], colWidths=widths, hAlign="LEFT"
    )
    tb.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("LINEBELOW", (0, 0), (-1, 0), 1, TEAL),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#D6E1E8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return tb


def architecture():
    d = Drawing(490, 210)
    boxes = [
        (0, 155, "可信调度器", "任务 + 私有判据"),
        (182, 155, "被测工作区", "仅允许的输入"),
        (365, 155, "真实智能体", "独立身份 / 新会话"),
        (365, 55, "可信采集器", "文件 / 可见记忆"),
        (182, 55, "冻结证据", "SHA-256 / 原始记录"),
        (0, 55, "A / B / C", "同证据离线评分"),
    ]
    for x, y, a, b in boxes:
        d.add(
            Rect(
                x,
                y,
                125,
                49,
                rx=6,
                ry=6,
                fillColor=LIGHT,
                strokeColor=colors.HexColor("#BCD1DE"),
                strokeWidth=0.8,
            )
        )
        d.add(
            String(
                x + 62.5,
                y + 29,
                a,
                fontName="CJK",
                fontSize=10,
                textAnchor="middle",
                fillColor=NAVY,
            )
        )
        d.add(
            String(
                x + 62.5, y + 11, b, fontName="CJK", fontSize=8, textAnchor="middle", fillColor=GRAY
            )
        )
    for x1, y1, x2, y2 in [
        (130, 180, 174, 180),
        (312, 180, 358, 180),
        (427, 147, 427, 112),
        (358, 80, 312, 80),
        (174, 80, 132, 80),
    ]:
        d.add(Line(x1, y1, x2, y2, strokeColor=TEAL, strokeWidth=1.4))
        if x1 == x2:
            d.add(
                Polygon([x2 - 4, y2 + 6, x2, y2, x2 + 4, y2 + 6], fillColor=TEAL, strokeColor=TEAL)
            )
        else:
            k = 1 if x2 > x1 else -1
            d.add(
                Polygon(
                    [x2 - k * 6, y2 - 4, x2, y2, x2 - k * 6, y2 + 4],
                    fillColor=TEAL,
                    strokeColor=TEAL,
                )
            )
    d.add(
        String(
            0,
            18,
            "标准答案停留在可信调度 / 评分侧；不得挂载到被测工作区。",
            fontName="CJK",
            fontSize=9,
            fillColor=TEAL,
        )
    )
    return d


W = 492
for num, (heading, body) in enumerate(sections, 1):
    story.extend(
        [
            PageBreak(),
            para(f"0{num}" if num < 10 else str(num), "tag"),
            para(heading.split(". ", 1)[1], "title"),
        ]
    )
    blocks = re.split(r"\n\s*\n", body.strip())
    for block in blocks:
        if block.lstrip().startswith("|"):
            rows = []
            for line in block.splitlines():
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-+:?", x) for x in cells):
                    continue
                rows.append([para(c, "cell") for c in cells])
            nc = len(rows[0])
            widths = {3: [90, 201, 201], 4: [84, 144, 132, 132]}.get(nc, [W / nc] * nc)
            if num == 3:
                widths = [W / nc] * nc
            if num == 9:
                widths = [162, 165, 165]
            table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                        ("LINEBELOW", (0, 0), (-1, 0), 1, TEAL),
                        ("LINEBELOW", (0, 1), (-1, -1), 0.35, colors.HexColor("#D6E1E8")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.extend([table, Spacer(1, 13)])
        elif num == 12 and block.startswith("官方来源："):
            story.append(
                Paragraph(
                    '官方来源：<a href="https://www.oschina.net/os2026/" color="#087F8C">赛事官网</a> · <a href="https://apiv1.oschina.net/api/files/fhhc7e8z6no3gyt/w9qliatd2m6lgs8/ai_open_kylin_open_kylin_benchmark_XaCQyZepC8.pdf" color="#087F8C">openKylin 任务书原件</a>。复核日期 2026-10-04。原件 SHA-256 完整值见随附交付核对及机读清单。',
                    S["small"],
                )
            )
        else:
            story.append(para(block.replace("\n", " ")))
    if num == 1:
        story.extend(
            [
                Spacer(1, 20),
                extra_table(
                    [
                        ["任务书交付项", "本地材料"],
                        ["a. 方案", "本 PDF 与可编辑 Markdown"],
                        ["b. 样例", "2 题 / 4 份真实证据 / 12 条评分"],
                        ["c. 复现", "源码 ZIP、README、锁文件、配置、构建记录"],
                        ["d. 工具", "一键 CLI 与 ARM64 .deb"],
                        ["e. 演示", "连续原片、200 秒 R1 讲解、六维报告"],
                    ],
                    [133, 359],
                ),
            ]
        )
    if num == 2:
        story.extend([Spacer(1, 18), architecture()])
    if num == 3:
        story.extend(
            [
                Spacer(1, 18),
                extra_table(
                    [
                        ["集合", "题目数", "每能力", "用途"],
                        ["开发", "24", "4", "验证执行与测量"],
                        ["工程选型", "18", "3", "控制通过后冻结条件"],
                        ["留出", "18", "3", "冻结后两款各三次运行"],
                    ],
                    [94, 75, 75, 248],
                ),
            ]
        )
    if num == 5:
        story.extend(
            [
                Spacer(1, 15),
                extra_table(
                    [
                        ["会话 / 证据", "可直接核对的内容"],
                        ["s1 / 稳定约定", "normal；仅一张紧急工单可临时 urgent"],
                        ["s2 / 例外结束", "紧急工单已完成，临时授权已消耗"],
                        ["s3 / 最终委托", "为下一张普通工单生成 priority.json"],
                        ["终态 / 两款相同", 'priority.json = {"priority":"normal"}'],
                        ["A / B / C", "通过；file:priority.json"],
                    ],
                    [125, 367],
                ),
            ]
        )
    if num == 6:
        story.extend(
            [
                Spacer(1, 15),
                extra_table(
                    [
                        ["核对项目", "Hermes / OpenClaw 两次开发运行"],
                        ["执行终态", "completed / completed"],
                        ["current/report.txt", "完整目录清单中缺失 / 缺失"],
                        ["新文件判据", "失败 / 失败"],
                        ["三版总体", "A/B/C 均失败；没有抹去失败记录"],
                    ],
                    [142, 350],
                ),
            ]
        )

    if num == 9:
        story.extend([radar, Spacer(1, 12), para("每维 3 题，各重复 3 次；仅绘制客观任务验收比例。", "small")])
    if num == 12:
        story.append(
            Paragraph(
                '参考入口：<a href="https://github.com/UKGovernmentBEIS/inspect_ai" color="#087F8C">Inspect AI</a> · <a href="https://github.com/xiaowu0162/LongMemEval" color="#087F8C">LongMemEval</a> · <a href="https://github.com/HUST-AI-HYZ/MemoryAgentBench" color="#087F8C">MemoryAgentBench</a> · <a href="https://github.com/OpenMOSS/ContextWeave" color="#087F8C">ContextWeave</a>。完整 27 项索引见源码 docs/references.json。',
                S["small"],
            )
        )


def page(c, d):
    c.saveState()
    pw, ph = A4
    c.setStrokeColor(TEAL)
    c.setLineWidth(1.3)
    c.line(51, ph - 38, pw - 51, ph - 38)
    c.setFont("CJK", 8)
    c.setFillColor(GRAY)
    c.drawString(51, ph - 29, "openKylin Memory Bench · 工程验证 v3 · 交付修订 R1")
    c.setStrokeColor(colors.HexColor("#D4DFE7"))
    c.setLineWidth(0.5)
    c.line(51, 40, pw - 51, 40)
    c.setFont("CJK", 7.5)
    c.drawString(51, 26, "2026-10-04  |  真实证据可追溯  |  未进行独立人工校准")
    c.drawRightString(pw - 51, 26, f"{d.page:02d}")
    c.restoreState()


doc = SimpleDocTemplate(
    str(OUT),
    pagesize=A4,
    rightMargin=51,
    leftMargin=51,
    topMargin=62,
    bottomMargin=55,
    title="openKylin Memory Bench · 作品介绍与技术方案",
    author="openKylin Memory Bench project",
    subject="2026 上海开源软件应用创新大赛 openKylin 赛题 · 工程验证 v3 · 交付修订 R1",
)
doc.build(story, onFirstPage=page, onLaterPages=page)
from pypdf import PdfReader

pages = len(PdfReader(str(OUT)).pages)
print(
    json.dumps({"pdf": str(OUT), "pages": pages, "bytes": OUT.stat().st_size}, ensure_ascii=False)
)
assert pages >= 13, f"Missing expected sections: {pages}"
