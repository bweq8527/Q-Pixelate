# Timeline and layers implementation

Implement the approved [design](../specs/2026-11-20-timeline-layers-design.md) in the current checkout.

1. Reproduce lower-track seek, then separate gestures in `Timeline`. Use project-aware selection, drag bounds, and gap subtraction in `MainWindow`. Exercise clicks, locked selections, and overlapping right-drag creation with Qt events.
2. Add relative layer timing and separate sticker/mask transforms in `model.py`. Wire v3 persistence, legacy effect conversion, source-frame intervals, shared geometry rasterization, and FFmpeg composition. Verify actual pixels, relative asset paths, clipping after trimming, and copy spans.
3. Add draggable thumbnails, the scrollable layer inspector and lane rows, and independent canvas transforms. Connect layer drafts, confirmation, time and geometry editing, and viewport drop at the current source time. Exercise the normal UI entry points with Qt events.
4. Verify the full `unittest` suite, compilation, and offscreen layout. Update the user instructions for the new gestures.

All four steps are complete. Independent review led to contextual Delete handling, preserving hidden layer timing on focus changes, Ctrl+T completion in geometry inputs, valid frame placement near the end of a selection, and preserving even-pixel bounds when migrating old stickers.

Final verification: 57 tests passed in 47.864 seconds. Compilation and Git whitespace checks passed. Qt offscreen layout was checked at 1040×760 and 1390×960. FFmpeg tests checked composition order, independent mask geometry, layer timing, legacy sticker coverage and local re-encoding.
