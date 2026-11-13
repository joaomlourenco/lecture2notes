# Release notes

## v0.1 — 2026-11-13

First public release.

### What it does

- Turns a lecture recording (MOV, MP4, audio, anything ffmpeg reads) and, optionally, its slides (PPTX or PDF) into a
  timestamped transcript and slide-by-slide reading notes, in Markdown and PDF.
- Transcribes locally with Whisper: mlx-whisper on Apple Silicon (default) or the openai-whisper command line
  (`--engine openai`). Long recordings are protected against repetition loops.
- Assigns the speech to slides from the slide number read in the video (tesseract), with a words-based fallback
  (TF-IDF and a Viterbi path). Going back to earlier slides is handled.
- Works without slides: sections from the distinct screens of the video (with pictures and OCR text), or from the speech
  cut into parts by time.
- Handles several decks, a lecture in several recordings, a folder of recordings (one lecture each) and
  English/Portuguese mismatches between slides and speech (English translation by Whisper when needed).
- `--student-notes`: reading notes for students written by Claude Code, with figures, review questions and a glossary,
  built as DOCX and PDF with a kit written next to them. Can also work from slides and readings alone when the
  recording failed (`--notes-extra`).
- `--latex yes|no`: PDFs are made only when a LaTeX engine is installed (checked on every run; `--latex` overrides).
- `--check` lists the tools that are installed and how to install the missing ones; `--version` prints the version.

### Notes on this release

- Caches (transcription, translation, slide numbers, screens) remember the size of the recording and, for the
  transcription, the model and language, and are redone when these change. `--force` redoes everything.
- The prompt and the kit for the student notes run Claude Code with `acceptEdits` and an allow-list that includes
  `python3`, `pandoc` and `cp`. Read "Security" in the README before using it on material from other people.

### Known limitations

- The assignment of speech to slides is approximate, most reliable when the slide number is visible in the recording.
- Slides must be `.pptx` or `.pdf`; a PDF export without the deck's hidden slides does not match the numbers shown in
  the video.
- Text in pictures, SmartArt and embedded objects of a PPTX is not read.
- There is no automated test suite yet.
