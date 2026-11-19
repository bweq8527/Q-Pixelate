# Q-Pixelate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a local Windows video redaction editor with verified partial re-encoding and stitching.

**Architecture:** PySide6 desktop UI; independent Python project/model, FFmpeg process runner, video probe/planner and export engine. Work in the supplied new project directory, which has no Git repository or existing branch to isolate. Execute inline following the user's “开始吧”; do not request another plan approval.

**Tech Stack:** Python 3.12, PySide6, FFmpeg/ffprobe 8, unittest.

**Spec:** docs/superpowers/specs/2026-11-19-q-pixelate-design.md

## Global Constraints

- Fixed masks only; one effect per non-overlapping edit interval.
- Preserve source files and original full audio tracks; output is a new file.
- Never silently fall back to full re-encoding.
- Fast mode starts with H.264, 8-bit SDR, progressive CFR; reject unsupported cases explicitly.
- Background jobs, cancellation, Chinese paths, project persistence, true rendered preview.

## Review Focus

- B-frame boundaries: no missing/repeated frames or timestamp drift.
- Edited images in paths containing punctuation: no filter/shell injection.
- Failure/cancellation: existing destination/source survives and task files are cleaned.
- Project reload: changed source/assets fail clearly, unsaved edits are protected.
- Display letterboxing: mask coordinates remain correct when resized.

### Task 1: Project data and interval planning

Files: q_pixelate/model.py, tests/test_model.py, requirements.txt.
Interfaces: Rect, Edit, Project; Project.validate(duration), save(path), load(path); plan_ranges(edits, keyframes, total_frames) -> list[Span]. Times are seconds, ranges are integer presentation-frame indices after quantization, normalized rectangles.

- [x] Write failing tests for non-overlap, invalid numbers/rectangles, merged encoding ranges, saved relative paths and source-change detection.
- [x] Run `python -m unittest discover -s tests -v` and observe missing behavior.
- [x] Implement model and keyframe interval planner; run same tests to green.

### Task 2: Video engine and real integration verification

Files: q_pixelate/process.py, q_pixelate/media.py, q_pixelate/render.py, tests/test_render.py, tests/media_helpers.py.
Interfaces: Runner(cancel_event, progress, log).run(args); probe(path, runner)->MediaInfo; index_video(info, runner)->VideoIndex; Exporter.export(project, destination, runner); render_preview(project, edit_index, destination, runner).

- [x] Write failing real FFmpeg tests for full/region blur and image overlay, non-keyframe edits, unchanged frames/audio and total decoded frame count.
- [x] Generate CFR H.264 fixtures with B frames, multiple audio tracks and time-varying content. Use lossless decoded checks on untouched portions.
- [x] Implement asynchronous-safe subprocess runner, packet indexing, filters, keyframe-aligned video-only copy/encode chunks, concatenation and original-audio mux. Match original frame cadence and timestamps. Verify output duration, packet count and decode boundaries before success.
- [x] Extend tests to beginning/end/adjacent edits, no-audio/no-edits, fractional FPS, punctuation paths and cancellation preserving existing output. Unsupported codecs/VFR/start offsets produce explicit errors.
- [x] Run the full unittest suite.

### Task 3: Desktop editor

Files: q_pixelate/app.py, q_pixelate/widgets.py, q_pixelate/jobs.py, q_pixelate/__main__.py, tests/test_ui.py.
Interfaces: MainWindow.open_video(path), set_project(project), export_to(path); Timeline seek/selection signals; VideoCanvas normalized rectangle signals; Worker background task success/error/progress signals.

- [x] Write Qt offscreen workflow tests for opening media, adding/editing ranges, drawing normalized masks, project round-trip and export jobs.
- [x] Implement dark Chinese editor, playback, zoomable draggable timeline/selection, editable timestamps, effects and assets, rendered preview and project persistence. Lock source/project mutation during jobs and provide cancellation.
- [x] Verify tests plus native desktop opening, masking, preview and export with an actual generated video.

### Task 4: Delivery and review

Files: README.md, launch.ps1, launch.bat, .gitignore, docs/verification.md.

- [x] Write startup and supported-input instructions, exact limitations, reproducible tests and export verification notes.
- [x] Run full test suite and compileall. Get one fresh read-only review of all implementation against the design; fix important findings with regression tests.
- [x] Deliver working project and launcher with concise usage instructions and measured limitations.

## Execution record

Implementation progress and decisions are recorded in docs/superpowers/plans/execution-log.md. No Git commits are assumed because the supplied project is not a repository.
