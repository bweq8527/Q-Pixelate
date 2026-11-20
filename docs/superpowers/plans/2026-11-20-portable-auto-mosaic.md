# Portable launcher and automatic mosaic implementation plan

> **For agentic workers:** Use superpowers:executing-plans and test the behavior before implementing it. Checkboxes track delivery.

**Goal:** Run Q-Pixelate on fresh Windows x64 installations and optionally scan selected video ranges for editable mosaic regions.

**Architecture:** A standard library and Tk launcher manages verified, private component packages. The editor runs scans in an isolated subprocess and stores the resulting geometry as native project layers. FFmpeg renders these layers for preview and export.

**Spec:** `docs/superpowers/specs/2026-11-20-portable-launcher-auto-mosaic-design.md`

- [x] Implement component catalog, portable install, probe, rollback and uninstall. Test verified offline install, rejected or interrupted replacement, and relocation.
- [x] Build the minimal PyInstaller executable and a release recipe for the base editor, FFmpeg, CPU and GPU dependencies, and ONNX models. Verify first startup without PATH tools and an offline package handoff. Keep the source launch workflow usable.
- [x] Implement streaming, cancellable scan with selected CPU or GPU provider, exact source timestamps, box coordinate mapping and track grouping. Verify with deterministic detections on a generated clip and model smoke tests.
- [x] Persist mosaic geometry in editable layers. Render interpolated bounds within the selected source frames, before retiming. Verify geometry interpolation, project round-trip, activity before/after the layer, and actual video pixels.
- [x] Integrate component settings and a scan review panel with jump to detection, toggle selection and apply without duplicates. Verify the usual edit/preview/export flows, including saved mosaic without a model.
- [x] Review unresolved compatibility and distribution requirements, run automated checks and document any manual validation that remains.
