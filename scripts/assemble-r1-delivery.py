#!/usr/bin/env python3
"""Inspect R1 inputs by default; explicitly assemble new complete and slim archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = "openkylin-memory-bench"
PROTOCOL = "reports/engineering-v3-protocol-20261001.json"
DATASET = "datasets/engineering-v2"
SOURCE_SINGLES = ("README.md", "AGENTS.md", "LICENSE", "NOTICE", "pyproject.toml", "uv.lock",
                  ".env.example", ".gitignore", ".github/workflows/ci.yml")
PUBLIC_DOCS = {"deb-packaging.md", "task-matrix.md", "agent-adapters.md", "engineering-validation.md",
               "agent-images.json", "development-case-update-001.md", "openkylin-validation.md",
               "public-results.md", "validation-status.json", "references.md", "competition-deliverables.md",
               "batch-runner.md", "engineering-holdout-incident.md", "demo-recording-plan.md",
               "delivery-manifest.md", "model-gateway.md", "references.json", "openkylin-mac-vm.md",
               "implementation.md", "engineering-v3-results.md", "delivery-errata-R1.md",
               "agent-image-delivery-R1.md"}
PUBLIC_COMPETITION = {"评审复现说明.md", "材料核验.json", "作品介绍与技术方案.md", "冻结运行环境.json",
                      "交付核对.json", "publication/README.public.md", "publication/public-results.md",
                      "publication/render-captioned-video.py"}
REQUIRED_SOURCE = ("containers/openclaw.Dockerfile", "containers/hermes.Dockerfile",
                   "scripts/verify-frozen-rules.py", "scripts/build-deb.py", "scripts/run-benchmark.py",
                   "scripts/assemble-r1-delivery.py", "scripts/check-r1-delivery.py",
                   "scripts/revise-demo-video.py", "scripts/build-introduction-pdf.py",
                   "competition/publication/render-captioned-video.py")
SOURCE_TYPES = {
    "src": {".py"}, "scripts": {".py", ".sh", ".ps1"}, "tests": {".py"},
    "containers": {".py", ".sh", ".md", ".json", ".txt", ".Dockerfile"},
    "data": {".md", ".json", ".txt", ".csv"},
    "datasets": {".md", ".json", ".txt", ".csv"},
    "docs": {".md", ".json", ".svg", ".png"},
    "examples": {".json", ".md"},
    "competition": {".md", ".json", ".py", ".html", ".css", ".js", ".srt"},
}
EDITED_VIDEO = "reports/desktop-recording-final-20261002/edited-highlights-20261002.mp4"
EDITED_VIDEO_SHA256 = "86de24a148bc1ba502dc23ceab8b8edd78b82c083ff60030ec2c6c2b5417b952"
RECORDS = (PROTOCOL, EDITED_VIDEO, "reports/engineering-v3-holdout-20261001",
           "reports/engineering-v3-selection-20261001", "reports/engineering-v3-controls-20261001",
           "reports/engineering-v3-boundary-smoke-20261001", "reports/desktop-demo-real-20261002",
           "reports/offline-rule-verification-r1-final-20261004", "reports/video-R1-20261004",
           "reports/delivery-R1-acceptance-20261004")
RAW_VIDEO = "reports/desktop-recording-final-20261002/raw-openkylin-desktop-demo-20261002.mp4"
EXCLUDED_PARTS = {".git", ".runtime", ".venv", "__pycache__", ".pytest_cache", ".hypothesis", ".ruff_cache"}


class Rejected(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def local(path):
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink_path_forbidden")
    return path.resolve()


def excluded(path):
    return (any(part in EXCLUDED_PARTS or "ready-to-submit" in part for part in path.parts)
            or any("first-render" in part for part in path.parts)
            or path.suffix == ".pyc" or any(part.startswith(".env") and part != ".env.example" for part in path.parts))


def regular_files(root, *, source=False):
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in paths:
        if excluded(path):
            continue  # Never read excluded private configuration.
        require(not path.is_symlink(), "symlink_payload_forbidden")
        if path.is_dir():
            continue
        require(path.is_file(), "special_payload_forbidden")
        if source:
            relative = path.relative_to(root.parents[0])
            inside = path.relative_to(root).as_posix()
            if root.name == "docs" and inside not in PUBLIC_DOCS:
                continue
            if root.name == "competition" and inside not in PUBLIC_COMPETITION:
                continue
            if path.suffix not in SOURCE_TYPES[root.name] and path.name not in {
                    "LICENSE", "NOTICE", "Dockerfile", ".dockerignore"}:
                continue
            require(not any(part.startswith(".") for part in relative.parts[1:])
                    or path.name == ".dockerignore", "hidden_source_payload_forbidden")
        yield path


def source_files(root):
    result = [root / name for name in SOURCE_SINGLES if (root / name).is_file()]
    for name in SOURCE_TYPES:
        require((root / name).is_dir(), f"source_directory_missing:{name}")
        result.extend(regular_files(root / name, source=True))
    for path in result:
        local(path)
    require(set(REQUIRED_SOURCE) <= {p.relative_to(root).as_posix() for p in result}, "required_source_file_missing")
    return sorted(set(result))


def fingerprint(root):
    value = hashlib.sha256()
    for path in sorted((root / "src/kmb").glob("*.py")):
        value.update(path.name.encode() + path.read_bytes())
    for folder in ("tasks", "rubrics"):
        for path in sorted((root / DATASET / folder).glob("*.json")):
            value.update((folder + "/" + path.name).encode() + path.read_bytes())
    value.update((root / "uv.lock").read_bytes())
    return value.hexdigest()


def json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def text_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value)


def copy(src, dst):
    local(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        # Traverse first; shutil.copytree must never follow a payload symlink.
        list(regular_files(src))
        shutil.copytree(src, dst, ignore=lambda directory, names: [
            name for name in names if excluded(Path(directory) / name)])
    else:
        require(src.is_file(), "copy_input_missing")
        shutil.copy2(src, dst)


def public_source_copy(src, dst, relative):
    copy(src, dst)
    if relative == "datasets/engineering-v2/audit-report.json":
        value = json.loads(dst.read_text())
        value.update(source="data", candidate=DATASET)
        dst.chmod(0o600)
        dst.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def tar_files(path, files, root, prefix=""):
    with tarfile.open(path, "w:gz", compresslevel=6) as archive:
        for file in files:
            info = archive.gettarinfo(str(file), arcname=prefix + file.relative_to(root).as_posix())
            require(info.isfile(), "non_regular_archive_member")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if file.stat().st_mode & 0o111 else 0o644
            with file.open("rb") as stream:
                archive.addfile(info, stream)


def zip_files(path, files, root, prefix=""):
    with zipfile.ZipFile(path, "x") as archive:
        for file in files:
            info = zipfile.ZipInfo.from_file(file, prefix + file.relative_to(root).as_posix())
            info.external_attr = (stat.S_IFREG | (0o755 if file.stat().st_mode & 0o111 else 0o644)) << 16
            info.compress_type = zipfile.ZIP_STORED if file.suffix in {".mp4", ".deb", ".gz", ".zip"} else zipfile.ZIP_DEFLATED
            with file.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)


def seal(folder, metadata):
    files = list(regular_files(folder))
    inventory = {p.relative_to(folder).as_posix(): {"bytes": p.stat().st_size, "sha256": digest(p)} for p in files}
    json_file(folder / "交付清单.json", {**metadata, "files": inventory})
    inventory["交付清单.json"] = {"sha256": digest(folder / "交付清单.json")}
    text_file(folder / "checksums.sha256", "".join(f"{row['sha256']}  {name}\n" for name, row in sorted(inventory.items())))
    return {"files": len(files), "manifest_sha256": digest(folder / "交付清单.json")}


def assemble(args):
    root, output, slim = local(args.source_root), local(args.output), local(args.slim_output)
    outputs = [local(path) for path in (output, slim, output.with_name(output.name + ".zip"), slim.with_name(slim.name + ".zip"))]
    checksums = local(output.parent / f"{output.name}-发行SHA256SUMS.txt")
    require(all(not path.exists() for path in outputs), "output_already_exists")
    require(not checksums.exists(), "release_checksum_already_exists")
    require(not output.is_relative_to(slim) and not slim.is_relative_to(output), "output_paths_overlap")
    require(all(not path.is_relative_to(root / folder) for path in outputs for folder in SOURCE_TYPES), "output_inside_source_payload")
    for kind, suffix in (("pdf", ".pdf"), ("deb", ".deb"), ("video", ".mp4")):
        supplied = getattr(args, kind)
        require(not excluded(supplied) and supplied.suffix.lower() == suffix, "private_or_wrong_media_path")
    media = {"pdf": local(args.pdf), "deb": local(args.deb), "video": local(args.video), "raw_video": local(root / RAW_VIDEO)}
    record_paths = [*RECORDS, *args.record]
    require(len(set(record_paths)) == len(record_paths), "duplicate_record_input")
    for name in record_paths:
        require(name.startswith("reports/") and ".." not in Path(name).parts and not Path(name).is_absolute(), "unsafe_record_path")
        require(not excluded(Path(name)), "private_record_path")
    protected = [*[root / name for name in record_paths], *media.values(), root / "uv.lock"]
    require(all(not target.is_relative_to(path) and not path.is_relative_to(target)
                for target in [*outputs, checksums] for path in protected), "output_overlaps_input")
    required = [*media.values(), *[root / name for name in record_paths], root / "competition/作品介绍与技术方案.md",
                root / "competition/评审复现说明.md", root / "docs/engineering-v3-results.md"]
    missing = [str(path) for path in required if not path.exists()]
    if not args.assemble:
        return {"status": "inputs_inspected", "inputs_ready": not missing, "missing_inputs": missing,
                "output_created": False, "new_experiment": False, "model_calls": 0, "agent_runs": 0}
    require(not missing, "required_inputs_missing:" + ",".join(missing))
    for path in required:
        local(path)
    require(all(path.is_file() and path.stat().st_size for path in media.values()), "media_input_empty_or_wrong_type")
    for kind, path in media.items():
        with path.open("rb") as stream:
            header = stream.read(16)
        require(header.startswith(b"%PDF-") if kind == "pdf" else header.startswith(b"!<arch>\n")
                if kind == "deb" else header[4:8] == b"ftyp", "media_signature_mismatch")
    files = source_files(root)
    original_protocol = json.loads((root / PROTOCOL).read_text())
    frozen_fingerprint = fingerprint(root)
    require(frozen_fingerprint == original_protocol["experiment_fingerprint"], "frozen_core_dataset_or_lock_changed")
    original_source_hashes = {p.relative_to(root).as_posix(): digest(p) for p in files}
    source_hashes = dict(original_source_hashes)
    records = sorted({p for name in record_paths for p in regular_files(root / name)})
    record_hashes = {p.relative_to(root).as_posix(): digest(p) for p in records}
    require(record_hashes.get(EDITED_VIDEO) == EDITED_VIDEO_SHA256, "video_rebuild_input_changed")
    media_hashes = {name: digest(path) for name, path in media.items()}
    output.mkdir(parents=True, exist_ok=False)
    copy(media["pdf"], output / "01_作品介绍与技术方案.pdf")
    copy(root / "competition/作品介绍与技术方案.md", output / "01_作品介绍与技术方案.md")
    for name in ("评审复现说明.md", "冻结运行环境.json"):
        copy(root / "competition" / name, output / "03_源码与复现" / name)
    source_dir = output / "03_源码与复现"
    # Derive public metadata in a new temporary tree; frozen source files stay read-only.
    with tempfile.TemporaryDirectory(prefix="kmb-r1-public-source-") as temporary:
        stage = Path(temporary)
        staged_files = []
        for file in files:
            relative = file.relative_to(root).as_posix()
            destination = stage / relative
            public_source_copy(file, destination, relative)
            source_hashes[relative] = digest(destination)
            staged_files.append(destination)
        tar_files(source_dir / "源码.tar.gz", staged_files, stage, SOURCE_ROOT + "/")
        zip_files(source_dir / "源码.zip", staged_files, stage, SOURCE_ROOT + "/")
    tar_files(output / "08_完整实验凭证.tar.gz", records, root)
    copy(root / "reports/engineering-v3-holdout-20261001/report", output / "04_正式实验报告/report")
    summary = (root / "docs/engineering-v3-results.md").read_text()
    summary = re.sub(r"\[([^\]]+)\]\((?!https?://|#)[^)]+\)", r"\1", summary)
    text_file(output / "04_正式实验报告/结果摘要.md", summary + "\n\n[完整正式报告](report/index.html)。\n")
    copy(media["deb"], output / "05_安装包" / media["deb"].name)
    copy(media["video"], output / "06_演示视频/讲解字幕版-R1.mp4")
    copy(media["raw_video"], output / "06_演示视频/完整原片-20261002.mp4")
    sample_rows = []
    for task, run in (("boundary-001", "reports/engineering-v3-boundary-smoke-20261001"),
                      ("update-001", "reports/desktop-demo-real-20261002")):
        destination = output / "02_样例任务与结果" / task
        for folder, name in (("tasks", "任务.json"), ("rubrics", "私有判据.json")):
            copy(root / DATASET / folder / f"{task}.json", destination / name)
        copy(root / run / "report", destination / "report")
        copy(root / run / "scores", destination / "scores")
        for evidence in sorted((root / run / "report/evidence").glob("*.json")):
            value = json.loads(evidence.read_text())
            copy(evidence, destination / "evidence" / evidence.name)
            sample_rows.append({key: value[key] for key in ("task_id", "run_id", "agent", "status", "evidence_hash")})
    require(len(sample_rows) == 4 and len({row["run_id"] for row in sample_rows}) == 4, "sample_matrix_changed")
    json_file(output / "02_样例任务与结果/样例索引.json", {"scope": "historical_development_samples", "runs": sample_rows})
    instructions = """# openKylin Memory Bench · R1 修订交付

2026-10-04。R1 修复离线规则核验入口和交付状态、复现说明及演示表达。108 次真实留出和 324 条原 A/B/C 评分未重新运行；新核验只重算 A，并验证 ABC 结构绑定。B/C 语义未重算，无独立人审，未宣布评分器赢家。

原邮件已于 2026-10-04 12:43 被发件服务器接受，未验证主办方阅读。这个本地产物尚未重新发布或补交。

完整包提供方案、四份历史开发样例、源码及 engineering-v2 题库、正式 HTML 报告、revision 4 ARM64 安装包、R1 字幕视频、连续原片和原布局实验凭证。解压 03_源码与复现/源码.tar.gz 得到 openkylin-memory-bench，再在该源码目录解压 08_完整实验凭证.tar.gz，恢复 reports/。核对外部发行 SHA-256，再运行 scripts/check-r1-delivery.py 或遵循 competition/评审复现说明.md。

精简包提供方案、源码、四份历史样例、正式结果摘要和 R1 字幕视频；完整留出报告、108 次原始证据、安装包及连续原片在完整包。精简包不能替代完整技术复现。

适用限制：openKylin 3.0 ARM64；智能体使用 Debian 容器用户空间；短会话与 600 秒/20 次工具动作预算；未集成 AgentOS SDK。模型账户、Docker 镜像和虚拟机磁盘不在本包。源码首次安装需下载声明的依赖；现成 .deb 的 Python 依赖已打包，系统 Python/本机库仍需满足声明。
"""
    text_file(output / "00_交付目录与修订说明.md", instructions)
    text_file(output / "08_实验凭证解压说明.md", "# 恢复实验凭证\n\n先核对 checksums.sha256，解压源码.tar.gz 后，将 08_完整实验凭证.tar.gz 解压到所得源码目录内。归档仅含相对常规文件，拒绝绝对路径、..、链接及特殊成员。恢复后从源码根目录执行评审复现说明；datasets/engineering-v2 与协议 v3 是不同命名层。\n")
    json_file(output / "07_验收记录/来源与冻结指纹.json", {"schema_version": 1, "revision": "R1",
              "frozen_experiment_fingerprint": frozen_fingerprint, "uv_lock_sha256": digest(root / "uv.lock"),
              "core_sha256": {p.name: digest(p) for p in sorted((root / "src/kmb").glob("*.py"))},
              "source_files_sha256": source_hashes, "record_files_sha256": record_hashes,
              "original_source_files_sha256": original_source_hashes,
              "public_derivations": {"datasets/engineering-v2/audit-report.json": "source/candidate local paths replaced with relative dataset paths"},
              "media_files_sha256": media_hashes,
              "assembly_scope": "local_only_not_published", "credential_files_read": False,
              "source_exclusions": [*sorted(EXCLUDED_PARTS), "ready-to-submit", ".env* except .env.example"]})
    require(all(digest(root / name) == value for name, value in original_source_hashes.items()), "source_changed_during_assembly")
    require(all(digest(root / name) == value for name, value in record_hashes.items()), "records_changed_during_assembly")
    require(all(digest(media[name]) == value for name, value in media_hashes.items()), "media_changed_during_assembly")
    slim.mkdir(parents=True, exist_ok=False)
    for name in ("00_交付目录与修订说明.md", "01_作品介绍与技术方案.pdf", "01_作品介绍与技术方案.md",
                 "02_样例任务与结果", "03_源码与复现", "07_验收记录"):
        copy(output / name, slim / name)
    text_file(slim / "04_正式实验报告/结果摘要.md", summary + "\n\n精简包只含此摘要，完整正式 HTML 报告与原始留出凭证位于完整包。\n")
    copy(media["video"], slim / "06_演示视频/讲解字幕版-R1.mp4")
    metadata = {"schema_version": 1, "revision": "R1", "date": "2026-10-04", "new_experiment": False,
                "model_calls": 0, "agent_runs": 0, "published": False, "resubmitted": False,
                "frozen_experiment_fingerprint": frozen_fingerprint, "original_holdout_runs": 108,
                "original_ABC_scores": 324, "independent_human_review": "not_performed"}
    complete = seal(output, {**metadata, "kind": "complete"})
    small = seal(slim, {**metadata, "kind": "slim"})
    zip_files(outputs[2], list(regular_files(output)), output, output.name + "/")
    zip_files(outputs[3], list(regular_files(slim)), slim, slim.name + "/")
    text_file(checksums, "".join(f"{digest(path)}  {path.name}\n" for path in outputs[2:]))
    return {"status": "assembled_not_published", "complete": str(output), "slim": str(slim),
            "complete_inventory": complete, "slim_inventory": small, "release_checksums": str(checksums),
            "archives": [{"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)} for path in outputs[2:]],
            "frozen_experiment_fingerprint": frozen_fingerprint, "model_calls": 0, "agent_runs": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=PROJECT)
    parser.add_argument("--output", type=Path, default=PROJECT / "deliverables/competition-R1-20261004")
    parser.add_argument("--slim-output", type=Path, default=PROJECT / "deliverables/competition-R1-20261004-slim")
    parser.add_argument("--pdf", type=Path, default=PROJECT / "output/pdf/openKylin-Memory-Bench-R1-作品介绍与技术方案.pdf")
    parser.add_argument("--deb", type=Path, default=PROJECT / "dist/openkylin-memory-bench_0.1.0-4_arm64.deb")
    parser.add_argument("--video", type=Path, default=PROJECT / "reports/video-R1-20261004/captioned/openKylin-Memory-Bench-讲解字幕版.mp4")
    parser.add_argument("--record", action="append", default=[], help="Additional reports/... input; original layout preserved")
    parser.add_argument("--assemble", action="store_true", help="Explicitly create new archives; never publishes")
    args = parser.parse_args(argv)
    try:
        result = assemble(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc), "published": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
