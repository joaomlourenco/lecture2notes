# lecture2notes

Turn a recorded lecture (and, if you have them, its PowerPoint slides) into a **timestamped transcript** and **reading notes** organised slide by slide, in Markdown and PDF.

```bash
python3 lecture2notes.py slides.pptx lecture.mov          # with slides (PPTX or PDF)
python3 lecture2notes.py lecture.mov                      # without slides
python3 lecture2notes.py ~/Lectures/                      # every recording in a folder, one by one
```

## Why

A recorded lecture is hard to revise from: you can't skim a video or search it. Slides on their own leave out the explanations, examples and answers to questions that happened in the room. lecture2notes puts the two together. Every slide gets what was said while it was on screen, with timestamps, so students can read the lecture, search it, and jump to the right minute of the video when something needs a second look.

## Objectives

- **Transcribe** the lecture locally with Whisper: [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) on Apple Silicon (the default, several times faster) or [OpenAI Whisper](https://github.com/openai/whisper). Nothing is sent to an online service.
- **Assign the speech to slides**, using the slide number shown in the video when it can be read and the words otherwise.
- **Work without slides too**: find the distinct screens in the video, save a picture of each, and read its text with OCR. If the video doesn't help (audio only, or a camera pointed at the lecturer), cut the speech into parts of a few minutes.
- **Handle real course material**: several decks, a lecture split across several recordings, going back to earlier slides, and Portuguese or English speech.
- **Notice language mismatches** between the slides and the speech (for example English slides, Portuguese lecture) and adapt without extra options.
- **Produce files that are easy to use**: Markdown you can edit or give to an LLM, and PDF with a table of contents.
- **Write reading notes for the students** with Claude Code (`--student-notes`): a structured document with explanations, class discussions, figures redrawn from the slide data, review questions and a glossary, as PDF and DOCX.
- **Re-runs are cheap**: the transcription and the video analysis are cached, so you can retune the alignment without transcribing again.

## What you get

For `lecture.mov`, a folder named **`lecture notes`** is created next to it (the `.pdf` files only with LaTeX, see `--latex`):

| File | Contents |
|---|---|
| `lecture.transcript.md` / `.pdf` | Everything that was said, in paragraphs, with timestamps and a marker each time the slide changes. |
| `lecture.notes.md` / `.pdf` | One section per slide: the slide text, the PPTX speaker notes, the 3 key sentences of what was said, and the full excerpt with timestamps. The PDF has a table of contents. |
| `lecture.json` | The raw Whisper output. A later run reuses it (`--force` transcribes again; see "Caches" below). |
| `lecture.slidenumbers.json`, `lecture.scenes.json` | The cached video analysis. |
| `lecture-slides/slide-NNN.png` | With PDF slides: a picture of each slide. Without slides: one picture per distinct screen of the video. Shown in the notes. |

The folder name comes from the recording(s), so several lectures in the same folder never overwrite each other:

| Command | Output folder(s) |
|---|---|
| `lecture2notes.py a.mov` | `a notes/` |
| `lecture2notes.py a.mov b.mov` (one lecture in two parts) | `a+b notes/` |
| `lecture2notes.py --separate a.mov b.mov` | `a notes/` and `b notes/` |
| `lecture2notes.py ~/Lectures/` | one `<name> notes/` per recording in the folder |

## Requirements

### Software

| Tool | Needed for | macOS (Homebrew) | Debian / Ubuntu |
|---|---|---|---|
| Python 3.9+ | running the script (standard library only) | `brew install python` | `sudo apt install python3` |
| ffmpeg (with ffprobe) | extracting audio, looking at the video | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| mlx-whisper (`mlx_whisper`) | transcription, default engine (Apple Silicon only) | `python3 -m venv ~/.venvs/whisper && ~/.venvs/whisper/bin/pip install mlx-whisper` | — (use `--engine openai`) |
| OpenAI Whisper (`whisper` CLI) *(alternative)* | transcription with `--engine openai` | `brew install openai-whisper` or `pipx install openai-whisper` | `pipx install openai-whisper` |
| pandoc *(optional)* | PDF output | `brew install pandoc` | `sudo apt install pandoc` |
| LaTeX: xelatex, lualatex or pdflatex *(optional)* | PDF output | `brew install --cask mactex-no-gui` | `sudo apt install texlive-xetex texlive-fonts-recommended texlive-latex-extra` |
| tesseract *(optional)* | reading slide numbers / text on screen | `brew install tesseract tesseract-lang` | `sudo apt install tesseract-ocr tesseract-ocr-por` |
| Pillow *(optional)* | reading slide numbers (with a PPTX) | `brew install pillow` | `python3 -m pip install pillow` |

Reading a PPTX needs nothing extra: the file is a zip of XML and the script reads it directly. Reading slides given as **PDF** needs poppler (`pdftotext` and `pdfinfo` to read them, `pdftoppm` for the slide pictures in the notes): `brew install poppler` on macOS, `sudo apt install poppler-utils` on Debian/Ubuntu. They all come in the same package.

> **Homebrew Python and Pillow.** Homebrew upgrades `python3` to new versions (3.14 → 3.15 …), and each version has its own packages. After an upgrade, Pillow may be missing for the new `python3`, and the slide numbers in the video are then silently not read. `--check` shows it; `brew install pillow` fixes it.

**PDF files and LaTeX.** The PDFs are made by pandoc through a LaTeX engine. The script looks for xelatex, lualatex or pdflatex (or the one named with `--pdf-engine`) on every run: if one is found, `--latex yes` is the default, and if not, `--latex no`, with a note saying how to install LaTeX. With `--latex no` no PDF is made: you get the Markdown files, and with `--student-notes` the DOCX but no PDF. Give `--latex yes` or `--latex no` to override the choice. (`--no-pdf` still works and means the same as `--latex no` for the transcript and the notes.)

**Check your setup** before the first run. This lists what is installed, what is missing, the install command for your OS, whether the Whisper model is already downloaded, and which OCR languages tesseract has:

```bash
python3 lecture2notes.py --check
```

Each run also checks the tools it needs. If a required one is missing, the run stops and prints how to install it. If an optional one is missing, the run continues and says what it is skipping (no PDF, alignment by words only, and so on).

### Installing what is missing

You do not have to install everything by hand. When a program is needed and missing, the script offers to install it, in a terminal, and only after you answer yes to the exact commands it shows. To get everything in one go, run:

```bash
python3 lecture2notes.py --setup
```

- **Homebrew first.** Programs are installed with [Homebrew](https://brew.sh) (`brew install ffmpeg`, `pandoc`, `poppler`, `tesseract`, `openai-whisper`, and Pillow for Homebrew's Python). If Homebrew itself is missing (macOS or Linux), the script offers to run its official installer, shows the command, and asks first, always, even with `--install yes`. It finds Homebrew even when your shell's `PATH` does not list it.
- **Without Homebrew.** mlx-whisper goes into its own Python environment (`~/.venvs/whisper`, created with `python3 -m venv`; Apple Silicon only, elsewhere use `--engine openai`). Pillow is installed with `pip` (in the current virtual environment, or with `--user`).
- **LaTeX is never installed by the script.** It is large and best chosen by you; see the table above. Without it the PDFs are simply not made (see `--latex`).
- **Only when you can answer.** Without a terminal (a script, a scheduled job, a pipe), or with `--install no`, nothing is installed and you get the install instructions as before. `--install yes` skips the question per package. On Windows the script only prints the instructions.
- **Passwords.** The script never uses `sudo` itself. Homebrew's installer asks for your password when it needs it.

### Hardware

- **Disk**: the Whisper `turbo` model is about 1.6 GB, downloaded on first use to `~/.cache/huggingface/hub` (mlx) or `~/.cache/whisper` (openai). A full MacTeX install is about 5 GB (BasicTeX is much smaller). Temporary audio is about 115 MB per hour of recording and is deleted afterwards.
- **Memory**: about 6 GB of RAM for `turbo` (`--model small` or `base` need much less).
- **Time**: transcription takes the longest. On an Apple Silicon Mac, mlx-whisper uses the GPU and is several times faster than openai-whisper on the CPU (on a test recording: 9 s against 41 s, with identical text). With `--engine openai` on CPU, plan on roughly real time to a few times faster. On a PC with an NVIDIA GPU, use `--engine openai --whisper-args "--fp16 True --device cuda"`. Reading the slides from the video takes a few seconds to a few minutes per hour.
- **Memory for the video analysis**: without slides, the sampled frames are kept in memory at about 14 KB each: roughly 100 MB for 2 hours at 1 frame/s, and five times that with `--video-fps 5`. The video is read frame by frame, so it never takes twice that.

## Usage

```
python3 lecture2notes.py [options] [SLIDES.pptx|SLIDES.pdf ...] RECORDING [RECORDING ...] | FOLDER
python3 lecture2notes.py [options] SLIDES.pptx|SLIDES.pdf ...        # no recording: student notes from the slides
```

Files are told apart by extension: `.pptx` and `.pdf` files are slides, video and audio files are recordings. Both are used **in the order given**. Several decks are joined into one sequence of slides, and several recordings into one lecture (timestamps then show as `[P2 00:12:03]`, meaning minute 12 of part 2).

### Examples

```bash
# a lecture in Portuguese, with slides (going back to earlier slides is handled by default)
python3 lecture2notes.py aula05.pptx aula05.mov --language pt

# a lecture recorded in two parts, using two decks
python3 lecture2notes.py part1.pptx part2.pptx morning.mov afternoon.mov

# no slides: sections come from the screens shown in the video
python3 lecture2notes.py aula05.mov --language pt

# a whole semester: each recording is its own lecture; aulaNN.pptx next to aulaNN.mov is picked up automatically
python3 lecture2notes.py ~/Teaching/2026/recordings/

# Portuguese speech, English slides: detected automatically (see "Different languages" below)
python3 lecture2notes.py slides-en.pptx aula.mov

# the recording failed: student notes from the slides and the reading (see "Without a recording" below)
python3 lecture2notes.py "Lecture 11.pptx" --notes-extra "Readings/chapter 11.pdf"

# retune the alignment without transcribing again (the JSON is reused automatically)
python3 lecture2notes.py slides.pptx lecture.mov --switch-penalty 0.3
```

### Options

| Option | Meaning |
|---|---|
| `--check` | Check the external tools, print install instructions, and exit. Installs nothing. |
| `--setup` | Check the external tools and offer to install the missing ones (not LaTeX), then show the result. See "Installing what is missing". |
| `--install ask\|yes\|no` | What to do when a needed program is missing: `ask` (default) offers to install it, in a terminal; `yes` does not ask for each package (Homebrew itself is still asked); `no` never installs. |
| `--separate` | Each recording is its own lecture with its own folder. On by default when you pass a folder. A `.pptx` (or else `.pdf`) with the same name as a recording is used for it if you gave no slides. |
| `-o, --outdir DIR` | Output folder. With `--separate` or a folder, this is the parent of the per-lecture folders. |
| `--name NAME` | Name used in titles and file names. |
| `--language en\|pt\|…` | Spoken language (default: Whisper detects it). Also picks the tesseract OCR language. |
| `--engine mlx\|openai` | Speech recognition: `mlx` = mlx-whisper, fast on Apple Silicon (default); `openai` = the openai-whisper command line. |
| `--mlx-command PATH` | The `mlx_whisper` program (default: `mlx_whisper` in PATH, else `~/.venvs/whisper/bin/mlx_whisper`). |
| `--model NAME` | Whisper model (default `turbo`). Others: `tiny`, `base`, `small`, `medium`, `large`. With mlx they map to the `mlx-community` models on Hugging Face (e.g. `turbo` → `mlx-community/whisper-large-v3-turbo`); a repository name or a local model folder also works. |
| `--whisper-args "..."` | Extra arguments for the engine's whisper program, for example `"--initial-prompt '...'"`, or `"--device cuda --fp16 True"` with openai. |
| `--cross-language auto\|translate\|off` | English slides with speech in another language: match an English translation of the speech. `auto` (the default) translates only when the slide numbers in the video don't already place the speech. |
| `--translate-model NAME` | Whisper model for that translation (default `medium`, because `turbo` is not trained to translate). |
| `--keep-context` | Let Whisper use the previous window's text as context. Off by default, because in long recordings it can lock into repetition loops. |
| `--silence-threshold S` | Skip silences longer than S seconds where Whisper seems to hallucinate (default 2; 0 turns it off). |
| `--whisper-json FILE` | Use an existing Whisper JSON. Repeat it once per recording. |
| `--force` | Redo the transcription and the video analysis, whatever the caches say (see "Caches"). Needed only when the old results were made with other settings that the caches do not record, such as `--whisper-args`. |
| `--keep-audio` | Keep the extracted `.wav`. |
| `--key-points N` | Key sentences per slide (default 3, 0 for none). |
| `--latex yes\|no` | Make the PDF files (needs pandoc and LaTeX). Default: `yes` if a LaTeX engine is installed, `no` if not. With `no`, only the Markdown is written (and, for the student notes, the DOCX but no PDF). `--check` shows the default. |
| `--no-pdf` | Write only the Markdown files for the transcript and the notes (the same as `--latex no` there). |
| `--pdf-engine`, `--mainfont` | PDF engine (xelatex by default) and main font. |

**With slides:**

| Option | Meaning |
|---|---|
| `--switch-penalty X` | Cost of changing slide when aligning by words (default 0.15; higher gives steadier sections). |
| `--forward-only` | The lecture follows the slides strictly in order: word matching never goes back. By default it may return to earlier slides (`--allow-backward` is still accepted, and is the default). |
| `--video-sync auto\|on\|off` | Read the slide number in the bottom-right corner of the video (default: auto, used if tesseract and Pillow are installed). |
| `--number-box x,y,w,h` | Where the slide number is, as fractions of the frame (default: found automatically by trying bottom right, bottom left and bottom centre). |
| `--video-fps F` | Frames looked at per second (default 1). |
| `--change-threshold X` | Mean grey-level change that counts as a "big" visual change (default 10). |
| `--video-debug` | Write a CSV of what was read from the video, sample by sample. |

**Without slides:**

| Option | Meaning |
|---|---|
| `--sections auto\|video\|time` | Sections from the screens in the video, or parts by time (default: auto, video if it works). |
| `--scene-threshold P` | Percentage of the picture that must change to start a new slide (default 1.5). |
| `--min-slide-seconds S` | Shorter screens (transitions, pop-ups) are merged with the next one (default 4). |
| `--same-slide-threshold P` | Screens that differ in less than this percentage of the picture count as the same slide (default 0.5). |
| `--chunk-minutes M` | Length of the time-based parts (default 5). |

## Student notes with Claude Code

`--student-notes` adds a last step: [Claude Code](https://claude.com/claude-code) (the `claude` command line) writes
**reading notes for the students** from the transcript and the slides, in a `reading-notes/` folder inside the output
folder.

```bash
python3 lecture2notes.py slides.pptx lecture.mp4 --student-notes \
    --notes-context "first-year MSc students; lecturer Prof. X" --notes-course "Course Name 2026-27" \
    --notes-author "MSc in Biology · University X · Prof. X" \
    --notes-extra case-study.pdf
```

The script prepares the folder and then runs Claude Code in it, non-interactively, showing its progress:

| In `reading-notes/` | What it is |
|---|---|
| `PROMPT.md` | The instructions given to Claude, filled in for this lecture |
| `PROMPT-template.md` | The same instructions with placeholders: edit them once, reuse them with `--notes-prompt` |
| `inputs/slides.md` | The text of every slide, the speaker notes (marked as private) and the data behind PPTX charts |
| `inputs/*.txt` | The documents given with `--notes-extra` (PDF, PPTX, Markdown or text): readings, book chapters, case studies, handouts. Claude reads them as further sources, for explanations the lecture only hinted at and for precise references |
| `build/` | The kit that turns the Markdown into PDF and DOCX: a Lua filter for the boxes, the LaTeX look, a Word template, `build.sh` |
| `figures/style.py` | The palette and helpers that keep Claude's figures in one style |
| `<name>.reading-notes.md` / `.pdf` / `.docx` | The notes, written by Claude |
| `figures/` | `make_figures.py` and the figures it draws (PNG for Word, PDF for LaTeX) |

The built-in prompt describes the method: check the transcript's quality and gaps first; read it with parallel
subagents for long lectures; organise the notes by theme with a takeaways box, course organisation, parts and
chapters (each pointing to its place in the recording); report class discussions; redraw the slides' charts and draw
diagrams in one style; add review questions with answers, a glossary and the sources; then check every section
against the transcript again, build, and look at the pages. It also sets the rules: keep what was said, what is on the
slides and added background apart; never present speaker notes as spoken; correct misspoken figures from the slides;
keep participants anonymous; leave out unverified anecdotes; and never reveal answers reserved for a future activity.

**Revising.** If `reading-notes/` already holds notes (for example after you transcribe again with a better engine),
Claude revises them against the new transcript instead of starting over, and writes `CHANGES.md`. `--notes-fresh`
starts from scratch. The kit files are kept between runs, so your own changes to them survive.

**Without a recording.** When the recording of a lecture failed, give the slides alone (and the readings with
`--notes-extra`). There is nothing to transcribe or align, so the script goes straight to the student notes
(`--student-notes` is implied), in `"<deck> notes"/reading-notes/`. Claude gets a variant of the prompt: the slides
and the speaker notes are the backbone, the readings supply the explanations, proofs and examples, and the notes say
plainly that the recording is not available. They report nothing as said in class, and the slides' questions become
"Think about it" boxes. Without readings, Claude relies on the slides and labelled general knowledge, so give the
readings whenever you can.

If the recording turns up later, run the script with the slides and the recording. When the output folder is the
same (recording and deck with the same name, or the same `-o`), Claude reworks the slides-only notes with the
transcript (adding what was said and discussed) and writes `CHANGES.md`.

The notes open with a title block in this shape (the prompt shows it as the model):

```
<A specific title naming the subject of the lecture>
Lecture notes for <lecture or session> · <course name and year>
<Programme> · <Institution> · <Lecturer(s)>
Lecture of <day month year>
```

The title and the subtitle come from the lecture. The author line is what you give with `--notes-author` (for example
`--notes-author "MSc in Biology · University X · Prof. Y"`). Without it, Claude takes the programme, institution and
lecturer from the slides, or from `--notes-context`, and leaves the line out if they are not shown anywhere. Nothing
about a person or a course is built into the script.

**Options:** `--notes-language` (default English), `--notes-context`, `--notes-author` (the author line),
`--notes-course` (PDF running header),
`--notes-extra FILE` (repeatable), `--notes-prompt FILE`, `--notes-model`, `--notes-budget USD` (API accounts),
`--notes-fresh`, `--claude-command`.

**Requirements and cost.** Claude Code installed and logged in (run `claude` once and use `/login`), plus pandoc,
poppler and Python with matplotlib and Pillow for the figures, and xelatex for the PDF (without it, or with `--latex no`,
`build.sh` makes the DOCX only, a `build/no-latex` file records the choice, and Claude is told not to make a PDF). A lecture of a few hours typically takes
20–60 minutes and a substantial amount of usage on your Claude plan or API account. Read the result before sharing it:
the notes are carefully sourced, but they are written from an automatic transcription.

**Fonts.** The PDF uses Charter and Avenir Next when they are installed, and the TeX Gyre fonts otherwise. They are
looked up with `fc-list` (fontconfig) or, on a Mac without fontconfig, in the system and user font folders. A minimal
LaTeX install such as BasicTeX does not include the TeX Gyre fonts, so on a machine with neither pair `build.sh` may
complain about a missing font: install a fuller TeX distribution.

**The running header** of the PDF is the `--notes-course` text, or else the lecture name taken from the recording's
file name. Special LaTeX characters in it (`_ & % # $ { } ~ ^ \`) are escaped for you, so a file called
`Aula_01.mov` is fine.

**Security.** Claude Code runs unattended with `--permission-mode acceptEdits` and an allow-list that includes
`python3`, `pandoc`, `cp` and `mkdir`, which it needs to draw the figures and build the files. Everything it reads
comes from the transcript, the slides, the speaker notes and the `--notes-extra` documents, and any text in those
could try to give it instructions (a prompt injection). For your own lectures and readings that risk is small. For
decks or documents from other people, read them first, or run the script in a throwaway account or a container. To
tighten the allow-list, edit the `tools` list in `student_notes()` (for example, allow only
`python3 figures/make_figures.py` and `sh build/build.sh`).

## How it works

1. **Transcription.** ffmpeg extracts the audio of each recording (16 kHz mono) and Whisper transcribes it into timed segments: mlx-whisper by default, or openai-whisper with `--engine openai`. Both write the same JSON, so a transcript made with one engine is reused by the other. Long recordings are protected against Whisper's *repetition loops* (the same phrase transcribed hundreds of times while the real speech is lost): a window that looks repetitive is decoded again at a higher temperature, the previous text is not used as context, and suspected hallucinations over silences are skipped. mlx-whisper is called through its Python function for this, because its command line never retries. Repeated segments left in a saved transcription are removed when it is loaded, with a warning to transcribe again with `--force` when much was lost.
2. **Slides.** A PPTX is read directly: titles, text, tables, and speaker notes, in slide order. A PDF export is read page by page with `pdftotext` (the first line of a page is its title; footers repeated on every page are dropped; lines wrapped on the slide are rejoined), and each page is also saved as a picture for the notes. A PDF has no speaker notes, so prefer the PPTX when you have both; given the same deck in both formats, the script uses the PPTX.
3. **Matching speech to slides.**
   - *From the video.* First the script finds where the slide number is (bottom right, bottom left or bottom centre) by trying each spot on 30 frames. Then, once per second, that number is read with tesseract; small videos are enlarged more, so 7-pixel digits still read correctly. That shows which slide is on screen at every moment, including when you go back. With several decks, the speech decides between slides that share a number. Short misreadings are removed, and gaps are filled from the neighbouring readings.
   - *From the words.* Speech that the video can't place is matched by TF-IDF cosine similarity between the speech (with its neighbouring segments) and the slide text. A Viterbi path through the slides charges `--switch-penalty` for each change, so sections don't flicker. It may return to earlier slides (use `--forward-only` to forbid that), and it stays between the slides the video identified before and after.
   - *Without slides.* The video is cut where a large part of the picture changes. Short pieces are merged, a piece that only adds to the previous one (a bullet appearing) stays the same slide, and identical screens are the same slide even when you come back to them later. Each distinct screen is saved as a picture, and tesseract reads its text, which becomes that section's text and title. If the video has no distinct screens, the speech is cut at pauses into parts of about `--chunk-minutes` minutes, each titled with its most distinctive words.
4. **Output.** The Markdown is written, then, if `--latex` is `yes` (the default when a LaTeX engine is installed), pandoc and LaTeX turn it into PDF.
5. **Student notes** (optional): Claude Code writes the reading notes, as described above.

## Caches

Slow steps are saved next to the output and reused on the next run. Each cache remembers the **size of the recording**
it was made from, and a run that finds a different size says so and redoes the step (the size, not the date, so that
copying a recording does not matter). The transcription also remembers its model and language:

| File | Reused when | Redone when |
|---|---|---|
| `<recording>.json` (transcription), with its record `<recording>.meta.json` | it exists, or the same recording was transcribed alone before in its own `<recording> notes/` folder | the recording's size differs, or `--model` differs, or `--language` differs from the language it was transcribed as. `--engine` does not matter: both engines write the same JSON. |
| `<recording>.en-<model>.json` (translation) | it exists | the recording's size differs |
| `<recording>.slidenumbers.json` | the frame rate and `--number-box` are the same | the recording's size differs; a "no slide numbers found" result is also redone when the decks have a different number of slides |
| `<recording>.scenes.json` | the frame rate and the three scene options are the same | the recording's size differs |

Files made by an earlier version of the script have no record of this kind and are reused as before. `--force`
redoes everything, whatever the record says. Changing `--switch-penalty`, `--change-threshold`,
`--same-slide-threshold` or the decks never needs it.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "no slide numbers found", or the alignment ignores the video | `python3 lecture2notes.py --check` first: Pillow is often missing after a Homebrew Python upgrade. If it is there, the slides may show no number, or show it somewhere unusual: give `--number-box x,y,w,h`. When black bars are found around the slide, the usual places are tried both inside the slide and in the whole frame, which also covers dark slides that look like bars; `--number-box` always overrides the search. |
| The notes did not change after replacing a recording | Only if the new file has exactly the same size as the old one. Run with `--force`. |
| "N% of the recording was lost in repetition loops" | Whisper got stuck on that recording. Transcribe again with `--force` (the current settings avoid most loops). The lost stretch is missing from the notes until you do. |
| A PDF is not written | No LaTeX engine was found, so `--latex` defaulted to `no` (the run says so at the start; install LaTeX, or give `--latex yes` once it is installed). Or pandoc is missing. The Markdown is always written. |
| `build.sh` fails on a font | See "Fonts" under Student notes. |
| A slide's chart data is missing in the student notes | Only charts in `.pptx` files are read, not pictures of charts or SmartArt. |
| Accents are misread in the OCR text | tesseract lacks the language: `brew install tesseract-lang`. |

## Different languages

The script detects the language of the speech (from the transcript) and of the slides (from their words or, if a deck has too little text, from the language PowerPoint stores on the text). No extra software is needed for this. It then adapts:

| Slides | Speech | What happens |
|---|---|---|
| same language | same language | Normal matching. |
| English | other (e.g. Portuguese) | If the slide numbers in the video place at least 90% of the speech, nothing more is needed. Otherwise Whisper makes an **English translation** of the speech (cached in `<recording>.en-<model>.json`), and the translation, not the original, is matched to the slides. The transcript and notes keep the original speech. |
| other (e.g. Portuguese) | English, or a third language | Whisper can only translate *into* English, so the words can't be matched. The script says so and relies on the slide numbers in the video, so make sure your slides show them. |
| no slides | any | Sections come from the pictures, so language doesn't matter for matching. The OCR language follows the text on screen; if tesseract lacks that language, the script says how to install it. |

The translation is a second Whisper pass, so it takes about as long as the transcription again. It uses the `medium` model, which Whisper downloads on first use (about 1.5 GB). Use `--cross-language translate` to force a translation, or `--cross-language off` to skip it.

## Limits

- **The slide assignment is approximate.** It is most reliable when the slide number is visible in the recording. When only the words are used, it is weakest on slides with repeated or code-only content, or when you talk about several slides together. Every section shows its timestamps so you can check it.
- **Key points are extracted sentences, not a written summary.** For a real summary, give `*.notes.md` to an LLM.
- **Slides in a language other than English, with speech in a different language**, can only be placed by the slide numbers in the video (see "Different languages"). Language detection needs a reasonable amount of text; a deck of only pictures and code may not be recognised.
- **Without slides, results depend on the video.** A screen recording works best. A camera filming a projector, or a lecturer webcam that covers a big part of the frame, can create extra cuts or miss slide changes; adjust `--scene-threshold` and `--min-slide-seconds`. OCR text can contain errors.
- **Slides must be `.pptx` or `.pdf`.** Export Keynote, `.ppt` or `.odp` files to one of them first. A PDF must have one slide per page: handouts with several slides per page are read as one slide per page, and scanned PDFs have no text to read. If a PDF export leaves out the deck's hidden slides, its page numbers will not match the numbers shown in the video.
- **What is read from a PPTX.** Text boxes, titles, tables, speaker notes and (for the student notes) chart data. Text inside pictures, SmartArt and embedded objects is not.
- **One slide per speech segment.** The slide for a segment is the one on screen for most of its duration, so a slide change in the middle of a long segment goes to the slide shown longer.
- **Student notes run with broad permissions.** See "Security" under Student notes.

## For developers

The script is a single file with no dependencies beyond the standard library (Pillow is imported only when the video
is read). Its main parts, in file order:

| Part | Functions | Notes |
|---|---|---|
| External tools | `probe_env`, `require`, `note_missing`, `asr_command`, `report_env`, `setup` | The `INSTALL` table holds the install hint for every tool the script can ask for. A tool that is checked with `require` or `note_missing` must have an entry there. |
| Installing | `offer_install`, `install_commands`, `install_homebrew`, `ensure_brew_on_path` | `require` and `note_missing` call `offer_install` before giving up. `PACKAGE_OF` says which package provides a tool (LaTeX is left out on purpose); `install_commands` gives the commands. Nothing runs without a terminal and a "yes". |
| Slides | `read_pptx`, `read_pdf`, `render_pdf_pages`, `read_decks` | Each slide is a dict (`n`, `k`, `deck`, `title`, `items`, `notes`, `shown`, `kind`, and `part` for a PPTX). |
| Text matching | `tokens`, `build_scores`, `align`, `align_pinned`, `detect_language` | Pure functions. `STOP` is stored without accents, because `_token` removes the accents of a word before looking it up. |
| Video | `read_slide_numbers`, `clean_numbers`, `to_global`, `detect_scenes`, `visual_slides` | The slide number is read by tesseract in a corner of the frame. |
| Output | `paragraphs`, `write_transcript`, `write_notes`, `to_pdf` | The Markdown is escaped with `md_escape`; text for LaTeX with `tex_escape`. |
| Orchestration | `run_lecture`, `align_with_slides`, `cross_language`, `plan_lectures`, `resolve_latex`, `main` | `REPORT` collects what the final summary prints. |
| Student notes | `student_notes`, `fill_template`, `STUDENT_NOTES_PROMPT*`, `KIT_*` | The prompts and the build kit are string constants written to `reading-notes/`. `fill_template` fills the `$name` placeholders in one pass, so a lecture name containing `$extras` is not expanded. |
| Caches | `source_fingerprint`, `stale_reason`, `write_meta` | A cache is reused only if the recording it was made from (its size) and the model and language still match. |

`align`, `align_pinned`, `clean_numbers`, `to_global`, `drop_repetitions`, `detect_language`, `key_sentences`,
`lecture_label`, `tex_escape`, `fill_template` and `stale_reason` take and return plain data, so they are easy to test without any recording. There is
no test suite yet; those are the place to start. Useful checks after a change:

```bash
python3 -B -c "import ast; ast.parse(open('lecture2notes.py').read())"   # it parses
python3 lecture2notes.py --check                                          # the tools are found
python3 lecture2notes.py --help                                           # argparse is happy with the help texts
```

## License

lecture2notes is released under the [MIT License](LICENSE): copyright (c) 2026 João Lourenço. The license covers the
code of this repository. The recordings, slides, transcripts and notes that you make with it belong to you and to the
lecturers and speakers concerned. The programs it runs (ffmpeg, Whisper, tesseract, pandoc, poppler, LaTeX) are
separate software with their own licenses.

## Credits

- Transcription: [OpenAI Whisper](https://github.com/openai/whisper) (MIT License) and [mlx-whisper](https://github.com/ml-explore/mlx-examples) by Apple's MLX team (MIT License).
- Audio and video: [FFmpeg](https://ffmpeg.org/).
- OCR: [Tesseract](https://github.com/tesseract-ocr/tesseract) (Apache 2.0) and [Pillow](https://python-pillow.org/).
- PDF: [pandoc](https://pandoc.org/) and TeX Live / MacTeX.
- Version 0.1.2 (2026-11-13); see [RELEASE_NOTES.md](RELEASE_NOTES.md). `python3 lecture2notes.py --version` prints it.
- Author: João M. Lourenço (NOVA School of Science and Technology, NOVA University Lisbon), with help from Claude (Anthropic).
