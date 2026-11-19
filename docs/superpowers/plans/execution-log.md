# Execution log

- 2026-11-19: Design approved by user (“开始吧”). Execute inline with writing-plans, executing-plans and test-driven-development workflows.
- Workspace check: project has no Git repository or existing branch; use supplied dedicated project directory in place. Dependencies Python, PySide6, FFmpeg and ffprobe already installed.
- Ruling: Treat “开始吧” as authorization to execute the approved design and implementation plan without another approval round; native inline implementation avoids extra coordination.
- Task 1 complete: model/project/interval tests observed RED (missing module) → GREEN, five tests passed.
- Task 2 complete: real FFmpeg integration tests observed RED → GREEN. Tested H264/B frames, masks/images, original PCM hashes, exact copied-frame hashes, fractional FPS, MKV, cancellation and unsupported inputs.
- Debugging: MKV source streamcopy seek returned previous GOP, despite correct total frame count. Isolated by mapping decoded output frames to source frame hashes; fixed with DTS-aware output-side trimming. Open-GOP keyframe flags do not imply IDR; added NAL inspection and explicit rejection.
- Ruling: Keep first-release zero-video-start restriction; default MP4→MKV remux can shift video21ms due AAC preroll, so tests that claim supported MKV input explicitly preserve zero video start. Nonzero input gives a clear error rather than changing timing silently.
- Task 3 complete: Qt workflow tests observed RED → GREEN; native desktop selection, region drawing, preview, project save and full export succeeded. Short preview loop and initial frame decode verified with actual Qt multimedia events.
- Full suite: `python -m unittest discover -s tests -v` → 24 passed in14.942s. `python -m compileall -q q_pixelate` → exit0.
- One-hour synthetic verification:108000frames/30fps,160x90,137903218bytes; export4.112s;2s encoded/3598s copied. Entire output frameMD5: only60frames within encoded GOP changed;107940untouched frames identical. Low-resolution benchmark is not a real-game performance guarantee.
- Final independent read-only code review complete; no implementation commits because this directory has no Git repository.
- Final: fixed fractional-FPS first-frame leak — integer frame bounds shared by planning/export/preview; `test_fractional_frame_at_exact_effect_start_is_already_redacted` RED→GREEN.
- Final: fixed mask far-edge undercoverage — outward chroma-aligned far-edge rounding; `test_mask_rounding_covers_selected_far_edges` RED→GREEN.
- Final: fixed unsaved form loss — pending fields mark dirty and commit when selecting another clip; dirty/switch regressions RED→GREEN.
- Final: fixed preview proportions and playback mask state — 4:3/SAR/playback-gap regressions RED→GREEN. No deferred minor findings.
- Native follow-up: audio project reload hung at QAudioOutput.setMuted during player recreation (recorded by faulthandler). Removed unnecessary transient muting; reproduced native reload now succeeds and rendered blur preview stays visible while looping.
- Final full suite:33/33 passed in20.778s; compileall exit0. Final1-hour export4.156s with2s encoded;108000decoded frames,107940untouched hashes identical. Desktop test windows closed.
- Ruling: Third-party avc3 playback/upload acceptance remains explicitly unverified; a real platform upload requires a user's own workflow. Incorrect assumption would mean the platform needs a different export container/codec policy; no upload was attempted.
