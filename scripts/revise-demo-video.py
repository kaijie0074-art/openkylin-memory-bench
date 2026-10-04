#!/usr/bin/env python3
"""Create R1's editable recut from the retained real openKylin desktop excerpt."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/desktop-recording-final-20261002/edited-highlights-20261002.mp4"
SOURCE_SHA = "86de24a148bc1ba502dc23ceab8b8edd78b82c083ff60030ec2c6c2b5417b952"
OUT = ROOT / "reports/video-R1-20261004"
SEGMENTS = [(0, 20, 20), (198, 207.4, 60), (20, 62, 42), (62, 112, 50), (198, 207.4, 28)]
CAPTIONS = [
    (
        0,
        10,
        "openKylin Memory Bench：评测智能体跨会话记忆。\n真实桌面原片保留，R1 修订字幕与展示顺序。",
    ),
    (
        10,
        20,
        "OpenClaw 与 Hermes 使用独立身份和固定 ARM64 容器。\n调度、文件采集、评分和报告在 openKylin 上完成。",
    ),
    (
        20,
        32,
        "这里是工程 v3 正式报告，保留真实画面供阅读。\n18 道留出题 × 两款智能体 × 三次重复，共 108 次运行。",
    ),
    (
        32,
        45,
        "OpenClaw 客观任务通过 31/54，Hermes 为 26/54。\n107 次正常结束、1 次预算耗尽，全部保留。",
    ),
    (
        45,
        57,
        "动态更新：OpenClaw 为 7/9，Hermes 为 1/9。\n旧信息被更新后，最终行动是否使用新状态，是核心判据。",
    ),
    (
        57,
        69,
        "相近区分：两款智能体均为 0/9，是共同的弱项。\n不能因记住了某个事实，就认为它能正确区分相似项目。",
    ),
    (
        69,
        80,
        "每个维度只有 3 道独立题目，每题重复 3 次。\n这是本批任务的结果，不能外推数月记忆或所有工作场景。",
    ),
    (80, 92, "下面回到两款真实智能体的开发演示。\n开发记录与正式留出记录分开保存，不重复计分。"),
    (
        92,
        106,
        "运行显示 completed，只说明执行流程正常结束。\n是否交付任务要求的文件，需要检查独立采集的终态。",
    ),
    (
        106,
        122,
        "这个案例缺少要求的 current/report.txt，客观验收失败。\n不能以智能体口头宣称成功来覆盖缺失的文件。",
    ),
    (
        122,
        140,
        "A 是规则评分，B 是模型裁判，C 是混合评分。\n它们读取同一批冻结证据，逐项保留判定和引用。",
    ),
    (
        140,
        157,
        "查看运行、文件清单和判据，可以回查为什么通过或失败。\n缺证据时不能自动判通过，工具提案也不等于动作执行。",
    ),
    (
        157,
        172,
        "三版总体各为 57 通过、51 失败；6 个语义判据有分歧。\n未经过独立人工校准，模型与规则一致不能称为人工准确率。",
    ),
    (
        172,
        184,
        "R1 增加独立离线规则核验入口，不改冻结评分核心。\n它只重算 A；B/C 检查结构绑定，原始字节另按发行清单核对。",
    ),
    (
        184,
        200,
        "源码、报告、协议、证据与复现指引一起交付。\n原连续录屏另附；新修订工具的验收记录与旧原片分开。",
    ),
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def timecode(seconds: float) -> str:
    value = round(seconds * 1000)
    h, value = divmod(value, 3600000)
    m, value = divmod(value, 60000)
    s, ms = divmod(value, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> None:
    if sha(SOURCE) != SOURCE_SHA:
        raise SystemExit("source does not match retained real footage")
    OUT.mkdir(parents=True, exist_ok=False)
    graph = []
    provenance = []
    start_out = 0
    for i, (start, end, duration) in enumerate(SEGMENTS):
        hold = round(duration - (end - start), 3)
        graph.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            f"fps=10,tpad=stop_mode=clone:stop_duration={hold},"
            f"trim=duration={duration}[v{i}]"
        )
        provenance.append(
            {
                "output_start": start_out,
                "output_end": start_out + duration,
                "source_start": start,
                "source_end": end,
                "frame_hold_seconds": hold,
                "speed_changed": False,
            }
        )
        start_out += duration
    graph.append(
        "".join(f"[v{i}]" for i in range(len(SEGMENTS))) + f"concat=n={len(SEGMENTS)}:v=1:a=0,tpad=stop_mode=clone:stop_duration=1[out]"
    )
    edited = OUT / "edited-real-desktop-R1.mp4"
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-threads",
        "2",
        "-i",
        str(SOURCE),
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(graph),
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "10",
        "-frames:v",
        "2000",
        "-movflags",
        "+faststart",
        str(edited),
    ]
    (OUT / "edit-command.json").write_text(json.dumps(args, ensure_ascii=False, indent=2) + "\n")
    subprocess.run(args, check=True)
    timeline = {
        "version": "R1",
        "durationSeconds": 200,
        "fps": 10,
        "sourceWidth": 2584,
        "sourceHeight": 1618,
        "captionBandHeight": 192,
        "sourceSha256": sha(edited),
        "originalSource": str(SOURCE.relative_to(ROOT)),
        "originalSourceSha256": SOURCE_SHA,
        "originalRecordingDate": "2026-10-02",
        "statusLabel": "真实 openKylin 桌面原片 · 工程 v3 · R1 材料修订（报告画面含阅读停留）",
        "new_tool_execution_claimed": False,
        "segments": provenance,
        "captions": [{"start": a, "end": b, "text": text} for a, b, text in CAPTIONS],
    }
    timeline_path = OUT / "video-timeline-R1.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n")
    (OUT / "字幕-R1.srt").write_text(
        "\n\n".join(
            f"{i}\n{timecode(a)} --> {timecode(b)}\n{text}"
            for i, (a, b, text) in enumerate(CAPTIONS, 1)
        )
        + "\n"
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "competition/publication/render-captioned-video.py"),
            "--source",
            str(edited),
            "--timeline",
            str(timeline_path),
            "--font",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "--output",
            str(OUT / "captioned"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
