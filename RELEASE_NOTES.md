# Release notes

## Unreleased

- **Separate student notes for each lecture, even when they share one video**, made easier: `--notes-parts auto` lets the AI find
  the lectures (however many, one if there is one), and `--notes-part-names "Lecture 1: Sorting (12 Nov); Lecture 2: Searching
  (13 Nov)"` names them (a date in a name becomes that lecture's date; names alone imply as many parts, cuts found by the AI).
  Each part gets its own title and date. At the end the script lists the part files with their titles. `--notes-parts N` and
  times work as before.

- **`--language-switch`: recordings whose speech changes language.** Whisper uses one language per file, so a lecture that
  goes from Portuguese to English came out wrong in one of them. `--language-switch "pt 04:30 en"` (times `m:ss` or `h:mm:ss`)
  transcribes each part in its own language; `--language-switch auto` finds the changes itself (Whisper's language detector on a
  window every minute, refined to about 5 s, cut at the nearest pause). The parts are merged into one transcript with continuous
  timestamps, the plan is recorded in the cache, and the translation for English slides is made part by part. Tried on
  synthetic speech only (found the change at the right pause); check the plan on a real lecture.

- **One notes folder per AI**: the student notes of each AI go in `reading-notes-claude`, `reading-notes-agy` or
  `reading-notes-copilot`, so two AIs never overwrite each other and each revises only its own earlier notes. An existing
  folder called just `reading-notes` is reused by Claude Code (unless `reading-notes-claude` exists).
- The AI now works only in its own folder: the transcript is copied to `inputs/transcript.md`, and, for a lecture without
  slide files, the text read from the screens to `inputs/screens.md` (before, Claude Code found that text on its own in the
  output folder, which other AIs would not have). The prompt tells the AI to leave other folders alone.

- **The student notes can be written by other command-line AIs, not only Claude Code**: Google's Antigravity CLI (`agy`)
  and GitHub Copilot CLI, besides `claude`. The script looks for them; with one installed it uses it, with several it asks
  you in a terminal (`--notes-ai NAME` skips the question; without a terminal it takes Claude Code if present). New options:
  `--notes-ai auto|claude|agy|copilot|none`, `--notes-ai-command`, `--notes-ai-args`. `--notes-ai none` only prepares the
  folder and `PROMPT.md`. `--check` lists the AIs found. agy and Copilot were run on a trivial prompt and the whole flow
  was tested with a fake agy; a full lecture has been run only with Claude Code. agy's command line has no allow-list of
  commands (see Security in the README).

- **One set of student notes per part of a lecture**: `--notes-parts N` (Claude finds N parts in the transcript) or
  `--notes-parts 02:27` / `01:00,02:30` (the times where the later parts begin). Claude writes
  `<name>.partK.reading-notes.md`, one complete, standalone document for each part, with figures named by part. The build
  script (`build-kit 2`) now builds every `*.reading-notes.md` of the folder; an older `build.sh` is replaced. A one-document
  version is used as the starting point and, when the parts are done, moved to `reading-notes/superseded/`.
- **Fixed: a long author line ran into the margin of the PDF** of the student notes. LaTeX sets the author in a one-line
  box; the kit's `boxes.lua` filter now puts it in a centred paragraph box as wide as the text. An older `boxes.lua` in
  an existing `reading-notes/build/` is replaced when the script next prepares that folder (the old one is kept as
  `boxes.lua.old`); to fix a folder at once, copy the new file and run `sh build/build.sh`.
- **Fixed: `--student-notes` stopped when pandoc was missing**; it now writes the prompt and the kit and says what is missing.
- **`--notes-only`**: write only the student notes, from the transcript of an earlier run, without transcribing, matching or
  rewriting anything else.

- **The speech engine is chosen for you** (`--engine auto`, now the default): on a Mac, MacWhisper if the app and its
  `mw` command-line tool are installed, else mlx-whisper on Apple Silicon, else openai-whisper; on Linux and Windows,
  openai-whisper. `--engine macwhisper|mlx|openai` overrides. Before, the default was mlx everywhere, which failed
  on Linux and Windows.
- MacWhisper support: uses `mw transcribe` (JSON with Pro, else the free SRT), the model selected in the app or
  `--model provider:name`. It cannot translate, so the translation to English falls back to mlx or openai-whisper.
  Written from MacWhisper's documentation; not tried against the real app.
- Windows and Linux: console output no longer fails on characters the console cannot show; Tesseract and pandoc are
  found in their usual Windows install folders; the messages say `py` on Windows; `--setup` installs openai-whisper
  with pipx or pip when Homebrew is not there. The deprecated `os.path.commonprefix` is no longer used.
- A test suite (26 tests, standard library only) and a GitHub Actions workflow that runs it on macOS, Linux and Windows.

## v0.2 — 2026-11-13

- The script now offers to install what is missing, instead of only printing instructions: when a needed program is
  missing, in a terminal, it shows the commands and asks. Homebrew is used whenever possible, and if Homebrew is
  missing the script offers to run its official installer (always asked). mlx-whisper goes into a virtual environment,
  Pillow is installed with pip. LaTeX is never installed.
- New options: `--setup` (check, then offer to install everything missing) and `--install ask|yes|no`.
- Homebrew's folder is added to `PATH` for the run when it is not there, so tools installed with it are found.
- Nothing is installed without a terminal, with `--install no`, or on Windows.

## v0.1.2 — 2026-11-13

- The prompt for the student notes no longer contains a built-in author, course, date or title. The example title block
  uses placeholders, and the new option `--notes-author` sets the author line (without it, Claude takes the programme,
  institution and lecturer from the slides or `--notes-context`). Course-specific examples were removed from the
  README and the help texts.

## v0.1.1 — 2026-11-13

- The project is now released under the [MIT License](LICENSE) (copyright (c) 2026 João Lourenço). v0.1 was published
  without a license file; use v0.1.1 or later.
- Added `SPDX-License-Identifier: MIT` to the script header, `__license__` in the code, and a License section in the
  README.
- No change in behaviour.

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
