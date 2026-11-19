# Editor upgrade implementation plan

> Execute inline using executing-plans and test-driven-development. User explicitly requested implementation now; no additional approval round. Continue in the supplied directory (no Git repository).

Goal: implement the eight requested editor improvements and the select → lock range → edit → confirm workflow; defer automatic nudity detection.

Architecture: keep PySide6 and local FFmpeg. Source timestamps remain stable in the editor. Speed changes alter output duration; retime only affected GOPs, concatenate untouched GOPs by stream copy, and retime all audio tracks with pitch-preserving atempo when speed changes exist. Normal-speed projects keep original audio copy.

Decisions: seek N defaults to 5 seconds (editable); long press threshold 300 ms, shuttle at 3x with silent reverse seek previews; speed range 0.25–4x. New clips start with locked source boundaries; finishing confirms and locks the clip, explicit unlock permits editing. Unconfirmed changes block clip switching/save/export rather than silently committing. Preview can render the current draft. Sticker library copies imported assets into a persistent local app-data folder; project paths remain portable relative paths. Legacy image edits load as stickers. Shapes: rectangle, ellipse, closed lasso; feather percentage of the shorter source dimension, 0–20%, softened inward so pixels outside the selected shape remain unmodified.

1. Model/export: add speed, shape/points, feather, range and edit locks; project compatibility. Tests: persistence, malformed values, output duration/frame order, all audio tracks, preview speed, silent/fractional FPS. Fix reproduced concat path handling under sandbox paths containing #.
2. Widgets: separate playback upper lane and selection lower lane; pan with middle drag/Alt-left and scrollbar. Draw rect/ellipse/lasso. Tests: playback clicks never move selection; locked range cannot drag; pan preserves cursor/selection; lasso stays inside video and emits a closed valid shape.
3. Editor: explicit draft/confirm/lock flow; reusable sticker import; speed/shape/feather controls; key event filtering prevents focused buttons/sliders from capturing playback shortcuts. Tests: range lock and completed lock enforcement; no silent save/switch; space on focused controls; single/shift/held arrows; persistent library.
4. Run complete suite and compileall, inspect rendered assets and Qt screenshot without desktop control; independent read-only review, address material findings. Update README/verification and this ledger.

Review focus: media near frame boundaries, multiple speed edits in one GOP, multi-track audio duration, key releases/focus loss, legacy projects and missing sticker assets.

## Progress

- Baseline: 33 tests, 1 failure and 9 errors, rooted in FFmpeg concat relative-path URL resolution when sandbox temporary path contains #. UI teardown error followed failed export leaving a window open. Existing source/tests unchanged at this point.
- Ruling: apply requested changes directly, using current stack. User's “开始执行” authorizes implementation, with routine choices recorded above. No computer-control tools used.
- Task 1 complete: model extended-field roundtrip/validation RED → GREEN; old v1 projects still load. Real FFmpeg export tests include multiple speed edits in one GOP, 0.25× at EOF, fractional FPS, silent and multiple audio tracks, speed preview, copied tail hashes, mask geometry, inward feather pixel contrast, and delayed audio onset.
- Task 2 complete: separate upper playback lane/lower selection lane, immutable locked selection, middle/Alt drag pan; rectangle/ellipse/lasso drawing and shared normalized paths. Lasso signal reentrancy fixed with a regression for persisted bounds.
- Task 3 complete: explicit confirm-and-lock workflow, protected completed edits, persistent copied sticker assets, shift/held arrow navigation and focused-button Space routing. Pending edits block switching/export. Legacy range drag now marks unsaved state.
- Review: independent read-only reviewer reported delayed-audio onset loss and unmarked legacy range drafts; both reproduced with failing tests and fixed. No other material findings. Minor lasso-bounds signal ordering also fixed and tested.
- Verification: full suite 50/50 passed in 39.521s; compileall passed. Qt offscreen screenshots inspected at 1390×960 and 1040×760, confirmation controls fixed outside scroll area. Offscreen QA explicitly loaded installed Windows fonts (offscreen font discovery was empty); no desktop capture/control used.

- Final acceptance: `python -m unittest discover -s tests -v` → 51/51 passed in 39.265s, exit 0; compileall exit 0. Added scrollbar full-view/zoom-to-end regression; all requested features implemented, nudity detection deferred. Current project source updated in place; no Git commit (no repository).
