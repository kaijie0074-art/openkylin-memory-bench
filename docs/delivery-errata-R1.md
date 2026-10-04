# Delivery correction R1 · 2026-10-04

The original engineering v3 release and submitted attachments remain historical records. R1 corrects the delivery instructions and material status; the 108 holdout runs and 324 original A/B/C scores continue to bind the original v3 protocol and unchanged evaluator core.

## Corrected issues

- The original reviewer instruction `score --variant A --protocol ...` is rejected by the engineering gate, which requires complete A/B/C scoring and a matching judge. R1 supplies the separate `kmb-verify-rules` tool to verify original evidence/scores and recompute A without a provider. Its output is a verification record, not new formal scores.
- The original PDF and ZIP contain publication placeholders predating release. Repository, video and original assets are now available through the actual links below. R1 PDF and ZIP are generated as new files, with their own checksums.
- Current protocol metadata points to engineering v3; engineering v2 and its failed/recovery batches are explicitly historical. The dataset remains `datasets/engineering-v2`.
- Native openKylin Docker instructions distinguish the Linux bridge gateway from the Mac Colima context. Two development configs select `boundary-001` and `update-001` respectively.

## Current public entry points

- [Source](https://github.com/kaijie0074-art/openkylin-memory-bench)
- [Video viewer](https://kaijie0074-art.github.io/openkylin-memory-bench/)
- [Original engineering v3 release](https://github.com/kaijie0074-art/openkylin-memory-bench/releases/tag/v0.1.0-engineering-v3)

The original submission was accepted by the sending SMTP server on 2026-10-04; organizer delivery, reading and acceptance have not been verified. Recall is unsupported for the recipient domain. R1 publication and resubmission are recorded only after their actual completion.

## Validation

Revision 4 passed fresh openKylin ARM64 installation, network-disabled 108-A verification and report regeneration, then four real development runs and twelve scores (three task passes, one budget exhaustion/failure). Final archive checks are separately bound to finished artifact SHA-256 values. Live progress and exact evidence paths are in [validation-status.json](validation-status.json). Existing 592-test results describe the original source checks; new test and installation results are recorded with their date and scope. No independent human semantic validation has been performed.

## Material generation environment

The optional PDF/video generators are Mac host tools, separate from the frozen evaluation runtime. The PDF uses ReportLab, pypdf and macOS Arial Unicode/Arial Bold fonts; these material-only dependencies are not added to `uv.lock`. The video uses ffmpeg and the preserved 207.4-second recording input. openKylin evaluator installation and report regeneration do not require either generator. Their source and generation/visual QA records are provided; a different host must explicitly prepare compatible fonts and material dependencies before rebuilding.
