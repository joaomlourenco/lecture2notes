#!/usr/bin/env python3
# lecture2notes
# Copyright (c) 2026 João Lourenço <joao.lourenco@fct.unl.pt>
# SPDX-License-Identifier: MIT (see the LICENSE file)
# Version: 0.2
# Date:    2026-11-13
"""lecture2notes: classroom recording (MOV) [+ slides (PPTX or PDF)] -> transcript and reading notes (Markdown + PDF).

    python3 lecture2notes.py SLIDES.pptx RECORDING.mov [-o OUTDIR] [--language en] [--model turbo]
    python3 lecture2notes.py RECORDING.mov                                     # no slides: sections from the video
    python3 lecture2notes.py part1.pptx part2.pptx  lecture.mov                # several decks, one recording
    python3 lecture2notes.py slides.pptx  lecture-a.mov lecture-b.mov          # one deck, several recordings (one lecture)
    python3 lecture2notes.py --separate a.mov b.mov c.mov                      # three lectures, one output folder each
    python3 lecture2notes.py ~/Lectures/                                       # every recording in the folder, separately
    python3 lecture2notes.py slides.pptx --notes-extra chapter.pdf             # recording failed: notes from the slides
    python3 lecture2notes.py --check                                           # check the external tools and exit

The files are told apart by extension (.pptx or .pdf = slides, video/audio extensions = recordings) and are used in the order
given: the decks are joined into one sequence of slides and the recordings into one lecture; the slides are numbered
continuously, and timestamps are shown as [P2 00:12:03] (minutes into the 2nd recording) when there are several.
With --separate (or when a folder is given) every recording is a lecture of its own; if no .pptx is given, a .pptx with
the same name as the recording, next to it, is used for it.

Outputs, in OUTDIR (default: "<recording> notes" next to the recording, or "<rec1>+<rec2> notes" for several, so that
every set of recordings gets its own folder; with --separate, -o is the parent of the per-lecture folders):
    <name>.transcript.md / .pdf   what was said, with timestamps and slide markers
    <name>.notes.md / .pdf        reading notes, one section per slide: the slide text, the speaker notes of the
                                  PPTX, the key sentences of what was said about it, and the full excerpt
    <recording>.json              raw output of whisper, one per recording (reused on the next run; --force redoes)
    <name>-slides/                (without PPTX) one image per distinct screen of the video, shown in the notes

How it works
    1. ffmpeg extracts the audio of each recording and whisper transcribes it (segments with times): mlx-whisper by
       default (fast on Apple Silicon), or the openai-whisper command line with --engine openai.
    2. The PPTX files are read directly (a zip of XML files; nothing to install): text, titles, speaker notes.
    3. The recording itself is looked at once per second: the slide number shown on the slide is read (tesseract OCR;
       its place, bottom right, left or centre, is found automatically on sample frames, or give --number-box). That tells which
       slide is on screen at every moment, also when the lecture goes back to earlier slides, and with several decks
       which deck it is (the words of the speech decide between equal numbers). Slides without a number, or numbers
       that cannot be read, are filled in from their neighbours (same number before and after, no big visual change
       in between) or, otherwise, by the words, only among the slides between the known ones before and after.
       Without tesseract/Pillow, or with --video-sync off, the words alone are used (next step).
       The words: each transcript segment is assigned to a slide by comparing its words with the words of the slides
       (TF-IDF cosine similarity, a window of neighbouring segments for robustness) and finding the best path
       through the slides that never goes back (Viterbi). Switching slide costs --switch-penalty, so the
       assignment does not flicker. The path may return to earlier slides (lectures often do); use --forward-only
       for a lecture that strictly follows the slides in order.
       Languages: the language of the slides (their words, or else the language set in the PPTX) and of the speech
       are detected. If the slides are in English and the speech is not, and the video does not already place the
       speech, an English translation of the speech (whisper --task translate, --translate-model) is matched to the
       slides instead; the transcript and the notes keep the original speech. Whisper only translates into English,
       so slides in another language than an English speech can only be placed by the slide numbers of the video.
       Without PPTX: the video is cut where much of the picture changes (--scene-threshold), short pieces are merged
       (--min-slide-seconds), a piece that only adds to the previous one (a build) is the same slide, and pieces
       that look the same (--same-slide-threshold) are the same slide, also when the lecture comes back to it. Each distinct screen is saved as an image and its text is read by tesseract.
       Without a usable video (audio only, or a camera that never shows distinct screens) the speech is cut into
       parts of about --chunk-minutes minutes, at pauses, titled with their most distinctive words.
    4. pandoc turns the Markdown files into PDF.
    5. With --student-notes, a command-line AI (Claude Code, Antigravity agy or GitHub Copilot) writes reading notes for the students from the
       transcript and the slides, following a built-in prompt (written to reading-notes/PROMPT.md): content checked
       against the recording, boxes, figures redrawn from the slide data, review questions and glossary, built as PDF
       and DOCX with a kit written next to them. An existing version is revised against the new transcription.

Needs: python 3.9+, ffmpeg (with ffprobe), mlx-whisper (or openai-whisper with --engine openai), pandoc and a LaTeX engine for the PDF;
for the slide numbers of the video also tesseract and Pillow (pip install pillow); without PPTX, tesseract to read the
text on the screen. Run with --check to see what is missing and how to install it. The readings are saved in
<recording>.slidenumbers.json / .scenes.json (use --force to redo them; --video-debug writes a sample-by-sample .csv).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import math
import operator
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

__version__ = "0.2"  # keep in step with the header above, README.md and RELEASE_NOTES.md
__date__ = "2026-11-13"
__author__ = "João Lourenço <joao.lourenco@fct.unl.pt>"
__copyright__ = "Copyright (c) 2026 João Lourenço"
__license__ = "MIT"

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
A = "{%s}" % NS["a"]
P = "{%s}" % NS["p"]
R = "{%s}" % NS["r"]

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts", ".3gp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aif", ".aiff"}
OTHER_SLIDES = {".ppt": "open it in PowerPoint/LibreOffice and save it as .pptx",
                ".key": "export it from Keynote as PowerPoint (.pptx) or PDF",
                ".odp": "save it from LibreOffice Impress as .pptx (or export it as PDF)"}
SLIDE_EXT = {".pptx", ".pdf"}


# ----------------------------------------------------------------------------------------------- external tools
ENV: dict = {}  # tool -> path (or version for pillow), filled by probe_env()
PY = "py" if sys.platform == "win32" else "python3"  # how the user starts Python, for the messages

INSTALL = {  # tool -> (what it is for, {platform: how to install})
    "ffmpeg": ("extract the audio and look at the video",
               {"darwin": "brew install ffmpeg", "linux": "sudo apt install ffmpeg      (Fedora: sudo dnf install ffmpeg)",
                "win32": "winget install Gyan.FFmpeg"}),
    "ffprobe": ("read the size and duration of the video (part of ffmpeg)",
                {"darwin": "brew install ffmpeg", "linux": "sudo apt install ffmpeg", "win32": "winget install Gyan.FFmpeg"}),
    "whisper": ("transcribe the speech (the openai-whisper command line)",
                {"darwin": "brew install openai-whisper      (or: pipx install openai-whisper)",
                 "linux": "pipx install openai-whisper      (or: python3 -m pip install -U openai-whisper)",
                 "win32": "pip install -U openai-whisper"}),
    "mlx_whisper": ("transcribe the speech on Apple Silicon (mlx-whisper, the default engine on a Mac without MacWhisper)",
                    {"darwin": "python3 -m venv ~/.venvs/whisper && ~/.venvs/whisper/bin/pip install mlx-whisper      "
                               "(Apple Silicon only; elsewhere use --engine openai)",
                     "*": "mlx-whisper needs an Apple Silicon Mac: use --engine openai"}),
    "macwhisper": ("transcribe the speech with the MacWhisper app (commercial), through its command-line tool `mw`",
                   {"darwin": "install MacWhisper, then in it: Settings > Advanced > Command-Line Tool > Install "
                              "(https://docs.macwhisper.com/article/57-macwhisper-command-line-tool)",
                    "*": "MacWhisper exists only for the Mac: use --engine openai"}),
    "pdftotext": ("read slides given as PDF (part of poppler; pdftoppm, also in poppler, makes the slide pictures)",
                  {"darwin": "brew install poppler", "linux": "sudo apt install poppler-utils", "win32": "winget install oschwartz10612.Poppler"}),
    "pdfinfo": ("count the pages of slides given as PDF (part of poppler)",
                {"darwin": "brew install poppler", "linux": "sudo apt install poppler-utils", "win32": "winget install oschwartz10612.Poppler"}),
    "pdftoppm": ("make the pictures of the PDF slides shown in the notes (part of poppler)",
                 {"darwin": "brew install poppler", "linux": "sudo apt install poppler-utils", "win32": "winget install oschwartz10612.Poppler"}),
    "pandoc": ("turn the Markdown into PDF",
               {"darwin": "brew install pandoc", "linux": "sudo apt install pandoc", "win32": "winget install JohnMacFarlane.Pandoc"}),
    "latex": ("PDF engine for pandoc (xelatex, lualatex or pdflatex)",
              {"darwin": "brew install --cask mactex-no-gui      (smaller, may need extra packages: brew install --cask basictex)",
               "linux": "sudo apt install texlive-xetex texlive-fonts-recommended texlive-latex-extra",
               "win32": "winget install MiKTeX.MiKTeX"}),
    "tesseract": ("read the slide numbers and the text on the screen (OCR)",
                  {"darwin": "brew install tesseract      (more languages, e.g. Portuguese: brew install tesseract-lang)",
                   "linux": "sudo apt install tesseract-ocr tesseract-ocr-por",
                   "win32": "winget install UB-Mannheim.TesseractOCR"}),
    "pillow": ("Python imaging library, to read the slide numbers of the video"
               + (f" (missing in the Python running this script, the venv {sys.prefix}; Pillow installed by Homebrew "
                  "is not visible there)" if sys.prefix != sys.base_prefix else ""),
               {"darwin": (f"brew install pillow      (for this Python, {sys.executable}; or: "
                           f"{Path(sys.executable).name} -m pip install --user --break-system-packages pillow)")
                if sys.executable.startswith(("/opt/homebrew", "/usr/local")) and sys.prefix == sys.base_prefix
                else f"{sys.executable} -m pip install pillow",
                "*": f"{Path(sys.executable).name} -m pip install pillow"}),
}
TESS_LANG = {"en": "eng", "pt": "por", "es": "spa", "fr": "fra", "de": "deu", "it": "ita", "nl": "nld", "ca": "cat"}
WHISPER_FILES = {"turbo": "large-v3-turbo.pt", "large": "large-v3.pt", "large-v3-turbo": "large-v3-turbo.pt"}
MLX_REPOS = {  # openai-whisper model names -> mlx-community repositories on Hugging Face
    "tiny": "whisper-tiny-mlx", "base": "whisper-base-mlx", "small": "whisper-small-mlx", "medium": "whisper-medium-mlx",
    "large": "whisper-large-v3-mlx", "large-v3": "whisper-large-v3-mlx", "large-v2": "whisper-large-v2-mlx",
    "turbo": "whisper-large-v3-turbo", "large-v3-turbo": "whisper-large-v3-turbo",
}
MLX_VENV_BIN = Path.home() / ".venvs" / "whisper" / "bin" / "mlx_whisper"  # where mlx-whisper.sh installs it


def find_tool(name: str) -> str | None:
    """shutil.which and, on Windows, the usual install folders: the installers of tesseract and pandoc do not always put
    their folder on PATH."""
    found = shutil.which(name)
    if found or sys.platform != "win32":
        return found
    folders = {"tesseract": ("Tesseract-OCR", "Programs/Tesseract-OCR"), "pandoc": ("Pandoc", "Programs/Pandoc")}
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        for sub in folders.get(name, ()):
            cand = Path(os.environ.get(var) or "/nonexistent") / sub / f"{name}.exe"
            if cand.is_file():
                return str(cand)
    return None


def macwhisper_path() -> str | None:
    """MacWhisper's command-line tool `mw`, when the app and the tool are both there (the app's folder is checked too: `mw`
    is a common name). It is not run here: running it starts the app."""
    if sys.platform != "darwin":
        return None
    mw = shutil.which("mw") or ("/usr/local/bin/mw" if Path("/usr/local/bin/mw").is_file() else None)
    app = any((Path(d) / "MacWhisper.app").is_dir() for d in ("/Applications", str(Path.home() / "Applications")))
    return mw if mw and app else None


def probe_env() -> dict:
    env = {t: find_tool(t) for t in ("ffmpeg", "ffprobe", "whisper", "pandoc", "tesseract")}
    env["macwhisper"] = macwhisper_path()
    env["pdftotext"], env["pdftoppm"], env["pdfinfo"] = shutil.which("pdftotext"), shutil.which("pdftoppm"), shutil.which("pdfinfo")
    env["mlx_whisper"] = shutil.which("mlx_whisper") or (str(MLX_VENV_BIN) if MLX_VENV_BIN.is_file() else None)
    env["latex"] = next((shutil.which(e) for e in ("xelatex", "lualatex", "pdflatex") if shutil.which(e)), None)
    try:
        import PIL

        env["pillow"] = PIL.__version__
    except ImportError:
        env["pillow"] = None
    return env


def install_hint(tool: str) -> str:
    how = INSTALL[tool][1]
    plat = "linux" if sys.platform.startswith("linux") else sys.platform
    return how.get(plat) or how.get("*") or how.get("linux", "")


def missing_message(tools: list[str], why: str) -> str:
    lines = [f"{why}, these are missing:"]
    for t in tools:
        lines.append(f"  - {t}: {INSTALL[t][0]}\n      install: {install_hint(t)}")
    return "\n".join(lines)


def require(tools: list[str], why: str) -> None:
    missing = [t for t in tools if not ENV.get(t)]
    if missing and offer_install(missing, why):
        missing = [t for t in tools if not ENV.get(t)]
    if missing:
        sys.exit("error: " + missing_message(missing, why))


_noted: set[str] = set()


def note_missing(tools: list[str], why: str) -> bool:
    """True if all `tools` are there; otherwise offer to install them or, failing that, print (once) what is missing
    and how to install it."""
    missing = [t for t in tools if not ENV.get(t)]
    if missing and why not in _noted:
        _noted.add(why)
        if offer_install(missing, why):
            missing = [t for t in tools if not ENV.get(t)]
        if missing:
            print("note: " + missing_message(missing, why))
    return not missing


# ----------------------------------------------------------------------------------------------- installing what is missing
# When a tool is needed and missing, the script offers to install it: with Homebrew whenever possible (and offers to
# install Homebrew itself first), otherwise with pip in a virtual environment. It only does so in a terminal, only
# after a "yes" for the commands it shows, never for LaTeX (big, and best chosen by the user) and never with sudo of its
# own (the installers ask for the password themselves if they need it).
INSTALL_MODE = "ask"  # --install: ask (the default), yes (no question per package), no
_declined: set[str] = set()  # tools the user did not want installed in this run: not asked about again
HOMEBREW_URL = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
BREW_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin")  # where Homebrew lives
PACKAGE_OF = {  # tool -> the package that provides it. LaTeX is left out on purpose.
    "ffmpeg": "ffmpeg", "ffprobe": "ffmpeg", "pdftotext": "poppler", "pdfinfo": "poppler", "pdftoppm": "poppler",
    "pandoc": "pandoc", "tesseract": "tesseract", "pillow": "pillow", "whisper": "openai-whisper", "mlx_whisper": "mlx-whisper"}
PACKAGE_NOTE = {
    "openai-whisper": "also installs PyTorch, which is large",
    "mlx-whisper": "in its own Python environment, ~/.venvs/whisper; the speech model (about 1.6 GB) is downloaded later, on first use",
    "tesseract": "with tesseract-lang, the data of all the OCR languages",
}


def ensure_brew_on_path() -> None:
    """Homebrew's folder may be missing from PATH (a shell that never ran `brew shellenv`): add it at the end, so that the
    tools it installed are found and nothing found before changes."""
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in BREW_DIRS:
        if d not in dirs and (Path(d) / "brew").exists():
            os.environ["PATH"] = os.pathsep.join(dirs + [d])
            dirs.append(d)


def install_commands(pkg: str, brew: str) -> list[list[str]] | None:
    """The commands that install a package, or None if this script cannot do it on this machine."""
    if pkg == "openai-whisper" and sys.platform != "darwin" and not shutil.which("brew"):  # Linux: pipx or pip, if there
        if shutil.which("pipx"):
            return [["pipx", "install", "openai-whisper"]]
        if sys.prefix != sys.base_prefix:
            return [[sys.executable, "-m", "pip", "install", "openai-whisper"]]
    if pkg in ("ffmpeg", "poppler", "pandoc", "openai-whisper"):
        return [[brew, "install", pkg]]
    if pkg == "tesseract":
        return [[brew, "install", "tesseract", "tesseract-lang"]]
    if pkg == "pillow":
        if sys.prefix != sys.base_prefix:  # in a virtual environment: pip there
            return [[sys.executable, "-m", "pip", "install", "pillow"]]
        if sys.executable.startswith(("/opt/homebrew", "/usr/local")):  # Homebrew's Python: Homebrew's Pillow
            return [[brew, "install", "pillow"]]
        return [[sys.executable, "-m", "pip", "install", "--user", "pillow"]]
    if pkg == "mlx-whisper":
        if sys.platform == "darwin" and platform.machine() == "arm64":
            venv = MLX_VENV_BIN.parent.parent
            return [[sys.executable, "-m", "venv", str(venv)], [str(venv / "bin" / "pip"), "install", "mlx-whisper"]]
        return None  # mlx needs Apple Silicon: --engine openai
    return None


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        print()
        return False


def show_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def install_homebrew() -> str | None:
    """Run Homebrew's official installer (it asks for the password and for a keypress itself). Returns brew's path."""
    print("\nHomebrew is the package manager used to install the missing programs, and it is not installed.")
    print("Its official installer would be run, with this command:\n")
    print(f'  /bin/bash -c "$(curl -fsSL {HOMEBREW_URL})"\n')
    print("It downloads the installer from Homebrew's own repository on GitHub, asks for your password, and takes a few minutes.")
    if not confirm("Install Homebrew now?"):  # always asked, also with --install yes
        return None
    subprocess.run(["/bin/bash", "-c", f'/bin/bash -c "$(curl -fsSL {HOMEBREW_URL})"'])
    ensure_brew_on_path()
    brew = shutil.which("brew")
    if brew:
        print(f"\nHomebrew is installed ({brew}). The installer printed how to add it to the PATH of your shell: do it (or open a "
              "new terminal) so that other programs find what it installs; this script finds it by itself.")
    else:
        print("note: Homebrew was not installed")
    return brew


def offer_install(missing: list[str], why: str) -> bool:
    """Offer to install the missing tools (not LaTeX), and do it if the user agrees. Returns True if they are all there
    afterwards. Does nothing, and returns False, without a terminal, with --install no, or on Windows."""
    if INSTALL_MODE == "no" or sys.platform == "win32" or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    pkgs: list[str] = []
    for t in missing:
        p = PACKAGE_OF.get(t)
        if p and t not in _declined and p not in pkgs:
            pkgs.append(p)
    if not pkgs:
        return False
    brew = shutil.which("brew")
    plans = {p: install_commands(p, brew or "brew") for p in pkgs}
    doable = {p: c for p, c in plans.items() if c}
    msg = missing_message(missing, why)
    print("\n" + msg[0].upper() + msg[1:])
    for p in pkgs:
        if p not in doable:
            print(f"  - {p}: cannot be installed by this script on this machine; see the install line above")
    if not doable:
        _declined.update(missing)
        return False
    need_brew = any(Path(c[0]).name == "brew" for cmds in doable.values() for c in cmds)
    print("\nThese can be installed for you:")
    for p, cmds in doable.items():
        print(f"  {p}" + (f"  ({PACKAGE_NOTE[p]})" if p in PACKAGE_NOTE else ""))
        for c in cmds:
            print(f"      {show_cmd(c)}")
    if need_brew and not brew:
        print("  (these need Homebrew, which is not installed; I would offer to install it first)")
    if INSTALL_MODE != "yes" and not confirm("Install them now?"):
        _declined.update(missing)
        return False
    if need_brew and not brew:
        brew = install_homebrew()
        if brew:
            doable = {p: install_commands(p, brew) for p in doable}
        else:  # no Homebrew: what does not need it is still installed
            doable = {p: c for p, c in doable.items() if c and not any(Path(x[0]).name == "brew" for x in c)}
    for p, cmds in doable.items():
        print(f"\ninstalling {p} ...")
        for c in cmds:
            print("$ " + show_cmd(c), flush=True)
            if subprocess.run(c).returncode != 0:
                print(f"warning: the installation of {p} failed (see above); install it by hand, as described in the install line")
                break
    import importlib

    importlib.invalidate_caches()
    try:  # a pip --user install may have created the user site folder just now
        import site

        site.addsitedir(site.getusersitepackages())
    except Exception:
        pass
    ENV.update(probe_env())
    _declined.update(t for t in missing if not ENV.get(t))
    return all(ENV.get(t) for t in missing if t in PACKAGE_OF)


def tool(name: str) -> str:
    require([name], f"to go on, `{name}` is needed")
    return ENV[name]


def asr_tool(args) -> str:
    """The speech recognition program of the chosen engine (a key of ENV and INSTALL)."""
    return {"macwhisper": "macwhisper", "mlx": "mlx_whisper"}.get(args.engine, "whisper")


def resolve_engine(args) -> None:
    """--engine auto (the default): on a Mac, MacWhisper if it is installed with its command-line tool, else mlx-whisper
    on Apple Silicon, else openai-whisper; on Linux and Windows, openai-whisper."""
    if args.engine != "auto":
        return
    if sys.platform == "darwin" and ENV.get("macwhisper"):
        args.engine = "macwhisper"
    elif sys.platform == "darwin" and platform.machine() == "arm64":
        args.engine = "mlx"
        if any((Path(d) / "MacWhisper.app").is_dir() for d in ("/Applications", str(Path.home() / "Applications"))):
            print("note: MacWhisper is installed but its command-line tool is not, so mlx-whisper is used. To use MacWhisper: "
                  + install_hint("macwhisper"))
    else:
        args.engine = "openai"


def model_key(args) -> str | None:
    """The model as recorded in the caches. MacWhisper uses the model selected in the app unless --model gives an id
    like provider:name; then there is nothing to compare."""
    if args.engine == "macwhisper":
        return args.model if ":" in args.model else None
    return args.model


def asr_model(args, model: str) -> str:
    """The model name as the engine wants it: mlx-whisper takes a Hugging Face repository or a local folder."""
    if args.engine != "mlx" or "/" in model or Path(model).expanduser().is_dir():
        return model
    return "mlx-community/" + MLX_REPOS.get(model, f"whisper-{model}-mlx")


def model_cached(args, model: str) -> tuple[bool, Path]:
    """(is the model already downloaded, where it goes)."""
    m = asr_model(args, model)
    if args.engine == "macwhisper":  # the models are managed in the app
        return True, Path(".")
    if args.engine == "mlx":
        if Path(m).expanduser().is_dir():
            return True, Path(m).expanduser()
        hub = Path.home() / ".cache" / "huggingface" / "hub"
        return (hub / ("models--" + m.replace("/", "--"))).is_dir(), hub
    cache = Path.home() / ".cache" / "whisper" / WHISPER_FILES.get(m, f"{m}.pt")
    return cache.exists(), cache.parent


# mlx-whisper is run through its Python function, not its command line: the command line decodes at a single
# temperature, so it never retries a window that falls into a repetition loop ("no, no, no, ..." for minutes).
MLX_DRIVER = r"""
import json, sys
import mlx_whisper
a = json.loads(sys.argv[1])
res = mlx_whisper.transcribe(a["audio"], **a["opts"])
with open(a["out"], "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, default=float)
"""
FALLBACK_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def _venv_python(script: str) -> str:
    """The Python interpreter of a console script (from its #! line), e.g. the one of the mlx-whisper venv."""
    try:
        first = Path(script).read_text(errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return sys.executable
    exe = first[2:].strip().split()[0] if first.startswith("#!") else ""
    return exe if exe and not exe.endswith("/env") and Path(exe).exists() else sys.executable


def _cli_opts(text: str | None) -> dict:
    """--whisper-args "--initial-prompt 'x' --no-speech-threshold 0.5" -> {"initial_prompt": "x", ...} (for mlx)."""
    import ast

    out, words = {}, shlex.split(text or "")
    i = 0
    while i < len(words):
        key = words[i].lstrip("-").replace("-", "_")
        has_value = i + 1 < len(words) and not words[i + 1].startswith("--")
        val = words[i + 1] if has_value else "True"  # a flag alone means True
        i += 2 if has_value else 1
        try:
            out[key] = ast.literal_eval(val)
        except (ValueError, SyntaxError):
            out[key] = val
    return out


def asr_command(args, wav: Path, outdir: Path, model: str, language: str | None, task: str | None = None) -> list[str]:
    """Command line that transcribes (or translates) `wav` into outdir/<wav stem>.json with the chosen engine.
    Both engines get the settings that keep long recordings out of repetition loops: retry at higher temperatures
    when a window looks repetitive, do not feed the previous text as context (unless --keep-context), and skip
    silences where a hallucination is suspected (--silence-threshold)."""
    exe = tool(asr_tool(args))
    silence = args.silence_threshold if args.silence_threshold and args.silence_threshold > 0 else None
    if args.engine == "mlx":
        opts = {"path_or_hf_repo": asr_model(args, model), "verbose": False, "temperature": FALLBACK_TEMPERATURES,
                "condition_on_previous_text": args.keep_context}
        if silence:
            opts.update(word_timestamps=True, hallucination_silence_threshold=silence)
        if task:
            opts["task"] = task
        if language:
            opts["language"] = language
        opts.update(_cli_opts(args.whisper_args))
        payload = json.dumps({"audio": str(wav), "out": str(outdir / f"{wav.stem}.json"), "opts": opts})
        return [_venv_python(exe), "-c", MLX_DRIVER, payload]
    cmd = [exe, str(wav), "--model", model, "--output_format", "json", "--output_dir", str(outdir), "--fp16", "False",
           "--verbose", "False", "--condition_on_previous_text", str(args.keep_context)]
    if silence:
        cmd += ["--word_timestamps", "True", "--hallucination_silence_threshold", f"{silence:g}"]
    if task:
        cmd += ["--task", task]
    if language:
        cmd += ["--language", language]
    if args.whisper_args:
        cmd += shlex.split(args.whisper_args)
    return cmd


def show_command(cmd: list[str]) -> str:
    """The command as printed for the user (the mlx driver's code is not shown)."""
    if len(cmd) == 4 and cmd[1] == "-c":
        return f"mlx_whisper.transcribe {json.loads(cmd[3])['opts']}"
    return " ".join(shlex.quote(c) for c in cmd)


def drop_repetitions(segs: list[dict]) -> tuple[list[dict], int, float]:
    """Remove the traces of repetition loops from whisper segments: runs of three or more identical segments keep
    their first one, and a segment made of one word or phrase repeated over and over is dropped.
    Returns (kept segments, number removed, seconds removed)."""
    kept, removed, secs = [], 0, 0.0
    norm = lambda t: re.sub(r"\W+", " ", t.lower()).strip()
    i = 0
    while i < len(segs):
        j = i
        while j + 1 < len(segs) and norm(segs[j + 1]["text"]) == norm(segs[i]["text"]):
            j += 1
        run = segs[i:j + 1]
        keep = run if len(run) < 3 else run[:1]
        for s in keep:
            words = norm(s["text"]).split()
            top = Counter(words).most_common(1)[0][1] if words else 0
            if len(words) >= 8 and top / len(words) > 0.5 and s.get("compression_ratio", 0) > 2.4:
                removed += 1
                secs += s["end"] - s["start"]
            else:
                kept.append(s)
        for s in run[len(keep):]:
            removed += 1
            secs += s["end"] - s["start"]
        i = j + 1
    return kept, removed, secs


def tesseract_langs() -> list[str]:
    if not ENV.get("tesseract"):
        return []
    r = subprocess.run([ENV["tesseract"], "--list-langs"], capture_output=True, text=True)
    return [ln.strip() for ln in (r.stdout + r.stderr).splitlines()[1:] if ln.strip() and " " not in ln.strip()]


def report_env(args) -> int:
    """--check: what is installed, what is missing, and how to install it."""
    ok = sys.version_info >= (3, 9)
    print(f"python     {'ok' if ok else 'TOO OLD (3.9+ needed)'}  {sys.version.split()[0]}  ({sys.executable})")
    chosen = asr_tool(args)
    rows = [("ffmpeg", True), ("ffprobe", True), ("mlx_whisper", chosen == "mlx_whisper"), ("whisper", chosen == "whisper"),
            ("pandoc", False), ("latex", False), ("tesseract", False), ("pillow", False)]
    if sys.platform == "darwin" or chosen == "macwhisper":
        rows.insert(2, ("macwhisper", chosen == "macwhisper"))
    for t, required in rows:
        have = ENV.get(t)
        state = "ok" if have else ("MISSING (required)" if required else "missing (optional)")
        if t in ("macwhisper", "mlx_whisper", "whisper"):
            state += "  [engine]" if t == chosen else ""
        print(f"{t:<11} {state:<19} {have or ''}")
        if not have:
            print(f"{'':<10} for: {INSTALL[t][0]}\n{'':<10} install: {install_hint(t)}")
            ok = ok and not required
    if ENV.get("tesseract"):
        langs = tesseract_langs()
        print(f"{'':<10} tesseract languages: {', '.join(langs) or '?'}")
        want = TESS_LANG.get(args.language or "", None)
        if want and want not in langs:
            print(f"{'':<10} the language '{want}' for --language {args.language} is missing: {install_hint('tesseract')}")
    ais = available_ais(args)
    print("notes AI:   " + (", ".join(f"{AI_TOOLS[n][1]} ({p})" for n, p in ais.items()) if ais else
                            "none found (claude, agy or copilot: student notes cannot be written)") +
          (f"   [--notes-ai {args.notes_ai}]" if args.notes_ai != "auto" else ""))
    print(f"pdf files:  --latex {args.latex}" + (" (chosen)" if args.latex_chosen else f" (default: {'a LaTeX engine was found' if args.latex_found else 'no LaTeX engine found'})"))
    if args.engine == "macwhisper":
        print("engine: macwhisper; the model is the one selected in the MacWhisper app (--model provider:name uses another one)")
    else:
        cached, where = model_cached(args, args.model)
        print(f"engine: {args.engine} ({chosen}); model '{args.model}' = {asr_model(args, args.model)}: "
              + ("cached" if cached else f"not cached yet; downloaded on first use into {where}"))
    print("\nall required tools are there" if ok else "\nsome required tools are missing (see above)")
    if any(PACKAGE_OF.get(t) and not ENV.get(t) for t, _ in rows) and not args.setup:
        print(f"to install what is missing (not LaTeX), run:  {PY} lecture2notes.py --setup")
    return 0 if ok else 1


def setup(args) -> int:
    """--setup: show what is installed and offer to install what is missing (not LaTeX), then show it again."""
    report_env(args)
    wanted = ["ffmpeg", "ffprobe", asr_tool(args), "pandoc", "tesseract", "pillow", "pdftotext", "pdfinfo", "pdftoppm"]
    missing = [t for t in wanted if not ENV.get(t)]
    if not missing:
        print("\nnothing to install (LaTeX is never installed by this script)")
        return 0
    if INSTALL_MODE == "no":
        print("\n--install no: nothing was installed")
        return 1
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("\nrun --setup in a terminal to be offered the installation of what is missing")
        return 1
    offer_install(missing, "use every feature of lecture2notes")
    print("\nafter the installation:\n")
    return report_env(args)


# ----------------------------------------------------------------------------------------------- PPTX
def _rels(z: zipfile.ZipFile, path: str) -> dict[str, str]:
    """Relationship id -> target (as a path inside the zip) for the part at `path`."""
    d, f = path.rsplit("/", 1)
    rp = f"{d}/_rels/{f}.rels"
    if rp not in z.namelist():
        return {}
    out = {}
    for rel in ET.fromstring(z.read(rp)):
        t = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            continue
        base = d.split("/")
        for part in t.split("/"):
            if part == "..":
                if base:
                    base.pop()
            elif part and part != ".":
                base.append(part)
        out[rel.get("Id")] = ("/".join(base) if not t.startswith("/") else t.lstrip("/"))
        out[rel.get("Id") + ":type"] = rel.get("Type", "")
    return out


def _paragraphs(txbody: ET.Element) -> list[tuple[int, str]]:
    out = []
    for para in txbody.findall(f"{A}p"):
        ppr = para.find(f"{A}pPr")
        lvl = int(ppr.get("lvl", "0")) if ppr is not None else 0
        text = ""
        for node in para:
            if node.tag == f"{A}r":
                text += "".join(t.text or "" for t in node.findall(f"{A}t"))
            elif node.tag == f"{A}br":
                text += " "
            elif node.tag == f"{A}fld":
                text += "".join(t.text or "" for t in node.findall(f"{A}t"))
        text = text.strip()
        if text:
            out.append((lvl, text))
    return out


def _shape_text(root: ET.Element, skip_types=("sldNum", "dt", "ftr", "hdr", "sldImg")):
    """(title, [(level, text)]) of all shapes/tables of a slide or notes page, in document order."""
    title, items = None, []
    for el in root.iter():
        if el.tag == f"{P}sp":
            ph = el.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            ptype = ph.get("type") if ph is not None else None
            if ptype in skip_types:
                continue
            tb = el.find(f"{P}txBody")
            if tb is None:
                continue
            paras = _paragraphs(tb)
            if not paras:
                continue
            if ptype in ("title", "ctrTitle") and title is None:
                title = " ".join(t for _, t in paras)
            else:
                items.extend(paras)
        elif el.tag == f"{A}tbl":
            for tr in el.findall(f"{A}tr"):
                cells = []
                for tc in tr.findall(f"{A}tc"):
                    tb = tc.find(f"{A}txBody")
                    cells.append(" ".join(t for _, t in _paragraphs(tb)) if tb is not None else "")
                if any(cells):
                    items.append((0, " | ".join(cells)))
    return title, items


def read_pptx(path: Path) -> list[dict]:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        sys.exit(f"error: {path} is not a valid .pptx file")
    with z:
        pres = "ppt/presentation.xml"
        prels = _rels(z, pres)
        proot = ET.fromstring(z.read(pres))
        first = int(proot.get("firstSlideNum", "1"))  # number shown on the first slide (usually 1)
        order = [prels[s.get(f"{R}id")] for s in proot.iter(f"{P}sldId") if s.get(f"{R}id") in prels]
        slides = []
        for n, sp in enumerate(order, 1):
            root = ET.fromstring(z.read(sp))
            title, items = _shape_text(root)
            tagged = Counter((r.get("lang") or "").split("-")[0].lower() for r in root.iter() if r.tag in (f"{A}rPr", f"{A}endParaRPr"))
            notes = []
            rels = _rels(z, sp)
            for rid, tgt in rels.items():
                if not rid.endswith(":type") and rels.get(rid + ":type", "").endswith("/notesSlide") and tgt in z.namelist():
                    _, nitems = _shape_text(ET.fromstring(z.read(tgt)))
                    notes = [t for _, t in nitems]
            slides.append({"n": n, "title": title or "", "items": items, "notes": notes, "shown": n + first - 1, "kind": "pptx", "part": sp,
                           "tagged": {k: v for k, v in tagged.items() if k}})
    return slides


def read_pdf(path: Path) -> list[dict]:
    """Slides from a PDF export of a deck, one page per slide: the first line of a page is its title, the others its
    items. Lines repeated on most pages (footer, date, course name) and bare page numbers or bullets are dropped.
    There are no speaker notes in a PDF. Needs pdftotext and pdfinfo (poppler)."""
    require(["pdftotext", "pdfinfo"], f"to read the slides in {path.name}")
    info = subprocess.run([ENV["pdfinfo"], str(path)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if not m:
        sys.exit(f"error: {path.name} is not a readable PDF")
    pages = []
    for n in range(1, int(m.group(1)) + 1):  # one page at a time: some fonts turn "ff" ligatures into form feeds
        r = subprocess.run([ENV["pdftotext"], "-f", str(n), "-l", str(n), "-enc", "UTF-8", str(path), "-"],
                           capture_output=True, text=True, errors="replace")
        text = r.stdout[:-1] if r.stdout.endswith("\f") else r.stdout  # pdftotext ends every page with a form feed
        text = text.replace("\f", "ff")  # any other one is a ligature the font could not map
        lines = [" ".join(ln.split()) for ln in text.splitlines()]
        pages.append([ln for ln in lines if ln and not re.fullmatch(r"[\d.\-–—•·▪○*>]{1,4}", ln)])
    common = Counter(ln for pg in pages for ln in set(pg))
    boiler = {ln for ln, c in common.items() if len(pages) >= 4 and c > 0.5 * len(pages)}
    slides = []
    for n, pg in enumerate(pages, 1):
        lines = []
        for ln in pg:
            if ln in boiler:
                continue
            ln = re.sub(r"^[•·▪○◦\-–—*>]\s+", "", ln)  # the bullet itself
            if lines and lines[-1][-1:] not in ".:;!?)" and ln[:1].islower():
                lines[-1] += " " + ln  # a line wrapped on the slide: join it to the previous one
            else:
                lines.append(ln)
        slides.append({"n": n, "title": lines[0] if lines else "", "items": [(0, ln) for ln in lines[1:]][:60], "notes": [],
                       "shown": n, "kind": "pdf", "tagged": {}, "pdf": str(path)})
    return slides


def render_pdf_pages(slides: list[dict], outdir: Path, fstem: str) -> None:
    """A picture of every PDF slide (pdftoppm), shown in the notes."""
    pdf_slides = [sl for sl in slides if sl.get("kind") == "pdf"]
    if not pdf_slides or not note_missing(["pdftoppm"], "to show pictures of the PDF slides in the notes"):
        return
    imgdir = outdir / f"{fstem}-slides"
    imgdir.mkdir(exist_ok=True)
    for old in imgdir.glob("slide-*.png"):
        old.unlink()
    print(f"saving pictures of the {len(pdf_slides)} PDF slides in {imgdir.name}/ ...")
    for sl in pdf_slides:
        prefix = imgdir / f"slide-{sl['n']:03d}"
        subprocess.run([ENV["pdftoppm"], "-png", "-singlefile", "-scale-to", "1400", "-f", str(sl["k"]), "-l", str(sl["k"]),
                        sl["pdf"], str(prefix)], capture_output=True)
        if prefix.with_suffix(".png").exists():
            sl["image"] = f"{imgdir.name}/{prefix.name}.png"


def read_decks(paths: list[Path]) -> list[dict]:
    """The slides of all decks (PPTX or PDF), in the order given, numbered continuously (`k` = number inside its own deck)."""
    out: list[dict] = []
    for d, path in enumerate(paths):
        for sl in (read_pdf(path) if path.suffix.lower() == ".pdf" else read_pptx(path)):
            sl["k"], sl["deck"], sl["deck_name"] = sl["n"], d, path.stem
            sl["n"] = len(out) + 1
            out.append(sl)
    return out


def slide_ref(sl: dict, multi: bool) -> str:
    """'Slide 17' or, when several decks are joined, '<deck>, slide 17' (the number shown on the slide itself);
    'Part 3' for the parts of the speech made without slides."""
    if sl.get("kind") == "time":
        return f"Part {sl['n']}"
    if sl.get("kind") in ("pptx", "pdf") and multi:
        return f"{sl['deck_name']}, slide {sl['k']}"
    return f"Slide {sl['n']}"


# ----------------------------------------------------------------------------------------------- text helpers
STOP = set(
    """the and for that this with from are was were has have had not but you your our their its they them his her she
    can will would could should may might one all any each more most some such than then also into about over
    what which when where who whom how why there here these those been being does did doing done just very much
    only other out off own same too use used using let get got say said like
    que para com uma uns umas dos das nos nas por mais como mas foi ser são tem têm ter está estão isto isso
    aquilo aqui ali muito também quando onde qual quais seus suas nosso nossa então pois ainda sobre entre até
    sem sua seu essa esse esses essas num numa pelo pela pelos pelas""".split()
)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


STOP = {_fold(w) for w in STOP}  # _token() folds the accents of a word before looking it up here


def _token(w: str) -> str | None:
    w = _fold(w)
    if len(w) < 3 or w in STOP:
        return None
    return w[:-1] if len(w) > 4 and w.endswith("s") else w  # crude plural stripping


def tokens(text: str) -> list[str]:
    return [t for t in (_token(w) for w in re.findall(r"[^\W_]{3,}", text)) if t]


LANG_WORDS = {  # very common short words (accents removed), enough to tell the languages of slides and speech apart
    "en": "the and of to in is that it for with as are this on be by from or not we you can which have has an at but if will was "
          "into its how what when your our their more about between each both",
    "pt": "de que os as do da dos das em no na um uma para com por nao se mais ao como mas sao foi tem isso este esta pelo pela "
          "entao muito tambem quando ou seu sua e o a",
    "es": "el los las del que en un una para con por no se mas como pero son fue tiene esto este esta muy tambien cuando su y es",
    "fr": "le la les des du de et en un une pour avec par ne pas se plus comme mais sont est ce cette tres aussi quand sur dans qui",
    "de": "der die das und ist nicht ein eine mit von zu den dem des auf fur sich auch als wie aber wird sind im oder wenn dass",
    "it": "il lo la gli le di che e un una per con non si piu come ma sono del della nel questo questa anche quando su",
}
LANG_WORDS = {k: set(v.split()) for k, v in LANG_WORDS.items()}
LANG_ENDINGS = {"pt": ("cao", "coes", "oes"), "es": ("cion", "ciones"), "fr": ("eux", "ait"), "de": ("ung", "keit", "lich"),
                "it": ("zione", "zioni"), "en": ("ing", "tion", "ed", "ly")}
LANG_NAME = {"en": "English", "pt": "Portuguese", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian"}


def detect_language(text: str) -> str | None:
    """The language of a text (en, pt, es, fr, de, it), or None if there is too little evidence: counts very common
    words and typical endings. Good enough for a deck of slides or a few minutes of speech; nothing to install."""
    words = re.findall(r"[^\W\d_]+", _fold(text))
    hits = Counter()
    for w in words:
        for lang, common in LANG_WORDS.items():
            if w in common:
                hits[lang] += 1
            elif len(w) > 5 and w.endswith(LANG_ENDINGS[lang]):
                hits[lang] += 0.5
    if not hits:
        return None
    (best, b), *rest = hits.most_common(2) + [("", 0)]
    second = rest[0][1]
    return best if b >= min(8, max(3, 0.05 * len(words))) and b >= 1.5 * second else None


def lang_name(code: str | None) -> str:
    return LANG_NAME.get(code or "", code or "unknown")


def hms(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def md_escape(s: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#|~$^@])", r"\\\1", s)


def safe_stem(s: str, default: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s).strip("_.") or default


# ----------------------------------------------------------------------------------------------- alignment
def slide_text(sl: dict) -> str:
    return " ".join([sl["title"]] * 3 + [t for _, t in sl["items"]] + sl["notes"])


def build_scores(segments: list[dict], slides: list[dict]) -> list[list[float]]:
    docs = [Counter(tokens(slide_text(s))) for s in slides]
    n = len(slides)
    df = Counter(w for d in docs for w in d)
    idf = {w: math.log((1 + n) / (1 + c)) + 1.0 for w, c in df.items()}

    def vec(counter: Counter) -> dict[str, float]:
        v = {w: (1 + math.log(c)) * idf[w] for w, c in counter.items() if w in idf and c > 0}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    sv = [vec(d) for d in docs]
    seg_tokens = [Counter(tokens(s.get("align_text", s["text"]))) for s in segments]  # align_text: a translation
    scores = []
    for i in range(len(segments)):
        win = Counter()  # the segment and its neighbours (half weight), as the speech is cut in arbitrary pieces
        for j, wgt in ((i - 1, 0.5), (i, 1.0), (i + 1, 0.5)):
            if 0 <= j < len(segments):
                for w, c in seg_tokens[j].items():
                    win[w] += c * wgt
        v = vec(win)
        scores.append([sum(x * s.get(w, 0.0) for w, x in v.items()) for s in sv])
    return scores


def align(scores: list[list[float]], penalty: float, backward: bool) -> list[int]:
    """Best slide per segment: maximise the total similarity, paying `penalty` for each change of slide.
    Without `backward` the slide index never decreases."""
    if not scores or not scores[0]:
        return [0] * len(scores)
    S = len(scores[0])
    dp = [[0.0] * S for _ in scores]
    back = [[0] * S for _ in scores]
    for s in range(S):
        dp[0][s] = scores[0][s] - penalty * 0.2 * s
    for i in range(1, len(scores)):
        prev = dp[i - 1]
        if backward:
            best = max(range(S), key=lambda k: prev[k])
            move = prev[best] - penalty
            for s in range(S):
                if prev[s] >= move:
                    dp[i][s], back[i][s] = scores[i][s] + prev[s], s
                else:
                    dp[i][s], back[i][s] = scores[i][s] + move, best
        else:
            best_k, best_v = 0, prev[0]  # best predecessor among slides before s
            for s in range(S):
                if s > 0 and prev[s - 1] > best_v:
                    best_k, best_v = s - 1, prev[s - 1]
                move = best_v - penalty if s > 0 else float("-inf")
                if prev[s] >= move:
                    dp[i][s], back[i][s] = scores[i][s] + prev[s], s
                else:
                    dp[i][s], back[i][s] = scores[i][s] + move, best_k
    s = max(range(S), key=lambda k: dp[-1][k])
    path = [s]
    for i in range(len(scores) - 1, 0, -1):
        s = back[i][s]
        path.append(s)
    return path[::-1]


def key_sentences(text: str, slides_idf_text: list[str], n: int) -> list[str]:
    """Extractive key points: the n sentences richest in words that are rare across the slides."""
    if n <= 0:
        return []
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 6]
    if len(sents) <= n:
        return sents
    df = Counter(w for t in slides_idf_text for w in set(tokens(t)))
    N = len(slides_idf_text)

    def score(s: str) -> float:
        ws = set(tokens(s))
        return sum(math.log((1 + N) / (1 + df.get(w, 0))) + 1 for w in ws) / math.sqrt(len(s.split()))

    top = sorted(range(len(sents)), key=lambda i: -score(sents[i]))[:n]
    return [sents[i] for i in sorted(top)]


# ----------------------------------------------------------------------------------------------- slide numbers in the video
def probe_video(rec: Path):
    """(width, height, duration in s) of the first video stream, or None if there is no video."""
    if not ENV.get("ffprobe"):
        return None
    r = subprocess.run([ENV["ffprobe"], "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", str(rec)], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        return int(d["streams"][0]["width"]), int(d["streams"][0]["height"]), float(d["format"]["duration"])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def detect_slide_box(rec: Path, dur: float) -> tuple[float, float, float, float]:
    """Where the slide is inside the frame (x0, y0, x1, y1 as fractions): the screen recording may have black bars."""
    from PIL import Image, ImageFilter

    x0 = y0 = 1.0
    x1 = y1 = 0.0
    for fr in (0.1, 0.3, 0.5, 0.7, 0.9):
        r = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{dur * fr:.1f}", "-i", str(rec), "-frames:v", "1",
                            "-vf", "scale=480:-1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        if not r.stdout:
            continue
        im = Image.open(io.BytesIO(r.stdout)).convert("L")
        mask = im.point(lambda v: 255 if v > 32 else 0).filter(ImageFilter.MinFilter(9))  # erosion drops cursors and specks
        bb = mask.getbbox()
        if bb:
            w, h = im.size
            e = 4  # the erosion shrank the box by 4 pixels on every side
            x0, y0 = min(x0, max(0, bb[0] - e) / w), min(y0, max(0, bb[1] - e) / h)
            x1, y1 = max(x1, min(w, bb[2] + e) / w), max(y1, min(h, bb[3] + e) / h)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0, 1.0, 1.0
    return x0, y0, x1, y1


def ocr_digits(img) -> int | None:
    """The number written in a small grey-scale image (tesseract, digits only). Small videos have digits a few pixels
    tall: they are read at two enlargements, as the first digit of "37" can vanish at the smaller one and other
    numbers at the larger one; when the readings agree ("7" in "37"), the longer one wins."""
    import tempfile

    from PIL import Image, ImageOps, ImageStat

    def read(sc: int, tmp: str) -> str:
        im = img.convert("L")
        im = im.resize((im.width * sc, im.height * sc), Image.LANCZOS)
        if ImageStat.Stat(im).mean[0] < 128:  # light digits on a dark slide
            im = ImageOps.invert(im)
        im = ImageOps.expand(ImageOps.autocontrast(im), border=24, fill=255)
        f = Path(tmp) / f"n{sc}.png"
        im.save(f)
        for psm in ("7", "6", "8"):
            r = subprocess.run([tool("tesseract"), str(f), "stdout", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789"],
                               capture_output=True, text=True)
            digits = re.sub(r"\D", "", r.stdout)[:4]
            if digits:
                return digits
        return ""

    big = round(180 / max(1, img.height))
    with tempfile.TemporaryDirectory() as tmp:
        a = read(3, tmp)
        b = read(big, tmp) if big > 3 else ""
    if a and b and len(b) > len(a) and (b.endswith(a) or b.startswith(a)):
        a = b
    digits = a or b
    return int(digits) if digits else None


NUMBER_SPOTS = {  # where slide numbers usually are, as (x, y, w, h) fractions of the slide area
    "bottom right": (0.850, 0.915, 0.147, 0.085),
    "bottom left": (0.003, 0.915, 0.147, 0.085),
    "bottom left, inset": (0.060, 0.915, 0.120, 0.085),  # clear of a decoration along the left edge
    "bottom centre": (0.430, 0.915, 0.140, 0.085),
}


def find_number_box(rec: Path, dur: float, max_shown: int) -> tuple[str, tuple[float, float, float, float]] | None:
    """Try the usual places of the slide number on frames spread over the recording; the place where the most frames
    show a plausible number (1..max_shown, at least two different ones) wins. None if no place works."""
    from PIL import Image

    x0, y0, x1, y1 = detect_slide_box(rec, dur)
    sw, sh = x1 - x0, y1 - y0
    boxes = {f"{name} of the slide": (x0 + fx * sw, y0 + fy * sh, fw * sw, fh * sh) for name, (fx, fy, fw, fh) in NUMBER_SPOTS.items()}
    if sw * sh < 0.97:  # black bars were found... or a dark slide fooled the search: try the places in the whole frame too
        boxes.update({f"{name} of the frame": spot for name, spot in NUMBER_SPOTS.items()})
    found: dict[str, list[int]] = {name: [] for name in boxes}
    times = [dur * (0.03 + 0.94 * k / 29) for k in range(30)]
    print(f"looking for the slide numbers in {rec.name}: trying {', '.join(boxes)} on {len(times)} frames ...")
    for t in times:
        r = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{t:.1f}", "-i", str(rec), "-frames:v", "1", "-f", "image2pipe",
                            "-vcodec", "png", "-"], capture_output=True)
        if not r.stdout:
            continue
        im = Image.open(io.BytesIO(r.stdout)).convert("L")
        W, H = im.size
        for name, (bx, by, bw, bh) in boxes.items():
            crop = im.crop((int(bx * W), int(by * H), min(W, int((bx + bw) * W)), min(H, int((by + bh) * H))))
            if crop.width < 4 or crop.height < 4 or max(crop.tobytes()) - min(crop.tobytes()) <= 40:
                continue
            num = ocr_digits(crop)
            if num is not None and 1 <= num <= max_shown:
                found[name].append(num)
    score = {name: len(v) if len(set(v)) >= 2 else 0 for name, v in found.items()}  # one constant "number": part of a logo
    best = max(score, key=lambda k: score[k])
    print("  readable numbers: " + ", ".join(f"{name} {score[name]}/{len(times)}" for name in boxes))
    if score[best] < 0.2 * len(times):
        return None
    return best, boxes[best]


def read_slide_numbers(rec: Path, outdir: Path, stem: str, args, max_shown: int = 9999) -> dict | None:
    """Look at the recording once per 1/fps seconds, read the slide number in a corner of the slide and
    measure the visual change between samples. Returns {"fps", "numbers": [int|None per sample], "diffs": [float per
    sample]}; the numbers are not checked against the slides and the diffs not against a threshold here, so that the
    cache stays valid when the decks or --change-threshold change."""
    cache = outdir / f"{stem}.slidenumbers.json"
    src = source_fingerprint(rec)
    if cache.exists() and not args.force:
        d = json.loads(cache.read_text())
        if (d.get("version") == 6 and d.get("fps") == args.video_fps and d.get("box_arg") == args.number_box
                and d.get("source") in (None, src)  # None: a cache made before the recording was recorded in it
                and (d.get("numbers") is not None or d.get("max_shown") == max_shown)):  # "none found" depends on the decks
            print(f"using the slide numbers already read from {rec.name} ({cache.name})")
            return d if d.get("numbers") is not None else None
        if d.get("source") not in (None, src):
            print(f"note: {rec.name} changed since its slide numbers were read ({cache.name}): reading them again")
    from PIL import Image

    info = probe_video(rec)
    if not info:
        print(f"warning: {rec.name} has no video stream; no slide numbers to read")
        return None
    W, H, dur = info
    if args.number_box:
        try:
            bx, by, bw, bh = (float(v) for v in args.number_box.split(","))
        except ValueError:
            sys.exit(f"error: --number-box must be four fractions x,y,w,h, not {args.number_box!r}")
    else:
        spot = find_number_box(rec, dur, max_shown)
        if spot is None:
            print(f"note: no slide numbers found in {rec.name} (give --number-box x,y,w,h if they are somewhere else)")
            cache.write_text(json.dumps({"version": 6, "fps": args.video_fps, "box_arg": args.number_box, "numbers": None,
                                         "source": src, "max_shown": max_shown}))
            return None
        name, (bx, by, bw, bh) = spot
        print(f"slide numbers found at the {name}; reading them in box {bx:.3f},{by:.3f},{bw:.3f},{bh:.3f} (--number-box x,y,w,h)")
    # keep the crop inside the frame (ffmpeg fails otherwise)
    cx, cy = min(max(0, int(bx * W)), W - 8), min(max(0, int(by * H)), H - 8)
    cw, ch = max(8, min(int(bw * W), W - cx)), max(8, min(int(bh * H), H - cy))
    tmp = outdir / f".{stem}.video"
    tmp.mkdir(exist_ok=True)
    try:
        small, crop = tmp / "small.gray", tmp / "crop.gray"
        sw_px, sh_px = 128, 72
        print(f"looking at {rec.name} ({hms(dur)}, {args.video_fps:g} frame/s) ...")
        flt = (f"[0:v]fps={args.video_fps},split=2[a][b];[a]scale={sw_px}:{sh_px}:flags=area,format=gray[s];"
               f"[b]format=gray,crop={cw}:{ch}:{cx}:{cy}[c]")  # gray first: cropping yuv420 would round the size to even numbers
        subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(rec), "-an", "-filter_complex", flt,
                        "-map", "[s]", "-f", "rawvideo", str(small), "-map", "[c]", "-f", "rawvideo", str(crop)], check=True)
        if crop.stat().st_size % (cw * ch):
            sys.exit(f"error: unexpected frame size when cropping {rec.name}")
        n = min(crop.stat().st_size // (cw * ch), small.stat().st_size // (sw_px * sh_px))
        numbers: list[int | None] = []
        diffs: list[float] = []
        prev_sig, prev_num, prev_small = None, None, None
        cache_ocr: dict[bytes, int | None] = {}
        with open(crop, "rb") as fc, open(small, "rb") as fs:
            for i in range(n):
                raw = fc.read(cw * ch)
                sm = fs.read(sw_px * sh_px)
                diffs.append(0.0 if prev_small is None else _mad(sm, prev_small))
                prev_small = sm
                img = Image.frombytes("L", (cw, ch), raw)
                sig = img.resize((64, max(2, round(64 * ch / cw))), Image.BOX).tobytes()
                if prev_sig is not None and max(abs(a - b) for a, b in zip(sig, prev_sig)) <= 24:
                    numbers.append(prev_num)  # the number region did not change: same slide number
                    continue
                if max(raw) - min(raw) <= 40:  # no contrast: nothing written there (slide without a number)
                    num = None
                else:
                    key = bytes(v // 48 for v in sig)
                    if key in cache_ocr:
                        num = cache_ocr[key]
                    else:
                        num = ocr_digits(img)
                        cache_ocr[key] = num
                if num is not None and num < 1:
                    num = None
                prev_sig, prev_num = sig, num
                numbers.append(num)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    d = {"version": 6, "fps": args.video_fps, "box_arg": args.number_box, "numbers": numbers, "diffs": diffs, "source": src}
    cache.write_text(json.dumps(d))
    print(f"{sum(x is not None for x in numbers)} of {len(numbers)} samples have a readable slide number ({len(cache_ocr)} distinct readings)")
    return d


def clean_numbers(numbers: list[int | None], big: list[bool]) -> list[int | None]:
    """Remove one-sample misreadings and fill the gaps between two equal readings with no visual change inside."""
    n = len(numbers)
    out = list(numbers)
    # runs of equal readings
    runs, i = [], 0
    while i < n:
        j = i
        while j < n and numbers[j] == numbers[i]:
            j += 1
        runs.append([i, j, numbers[i]])
        i = j
    for k, (a, b, v) in enumerate(runs):
        if v is None or b - a > 1 or k == 0 or k == len(runs) - 1:
            continue
        near = {x for x in (runs[k - 1][2], runs[k + 1][2]) if x is not None}
        if near and not any(abs(v - x) <= 1 for x in near):
            out[a:b] = [None] * (b - a)  # isolated one-sample reading, unlike its neighbours: an OCR error
    runs, i = [], 0
    while i < n:
        j = i
        while j < n and out[j] == out[i]:
            j += 1
        runs.append((i, j, out[i]))
        i = j
    for k in range(1, len(runs) - 1):
        a, b, v = runs[k]
        if v is None and runs[k - 1][2] is not None and runs[k - 1][2] == runs[k + 1][2] and not any(big[a + 1:b]):
            out[a:b] = [runs[k - 1][2]] * (b - a)  # same number before and after, nothing changed in between
    return out


def to_global(numbers: list[int | None], slides: list[dict], fps: float, evidence=None) -> list[int | None]:
    """Displayed number -> index in the joined slides. With several decks the same number exists in each one; among
    those candidates take the one whose words best match the speech while that number was on screen (`evidence(t0, t1,
    candidate)` gives the similarity) and, when the speech does not tell, the one that continues the lecture best."""
    out: list[int | None] = [None] * len(numbers)
    prev = None
    i, n = 0, len(numbers)
    while i < n:
        v = numbers[i]
        if v is None:
            i += 1
            continue
        j = i
        while j < n and numbers[j] == v:
            j += 1
        cands = [k for k, sl in enumerate(slides) if sl["shown"] == v]
        g = None
        if len(cands) == 1:
            g = cands[0]
        elif cands:
            ev = [evidence(i / fps, j / fps, c) for c in cands] if evidence else [0.0] * len(cands)
            top = max(ev)
            if top > 0 and sorted(ev)[-1] > 1.25 * sorted(ev)[-2]:
                g = cands[ev.index(top)]
            elif prev is None:
                g = cands[0]
            else:
                g = min(cands, key=lambda c: (c - prev) if c >= prev else 2 * (prev - c))
        if g is not None:
            out[i:j] = [g] * (j - i)
            prev = g
        i = j
    return out


def segment_slide(seg: dict, timeline: list[int | None], fps: float) -> int | None:
    """The slide shown (from the video) during most of the segment, None if no number was readable meanwhile."""
    a = int(seg["start"] * fps)
    b = min(len(timeline), max(a + 1, int(math.ceil(seg["end"] * fps))))
    votes = Counter(g for g in timeline[a:b] if g is not None)
    return votes.most_common(1)[0][0] if votes else None


def align_pinned(scores: list[list[float]], pins: list[int | None], penalty: float, backward: bool) -> list[int]:
    """Segments whose slide is known from the video keep it; the stretches between them are aligned by the words,
    only among the slides between the known ones before and after."""
    n, S = len(scores), len(scores[0])
    out: list[int] = list(pins)  # type: ignore[arg-type]
    i = 0
    while i < n:
        if pins[i] is not None:
            i += 1
            continue
        j = i
        while j < n and pins[j] is None:
            j += 1
        lo, hi = (pins[i - 1] if i > 0 else None), (pins[j] if j < n else None)
        a, b = (0 if lo is None else lo), (S - 1 if hi is None else hi)
        back = backward or (lo is not None and hi is not None and lo > hi)
        if a > b:
            a, b = b, a
        path = align([row[a:b + 1] for row in scores[i:j]], penalty, back)
        out[i:j] = [a + p for p in path]
        i = j
    return out


# ----------------------------------------------------------------------------------------------- without slides
def _mad(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two grey-level images of the same size (0-255)."""
    return sum(map(abs, map(operator.sub, a, b))) / max(1, len(a))


SCENE_W, SCENE_H = 160, 90
PIX_DELTA = 24  # grey-level change that counts as a changed pixel (small frames are blurred by the downscaling)


def _changed(a: bytes, b: bytes) -> float:
    """Percentage of the pixels that changed visibly between two small grey frames (robust to compression noise; a
    mean difference is not: two white slides differ in few pixels, so their mean difference is tiny)."""
    if a == b:
        return 0.0
    return 100.0 * sum(abs(x - y) > PIX_DELTA for x, y in zip(a, b)) / max(1, len(a))


def _differs(a: bytes, b: bytes, pct: float) -> bool:
    """True if more than `pct` percent of the pixels changed visibly: the same as `_changed(a, b) > pct`, but it stops
    counting as soon as the answer is known, which is much faster when comparing a frame with many different ones."""
    if a == b:
        return False
    limit, n = pct * max(1, len(a)) / 100.0, 0
    for x, y in zip(a, b):
        if abs(x - y) > PIX_DELTA:
            n += 1
            if n > limit:
                return True
    return False


def _is_build(a: bytes, b: bytes) -> bool:
    """True if `b` only adds to `a` (a bullet or a picture appears on the same slide): the pixels that changed were,
    almost all, background in `a`."""
    bg = Counter(a).most_common(1)[0][0]
    changed = [x for x, y in zip(a, b) if abs(x - y) > PIX_DELTA]
    return bool(changed) and sum(abs(x - bg) <= PIX_DELTA for x in changed) >= 0.9 * len(changed)


def detect_scenes(rec: Path, outdir: Path, stem: str, args) -> dict | None:
    """Cut the recording where the picture changes a lot: {"n": samples, "segments": [{"a", "b", "t", "rep"}]} with the
    samples [a, b) of each piece, the time `t` of its representative frame and that frame (`rep`, small, grey)."""
    cache = outdir / f"{stem}.scenes.json"
    params = {"version": 2, "fps": args.video_fps, "threshold": args.scene_threshold, "min_seconds": args.min_slide_seconds}
    src = source_fingerprint(rec)
    if cache.exists() and not args.force:
        d = json.loads(cache.read_text())
        if d.get("params") == params and d.get("source") in (None, src):
            print(f"using the screens already found in {rec.name} ({cache.name})")
            return {"n": d["n"], "segments": [dict(s, rep=bytes.fromhex(s["rep"])) for s in d["segments"]]}
    info = probe_video(rec)
    if not info:
        print(f"note: {rec.name} has no video stream")
        return None
    print(f"looking for the distinct screens of {rec.name} ({hms(info[2])}, {args.video_fps:g} frame/s) ...")
    cmd = [tool("ffmpeg"), "-v", "error", "-i", str(rec), "-an", "-vf",
           f"fps={args.video_fps},scale={SCENE_W}:{SCENE_H}:flags=area,format=gray", "-f", "rawvideo", "-"]
    fs = SCENE_W * SCENE_H
    frames: list[bytes] = []
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:  # read frame by frame: the whole video is never in memory twice
        while True:
            buf = proc.stdout.read(fs)
            if len(buf) < fs:
                break
            frames.append(buf)
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    if not frames:
        return None
    cuts = [0] + [i for i in range(1, len(frames)) if _differs(frames[i], frames[i - 1], args.scene_threshold)] + [len(frames)]
    pieces = list(zip(cuts, cuts[1:]))
    min_len = max(1, round(args.min_slide_seconds * args.video_fps))
    merged: list[tuple[int, int]] = []
    for a, b in pieces:  # a short piece (a transition, an animation, a pop-up) joins the next one
        if merged and merged[-1][1] - merged[-1][0] < min_len:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    if len(merged) > 1 and merged[-1][1] - merged[-1][0] < min_len:
        merged[-2:] = [(merged[-2][0], merged[-1][1])]
    rep_at = lambda a, b: b - 1 if b - a < 3 else b - 2  # near the end: the slide fully built, before the next transition
    built: list[tuple[int, int]] = []
    for a, b in merged:  # a piece that only adds to the previous one is a build of the same slide
        if built and _is_build(frames[rep_at(*built[-1])], frames[rep_at(a, b)]):
            built[-1] = (built[-1][0], b)
        else:
            built.append((a, b))
    segs = []
    for a, b in built:
        k = rep_at(a, b)
        segs.append({"a": a, "b": b, "t": round(k / args.video_fps, 2), "rep": frames[k]})
    cache.write_text(json.dumps({"params": params, "source": src, "n": len(frames),
                                 "segments": [dict(s, rep=s["rep"].hex()) for s in segs]}))
    print(f"{len(segs)} pieces between big visual changes")
    return {"n": len(frames), "segments": segs}


def ocr_screen(png: Path, langs: str | None) -> list[str]:
    """The lines of text tesseract reads in a frame, without the noise lines."""
    cmd = [tool("tesseract"), str(png), "stdout", "--psm", "3"]
    if langs:
        cmd += ["-l", langs]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = []
    for ln in r.stdout.splitlines():
        ln = re.sub(r"^[-–—•·▪‣*>o»]\s+", "", " ".join(ln.split()))  # the bullet itself
        letters = sum(c.isalnum() for c in ln)
        if letters >= 3 and letters >= 0.5 * len(ln.replace(" ", "")):
            out.append(ln)
    return out


def visual_slides(per_rec: list[dict | None], recs: list[Path], outdir: Path, fstem: str, args, speech_lang: str | None = None):
    """Distinct screens across all recordings (a screen seen again is the same slide). Returns (slides, timelines),
    one timeline per recording with the slide index at each sample (None: blank screen or no video)."""
    uniq: list[dict] = []
    timelines: list[list[int | None] | None] = []
    for ri, d in enumerate(per_rec):
        if d is None:
            timelines.append(None)
            continue
        tl: list[int | None] = [None] * d["n"]
        for sg in d["segments"]:
            if max(sg["rep"]) - min(sg["rep"]) < 20:  # a blank (black, white) screen is not a slide
                continue
            dur = sg["b"] - sg["a"]
            j = next((k for k, u in enumerate(uniq) if not _differs(u["rep"], sg["rep"], args.same_slide_threshold)), None)
            if j is None:
                uniq.append({"rep": sg["rep"], "dur": dur, "rec": ri, "t": sg["t"]})
                j = len(uniq) - 1
            elif dur > uniq[j]["dur"]:  # the picture of the slide is taken from its longest visit
                uniq[j].update(dur=dur, rec=ri, t=sg["t"])
            elif _is_build(uniq[j]["rep"], sg["rep"]):  # ... or from a visit where it is more complete
                uniq[j].update(rep=sg["rep"], rec=ri, t=sg["t"])
            tl[sg["a"]:sg["b"]] = [j] * dur
        timelines.append(tl)
    if not uniq:
        return [], timelines
    imgdir = outdir / f"{fstem}-slides"
    imgdir.mkdir(exist_ok=True)
    for old in imgdir.glob("slide-*.png"):
        old.unlink()
    ocr = note_missing(["tesseract"], "to read the text on the screen (without it the notes only show the pictures)")
    print(f"{len(uniq)} distinct screens; saving their pictures in {imgdir.name}/" + (" and reading their text ..." if ocr else ""))
    pngs = []
    for n, u in enumerate(uniq, 1):
        png = imgdir / f"slide-{n:03d}.png"
        subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{u['t']:.2f}", "-i", str(recs[u["rec"]]), "-frames:v", "1",
                        "-vf", "scale='min(1600,iw)':-2", str(png)], check=True)
        pngs.append(png if png.exists() else None)
    texts: list[list[str]] = [[] for _ in pngs]
    if ocr:
        have = tesseract_langs()
        use = [x for x in dict.fromkeys(("eng", TESS_LANG.get(args.language or speech_lang or ""))) if x and x in have]
        texts = [ocr_screen(p, "+".join(use) or None) if p else [] for p in pngs]
        screen_lang = detect_language(" ".join(ln for t in texts for ln in t))
        want = TESS_LANG.get(screen_lang or "")
        print(f"language of the text on the screen: {lang_name(screen_lang)}")
        REPORT["screen"] = lang_name(screen_lang)
        if want and want not in use:  # the screen is in another language than the one OCR was told: read it again
            if want in have:
                print(f"reading the screens again with the {lang_name(screen_lang)} OCR data ...")
                texts = [ocr_screen(p, "+".join(use + [want])) if p else [] for p in pngs]
            else:
                print(f"note: tesseract has no {lang_name(screen_lang)} data ('{want}'), so accents may be misread: {install_hint('tesseract')}")
        if screen_lang and speech_lang and screen_lang != speech_lang:
            print(f"note: the screen is in {lang_name(screen_lang)} and the speech in {lang_name(speech_lang)}; that is fine here, "
                  "the sections come from the pictures, not from the words")
    slides = []
    for n, (u, png, lines) in enumerate(zip(uniq, pngs, texts), 1):
        title = next((ln for ln in lines if sum(c.isalpha() for c in ln) >= 3 and len(ln) <= 100), "")
        items = [(0, ln) for ln in lines if ln != title][:40]
        when = f"P{u['rec'] + 1} {hms(u['t'])}" if len(recs) > 1 else hms(u["t"])
        slides.append({"n": n, "k": n, "title": title, "items": items, "notes": [], "shown": n, "deck": 0, "deck_name": "",
                       "kind": "video", "image": f"{imgdir.name}/{png.name}" if png else None, "picture_at": when})
    return slides, timelines


def time_sections(segs: list[dict], minutes: float) -> tuple[list[dict], list[int]]:
    """Cut the speech into parts of about `minutes` minutes, at pauses, titled with their most distinctive words."""
    target = max(30.0, minutes * 60)
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for s in segs:
        if cur:
            dur, gap = s["start"] - cur[0]["start"], s["start"] - cur[-1]["end"]
            if s["rec"] != cur[-1]["rec"] or (dur >= target and gap >= 1.0) or dur >= 1.5 * target:
                groups.append(cur)
                cur = []
        cur.append(s)
    if cur:
        groups.append(cur)
    surface: dict[str, Counter] = {}
    tfs = []
    for g in groups:
        tf = Counter()
        for w in re.findall(r"[^\W_]{3,}", " ".join(s["text"] for s in g)):
            t = _token(w)
            if t and not w.isdigit():
                tf[t] += 1
                surface.setdefault(t, Counter())[w.lower()] += 1
        tfs.append(tf)
    df = Counter(t for tf in tfs for t in tf)
    slides = []
    for k, tf in enumerate(tfs):
        weight = {t: (1 + math.log(c)) * math.log((1 + len(tfs)) / df[t] + 1) for t, c in tf.items() if c > 1 or len(tf) < 40}
        best = sorted(weight, key=lambda t: -weight[t])[:4]
        title = ", ".join(surface[t].most_common(1)[0][0] for t in best)
        slides.append({"n": k + 1, "k": k + 1, "title": title, "items": [], "notes": [], "shown": k + 1, "deck": 0,
                       "deck_name": "", "kind": "time"})
    path = [gi for gi, g in enumerate(groups) for _ in g]
    return slides, path


def sections_without_slides(segs, recs, stems, outdir, fstem, args, speech_lang: str | None) -> tuple[list[dict], list[int], str]:
    """Slides made from the video (distinct screens) or, failing that, from the time. Returns (slides, path, mode)."""
    if args.sections in ("auto", "video"):
        if not note_missing(["ffmpeg", "ffprobe"], "to find the slides in the video"):
            if args.sections == "video":
                sys.exit("error: --sections video needs ffmpeg and ffprobe")
        else:
            per = [detect_scenes(rec, outdir, st, args) for rec, st in zip(recs, stems)]
            slides, timelines = visual_slides(per, recs, outdir, fstem, args, speech_lang)
            pins = [segment_slide(s, timelines[s["rec"]], args.video_fps) if slides and timelines[s["rec"]] else None for s in segs]
            if (len(slides) >= 2 or (slides and args.sections == "video")) and any(p is not None for p in pins):
                path = align_pinned(build_scores(segs, slides), pins, args.switch_penalty, True)
                return slides, path, "video"
            if args.sections == "video":
                sys.exit("error: no distinct screens found in the video (try a lower --scene-threshold)")
            print("note: the video does not show distinct screens; cutting the speech into parts by time instead")
    slides, path = time_sections(segs, args.chunk_minutes)
    return slides, path, "time"


# ----------------------------------------------------------------------------------------------- transcription and PDF
def source_fingerprint(path: Path) -> dict | None:
    """What identifies a recording in the caches: its size in bytes (not its date, which a copy changes). A new export
    with the same name nearly always differs in size."""
    try:
        return {"size": path.stat().st_size}
    except OSError:
        return None


def meta_path(json_path: Path) -> Path:
    """The record kept next to a Whisper JSON: <stem>.json -> <stem>.meta.json."""
    return json_path.with_suffix(".meta.json")


def write_meta(json_path: Path, meta: dict) -> None:
    meta_path(json_path).write_text(json.dumps(meta), encoding="utf-8")


def stale_reason(json_path: Path, recording: Path, model: str | None, language: str | None, switch: str | None = None) -> str | None:
    """Why a saved Whisper JSON should not be reused for this run (None: it can be). It is judged by the record written
    next to it; a JSON made before records existed has none and is trusted, as before. The engine does not matter:
    both engines write the same JSON."""
    try:
        meta = json.loads(meta_path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("source") and meta["source"] != source_fingerprint(recording):
        return f"{recording.name} is not the file that was transcribed"
    if model and meta.get("model") and meta["model"] != model:
        return f"it was made with the model '{meta['model']}', not '{model}'"
    if language and meta.get("language") and meta["language"] != language:
        return f"it was transcribed as '{meta['language']}', not '{language}'"
    if meta.get("language_switch") != switch:
        return f"it was made with --language-switch {meta.get('language_switch') or 'off'}, not {switch or 'off'}"
    return None


# ----------------------------------------------------------------------------------------------- speech that changes language
# Whisper picks ONE language per file (from its first 30 seconds), so a recording that starts in Portuguese and goes on in
# English comes out wrong in one of them. --language-switch cuts the audio where the language changes and transcribes each
# part in its own language: at the times given ("pt 04:30 en"), or at the places the script finds ("auto").
LANG_CODE = re.compile(r"[a-z]{2,3}")


def parse_clock(tok: str) -> float | None:
    """'04:30' = 4 min 30 s (minutes:seconds), '1:04:30' or '01:04:30' = hours:minutes:seconds, '270s' = seconds."""
    m = re.fullmatch(r"(\d+):([0-5]\d)", tok)
    if m:
        return int(m[1]) * 60 + int(m[2])
    m = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d)", tok)
    if m:
        return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
    m = re.fullmatch(r"(\d+(?:\.\d+)?)s", tok)
    return float(m[1]) if m else None


def normalize_switch(spec: str | None) -> str | None:
    """The --language-switch text as the caches record it: lower case, single spaces; None if there is none."""
    toks = [t for t in re.split(r"[\s,;]+", (spec or "").strip().lower()) if t]
    return " ".join(toks) if toks else None


def parse_language_switch(spec: str | None) -> tuple[str, list[dict] | None]:
    """--language-switch: ('none', None); ('auto', None); or ('fixed', plan) for 'pt 04:30 en' (Portuguese until 4 min 30 s,
    then English; 'pt 04:30 en 1:10:00 pt' has three parts), where plan = [{"language": "pt", "start": 0.0}, {"language":
    "en", "start": 270.0}]. Raises ValueError for anything else."""
    key = normalize_switch(spec)
    if not key:
        return "none", None
    toks = key.split()
    if toks == ["auto"]:
        return "auto", None
    if len(toks) < 3 or len(toks) % 2 == 0 or not all(LANG_CODE.fullmatch(t) for t in toks[0::2]):
        raise ValueError(f"--language-switch must be auto or a plan like \"pt 04:30 en\" (a language, then a time and the next "
                         f"language, ...), not {spec!r}")
    plan = [{"language": toks[0], "start": 0.0}]
    for when, lang in zip(toks[1::2], toks[2::2]):
        t = parse_clock(when)
        if t is None or t <= plan[-1]["start"]:
            raise ValueError(f"--language-switch: {when!r} is not a time (m:ss, h:mm:ss or 270s) later than the one before it")
        plan.append({"language": lang, "start": t})
    return "fixed", plan


def describe_plan(plan: list[dict]) -> str:
    return ", then ".join(f"{p['language']} from {hms(p['start'])}" for p in plan)


def switch_plan_of(json_path: Path) -> list[dict] | None:
    """The language plan recorded in a transcription made with --language-switch, or None."""
    try:
        plan = json.loads(json_path.read_text(encoding="utf-8")).get("language_switch")
    except (OSError, ValueError):
        return None
    return plan if isinstance(plan, list) and len(plan) > 1 else None


# language detection by Whisper's own language classifier, in the Python of the engine (like MLX_DRIVER); it reads windows of the 16 kHz
# wav directly, so nothing is cut into files.
LANG_DRIVER = r"""
import json, sys, wave
import numpy as np
a = json.loads(sys.argv[1])
wf = wave.open(a["wav"], "rb")
rate, total = wf.getframerate(), wf.getnframes()
def chunk(t, w):
    wf.setpos(min(int(t * rate), total))
    return np.frombuffer(wf.readframes(int(w * rate)), dtype=np.int16).astype(np.float32) / 32768.0
out = []
if a["engine"] == "mlx":
    import mlx.core as mx
    from mlx_whisper import load_models
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    model = load_models.load_model(a["model"], dtype=mx.float16)
    def probs_of(x):
        mel = log_mel_spectrogram(x, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        return model.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))[1]
else:
    import torch, whisper
    from whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    model = whisper.load_model(a["model"])
    dtype = torch.float16 if model.device.type == "cuda" else torch.float32
    def probs_of(x):
        mel = log_mel_spectrogram(x, model.dims.n_mels, padding=N_SAMPLES)
        return model.detect_language(pad_or_trim(mel, N_FRAMES).to(model.device).to(dtype))[1]
for t, w in a["jobs"]:
    x = chunk(t, w)
    if len(x) < rate:  # less than a second of audio
        out.append(None)
        continue
    probs = probs_of(x)
    lang = max(probs, key=probs.get)
    out.append([lang, float(probs[lang])])
print(json.dumps(out))
"""


def detection_engine(args) -> tuple[str, str]:
    """(engine, model) that detect the language: the engine of the run if it is a Whisper one; with MacWhisper, mlx-whisper
    or openai-whisper if installed."""
    if args.engine in ("mlx", "openai"):
        engine = args.engine
    elif sys.platform == "darwin" and platform.machine() == "arm64" and ENV.get("mlx_whisper"):
        engine = "mlx"
    elif ENV.get("whisper"):
        engine = "openai"
    else:
        sys.exit("error: --language-switch auto checks the language with Whisper, which needs mlx-whisper or openai-whisper "
                 f"({PY} lecture2notes.py --setup --engine openai); or give the times yourself, as in --language-switch \"pt 04:30 en\"")
    return engine, ("turbo" if ":" in args.model else args.model)  # a MacWhisper model id is no Whisper model


def probe_languages(wav: Path, jobs: list[tuple[float, float]], engine: str, model: str) -> list[str | None]:
    """The language of the speech in each (start, length) window of the wav, or None where it is unsure (silence, music)."""
    exe = tool("mlx_whisper" if engine == "mlx" else "whisper")
    payload = json.dumps({"engine": engine, "wav": str(wav), "jobs": jobs,
                          "model": asr_model(argparse.Namespace(engine=engine), model)})
    r = subprocess.run([_venv_python(exe), "-c", LANG_DRIVER, payload], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit("error: could not detect the language with " + engine + " whisper:\n" + (r.stderr or r.stdout).strip()[-600:])
    return [item[0] if item and item[1] >= 0.4 else None for item in json.loads(r.stdout.strip().splitlines()[-1])]


def find_language_switches(detect, duration: float, step: float = 60.0, window: float = 30.0, fine_step: float = 5.0,
                           fine_window: float = 10.0, min_samples: int = 2) -> list[dict]:
    """Where the speech changes language: [{"language", "start"}], the first at 0. `detect(jobs)` gives the language (or None)
    of each (start, length) window. The language is checked in a window of `window` s every `step` s; a language that holds for
    fewer than `min_samples` windows in a row (a quoted sentence) is not a switch; each change is then placed within `fine_step` s
    by checking shorter windows around it."""
    starts = [i * step for i in range(int(max(duration - 10.0, 0.0) // step) + 1)]
    labels = list(detect([(t, window) for t in starts]))
    for order in (range(len(labels)), range(len(labels) - 1, -1, -1)):  # a window with no answer takes its neighbour's language
        seen = None
        for i in order:
            if labels[i] is None:
                labels[i] = seen
            else:
                seen = labels[i]
    if labels[0] is None:
        return []

    def runs_of(lbls):
        runs: list[list] = []
        for i, lang in enumerate(lbls):
            if runs and runs[-1][0] == lang:
                runs[-1][2] = i
            else:
                runs.append([lang, i, i])
        return runs

    runs = runs_of(labels)
    size = lambda k: runs[k][2] - runs[k][1] + 1
    while len(runs) > 1:
        shorts = [k for k in range(len(runs)) if size(k) < min_samples]
        if not shorts:
            break
        neighbour = lambda k: max((j for j in (k - 1, k + 1) if 0 <= j < len(runs)), key=size)  # the longer of the runs next to it
        short = min(shorts, key=lambda k: (size(k), -size(neighbour(k))))  # the shortest first, then the one with the surest neighbour
        for i in range(runs[short][1], runs[short][2] + 1):
            labels[i] = runs[neighbour(short)][0]
        runs = runs_of(labels)
    plan = [{"language": runs[0][0], "start": 0.0}]
    for before, after in zip(runs, runs[1:]):
        ta, tb = starts[before[2]], starts[after[1]]  # the last window in the old language, the first in the new one
        fine, t = [], max(0.0, ta + window / 2 - fine_window)
        while t <= tb + window / 2:
            fine.append(t)
            t += fine_step
        found = detect([(t, fine_window) for t in fine])
        first = len(fine)  # the first window from which all the rest are in the new language
        for i in range(len(fine) - 1, -1, -1):
            if found[i] in (None, after[0]):
                first = i
            else:
                break
        at = tb if first == len(fine) else fine[first] + (fine_step / 2 if first > 0 else 0.0)
        plan.append({"language": after[0], "start": at})
    return plan


def snap_to_pause(wav: Path, estimate: float, reach: float = 10.0) -> float:
    """Move a cut to the middle of the pause nearest to it (within `reach` seconds), so that no word is cut in two."""
    t0 = max(0.0, estimate - reach)
    r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-nostats", "-ss", f"{t0:.3f}", "-t", f"{2 * reach:.3f}", "-i", str(wav), "-af",
                        "silencedetect=noise=-35dB:d=0.35", "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    pauses, begin = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            begin = max(0.0, float(m[1]))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and begin is not None:
            pauses.append(t0 + (begin + float(m[1])) / 2)
            begin = None
    return min(pauses, key=lambda p: abs(p - estimate)) if pauses else estimate


def auto_language_plan(wav: Path, args) -> list[dict]:
    """--language-switch auto: look for the language changes in the audio (see find_language_switches), and put each cut at a pause."""
    engine, model = detection_engine(args)
    duration = wav.stat().st_size / 32000.0
    print(f"checking the language of the speech in {wav.name} ({hms(duration)}), one window every minute, with {engine} whisper "
          f"(model {model}) ...")
    plan = find_language_switches(lambda jobs: probe_languages(wav, jobs, engine, model), duration)
    if not plan:
        sys.exit("error: could not tell the language of the speech (no clear speech?): give --language, or the times yourself, "
                 "as in --language-switch \"pt 04:30 en\"")
    for p in plan[1:]:
        p["start"] = round(snap_to_pause(wav, p["start"]), 2)
    return plan


def shift_segments(segs: list[dict], offset: float) -> list[dict]:
    """The Whisper segments (and their words) moved `offset` seconds later."""
    out = []
    for s in segs:
        s = dict(s, start=float(s["start"]) + offset, end=float(s["end"]) + offset)
        if s.get("words"):
            s["words"] = [dict(w, start=float(w["start"]) + offset, end=float(w["end"]) + offset) for w in s["words"]]
        out.append(s)
    return out


def transcribe_in_languages(wav: Path, out_json: Path, args, plan: list[dict], workdir: Path, model: str, task: str | None = None) -> None:
    """Transcribe (or translate) `wav` in pieces, each in the language of the plan, and write one Whisper-style JSON with the
    segments of all the pieces at their times in the recording, the plan in "language_switch" and its first language in "language"."""
    pieces = [(p["language"], p["start"], plan[i + 1]["start"] if i + 1 < len(plan) else None) for i, p in enumerate(plan)]
    tmp = workdir / f".{wav.stem}.pieces"
    tmp.mkdir(exist_ok=True)
    segs: list[dict] = []
    try:
        for k, (lang, start, end) in enumerate(pieces, 1):
            piece = tmp / f"{wav.stem}.piece{k}.wav"
            cut = [tool("ffmpeg"), "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(wav)]
            subprocess.run(cut + (["-t", f"{end - start:.3f}"] if end else []) + ["-c", "copy", str(piece)], check=True)
            print(f"part {k} of {len(pieces)}: {lang}, from {hms(start)}" + (f" to {hms(end)}" if end else " to the end"))
            result = tmp / f"{piece.stem}.json"
            if args.engine == "macwhisper":
                macwhisper_transcribe(piece, result, argparse.Namespace(**{**vars(args), "language": lang}))
            else:
                cmd = asr_command(args, piece, tmp, model, lang, task)
                print(("translating" if task else "transcribing") + f" with {args.engine} whisper (this takes a while):", show_command(cmd))
                subprocess.run(cmd, check=True)
            segs += shift_segments(json.loads(result.read_text(encoding="utf-8")).get("segments", []), start)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for i, sg in enumerate(segs):
        sg["id"] = i
    out_json.write_text(json.dumps({"text": " ".join(sg["text"].strip() for sg in segs), "language": plan[0]["language"],
                                    "language_switch": plan, "segments": segs}, ensure_ascii=False), encoding="utf-8")


SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def parse_srt(text: str) -> list[dict]:
    """Segments {start, end, text} (seconds) from SRT (or WebVTT) text."""
    secs = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0
    segs = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        for k, ln in enumerate(lines):
            m = SRT_TIME.search(ln)
            if m:
                body = " ".join(x.strip() for x in lines[k + 1:] if x.strip())
                if body:
                    segs.append({"start": secs(*m.groups()[:4]), "end": secs(*m.groups()[4:]), "text": body})
                break
    return segs


def parse_mw_json(text: str, audio_seconds: float) -> list[dict]:
    """Segments from the JSON of `mw transcribe --format json`: {"segments": [{"start", "end", "text"}]}, in milliseconds
    according to MacWhisper's documentation. If the numbers are not larger than the audio is long, they are seconds."""
    raw = json.loads(text).get("segments", [])
    last = max((float(s["end"]) for s in raw), default=0.0)
    scale = 1.0 if audio_seconds and last <= 1.5 * audio_seconds else 1000.0
    return [{"start": float(s["start"]) / scale, "end": float(s["end"]) / scale, "text": s["text"].strip()}
            for s in raw if s.get("text", "").strip()]


def macwhisper_transcribe(wav: Path, out_json: Path, args) -> None:
    """Transcribe `wav` with MacWhisper's `mw` and write a Whisper-style JSON ({"language", "segments"}) to out_json. JSON
    output needs MacWhisper Pro; without it the free SRT output is used (same segments and times)."""
    audio_seconds = wav.stat().st_size / 32000.0  # 16 kHz, mono, 16 bits
    segs: list[dict] = []
    for fmt in ("json", "srt"):
        cmd = [tool("macwhisper"), "transcribe", str(wav), "--format", fmt, "--no-speakers"]
        if args.language:
            cmd += ["--language", args.language]
        if ":" in args.model:  # a MacWhisper model id, provider:name; otherwise the model selected in the app
            cmd += ["--model", args.model]
        print(f"transcribing with MacWhisper (this takes a while): {show_command(cmd)}")
        r = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            try:
                segs = parse_mw_json(r.stdout, audio_seconds) if fmt == "json" else parse_srt(r.stdout)
            except (ValueError, KeyError, TypeError):
                segs = []
        if segs:
            break
        if fmt == "json":
            print("note: MacWhisper gave no JSON (that format needs MacWhisper Pro): asking for SRT, which is free")
    if not segs:
        sys.exit("error: MacWhisper (mw) did not give a transcription (see above: is a model downloaded in the app?)")
    out_json.write_text(json.dumps({"text": " ".join(s["text"] for s in segs), "language": args.language,
                                    "segments": [dict(s, id=i) for i, s in enumerate(segs)]}, ensure_ascii=False), encoding="utf-8")


def transcribe(recording: Path, outdir: Path, args, stem: str, given_json: str | None = None) -> Path:
    """Whisper JSON of one recording (`stem` names the files in outdir, unique among the recordings)."""
    if given_json:
        if not Path(given_json).is_file():
            sys.exit(f"error: --whisper-json {given_json} not found")
        return Path(given_json)
    out_json = outdir / f"{stem}.json"
    if out_json.exists() and not args.force:
        why = stale_reason(out_json, recording, model_key(args), spoken_language(args), normalize_switch(args.language_switch))
        if not why:
            print(f"using the existing transcription {out_json} (--force to redo it)")
            return out_json
        print(f"note: the existing transcription {out_json.name} is out of date ({why}): transcribing again")
    # the same recording may have been transcribed alone before, into its own folder: reuse that
    alone = recording.parent / f"{recording.stem} notes" / f"{safe_stem(recording.stem, 'recording')}.json"
    if alone.exists() and alone.resolve() != out_json.resolve() and not args.force:
        why = stale_reason(alone, recording, model_key(args), spoken_language(args), normalize_switch(args.language_switch))
        if not why:
            print(f"using the existing transcription {alone} (--force to redo it)")
            shutil.copyfile(alone, out_json)
            if meta_path(alone).exists():
                shutil.copyfile(meta_path(alone), meta_path(out_json))
            return out_json
        print(f"note: the transcription {alone} is out of date ({why}), not used")
    ffmpeg = tool("ffmpeg")
    wav = outdir / f"{stem}.wav"
    print(f"extracting the audio of {recording.name} ...")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(recording), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
    try:
        mode, plan = parse_language_switch(args.language_switch)
        if mode != "none":  # the speech changes language: each part in its own
            if mode == "auto":
                plan = auto_language_plan(wav, args)
            print("language of the speech: " + describe_plan(plan))
            if len(plan) == 1:
                print("note: no change of language was found; the whole recording is transcribed as " + plan[0]["language"])
            transcribe_in_languages(wav, out_json, args, plan, outdir, args.model)
        elif args.engine == "macwhisper":
            if args.whisper_args:
                print("note: --whisper-args, --keep-context and --silence-threshold are for Whisper itself: MacWhisper ignores them")
            macwhisper_transcribe(wav, out_json, args)
        else:
            cmd = asr_command(args, wav, outdir, args.model, args.language)
            print(f"transcribing with {args.engine} whisper (this takes a while):", show_command(cmd))
            subprocess.run(cmd, check=True)
    finally:
        if not args.keep_audio:
            wav.unlink(missing_ok=True)
    if not out_json.exists():
        sys.exit(f"error: {args.engine} whisper did not write {out_json} (check --whisper-args)")
    try:
        spoken = json.loads(out_json.read_text(encoding="utf-8")).get("language")
    except (OSError, ValueError):
        spoken = None
    write_meta(out_json, {"source": source_fingerprint(recording), "model": model_key(args), "language": spoken, "engine": args.engine,
                          "language_switch": normalize_switch(args.language_switch)})
    return out_json


def spoken_language(args) -> str | None:
    """--language for the cache check: with --language-switch each part has its own language, so there is none."""
    return None if args.language_switch else args.language


def translation_args(args):
    """The settings for the translation to English. MacWhisper cannot translate, so then mlx-whisper (Apple Silicon) or
    openai-whisper does it, if installed. None if nothing can."""
    if args.engine != "macwhisper":
        return args
    for engine, name in (("mlx", "mlx_whisper"), ("openai", "whisper")):
        if ENV.get(name) and (engine != "mlx" or platform.machine() == "arm64"):
            return argparse.Namespace(**{**vars(args), "engine": engine})
    return None


def translate_model_name(args) -> str:
    """Whisper model for the translation: --translate-model, else medium (turbo is not trained to translate; and a
    MacWhisper model id is no Whisper model name), else --model."""
    return args.translate_model or ("medium" if args.engine == "macwhisper" or args.model in ("turbo", "large-v3-turbo") else args.model)


def translate_recording(recording: Path, outdir: Path, args, stem: str, lang: str | None) -> list[dict] | None:
    """English translation of the speech (whisper --task translate), cached in <stem>.en-<model>.json; only for
    matching words. None if it did not come out in English."""
    model = translate_model_name(args)
    out_json = outdir / f"{stem}.en-{safe_stem(model, 'model')}.json"
    why = stale_reason(out_json, recording, None, None) if out_json.exists() and not args.force else None
    if why:
        print(f"note: the existing translation {out_json.name} is out of date ({why}): translating again")
    if out_json.exists() and not args.force and not why:
        print(f"using the existing translation {out_json.name}")
    else:
        targs = translation_args(args)
        if targs is None:
            print("warning: MacWhisper cannot translate, and neither mlx-whisper nor openai-whisper is installed, so the speech "
                  f"cannot be matched to English slides by its words. Install one ({PY} lecture2notes.py --setup --engine openai, "
                  "or mlx-whisper on Apple Silicon), or rely on the slide numbers in the video.")
            return None
        args = targs  # the settings of the engine that translates
        require(["ffmpeg", asr_tool(args)], "to translate the speech")
        if not model_cached(args, model)[0]:
            print(f"note: the whisper model '{asr_model(args, model)}' is downloaded on first use (turbo is not trained to "
                  "translate; --translate-model chooses another)")
        tmp = outdir / f".{stem}.translate"
        tmp.mkdir(exist_ok=True)
        try:
            wav = tmp / f"{stem}.wav"
            print(f"extracting the audio of {recording.name} again, to translate it ...")
            subprocess.run([tool("ffmpeg"), "-y", "-loglevel", "error", "-i", str(recording), "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", str(wav)], check=True)
            plan = switch_plan_of(outdir / f"{stem}.json")  # the speech changes language: translate each part in its own
            if plan:
                transcribe_in_languages(wav, tmp / f"{stem}.json", args, plan, tmp, model, task="translate")
            else:
                cmd = asr_command(args, wav, tmp, model, args.language or lang, task="translate")
                print(f"translating with {args.engine} whisper (this takes a while):", show_command(cmd))
                subprocess.run(cmd, check=True)
            shutil.move(str(tmp / f"{stem}.json"), out_json)
            write_meta(out_json, {"source": source_fingerprint(recording), "model": model})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    data = json.loads(out_json.read_text(encoding="utf-8"))
    tsegs = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip(),
              "compression_ratio": s.get("compression_ratio", 0)} for s in data.get("segments", [])]
    tsegs = drop_repetitions(tsegs)[0]
    got = detect_language(" ".join(s["text"] for s in tsegs))
    if got and got != "en":
        print(f"warning: the translation by '{model}' came out in {lang_name(got)}, not English, so it is not used; "
              "try another --translate-model (e.g. medium or large)")
        return None
    return tsegs


def to_pdf(md: Path, args, toc: bool) -> bool:
    if not note_missing(["pandoc"], "to write the PDF files (the Markdown files are written anyway)"):
        return False
    engine = args.pdf_engine or next((e for e in ("xelatex", "lualatex", "pdflatex") if shutil.which(e)), None)
    if engine is None or not shutil.which(engine):
        ENV["latex"] = None
        note_missing(["latex"], "to write the PDF files (the Markdown files are written anyway)")
        return False
    cmd = [ENV["pandoc"], str(md), "-o", str(md.with_suffix(".pdf")), "--pdf-engine", engine, "--resource-path", str(md.parent),
           "-V", "geometry:margin=2.2cm", "-V", "fontsize=11pt", "-V", "colorlinks=true"]
    if toc:
        cmd += ["--toc", "--toc-depth=2"]
    if args.mainfont and engine in ("xelatex", "lualatex"):
        cmd += ["-V", f"mainfont={args.mainfont}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"warning: pandoc failed for {md.name}:\n{r.stderr.strip()[-800:]}")
        return False
    return True


# ----------------------------------------------------------------------------------------------- output
def yaml_header(title: str, subtitle: str) -> str:
    q = lambda s: json.dumps(md_escape(s), ensure_ascii=False)  # JSON strings are valid YAML strings
    return f"---\ntitle: {q(title)}\nsubtitle: {q(subtitle)}\ndate: {q(datetime.date.today().isoformat())}\n---\n\n"


def tlabel(seg: dict, t: float, multi_rec: bool) -> str:
    """Timestamp text: 00:12:03, or P2 00:12:03 (time inside the 2nd recording) when there are several recordings."""
    return f"P{seg['rec'] + 1} {hms(t)}" if multi_rec else hms(t)


def paragraphs(segs: list[dict], gap: float = 2.0, maxlen: int = 700) -> list[tuple[dict, str, int]]:
    """Group segments into readable paragraphs: (first segment, text, slide index of the first segment)."""
    out, cur, first, last = [], "", None, None
    for s in segs:
        if cur and (s["rec"] != last["rec"] or s["start"] - last["end"] > gap or len(cur) > maxlen or s["slide"] != first["slide"]):
            out.append((first, cur.strip(), first["slide"]))
            cur = ""
        if not cur:
            first = s
        cur += " " + s["text"].strip()
        last = s
    if cur.strip():
        out.append((first, cur.strip(), first["slide"]))
    return out


def sources_line(decks: list[Path], recs: list[Path], mode: str) -> str:
    rec = ("recording: " if len(recs) == 1 else "recordings (in this order): ") + ", ".join(r.name for r in recs)
    if decks:
        return ("Slides: " if len(decks) == 1 else "Slides (in this order): ") + ", ".join(d.name for d in decks) + "; " + rec
    how = "sections follow the screens shown in the video" if mode == "video" else "sections are parts of the speech, cut by time"
    return rec[0].upper() + rec[1:] + f" (no slides given: {how})"


def write_transcript(path: Path, segs, slides, name: str, decks: list[Path], recs: list[Path], mode: str) -> None:
    multi_rec, multi_deck = len(recs) > 1, len(decks) > 1
    lines = [yaml_header(f"Transcript: {name}", sources_line(decks, recs, mode))]
    last_slide = None
    for first, text, sl in paragraphs(segs):
        if sl != last_slide:
            t = slides[sl]["title"] or "(no title)"
            lines.append(f"\n> **{md_escape(slide_ref(slides[sl], multi_deck))}** — {md_escape(t)}\n\n")
            last_slide = sl
        lines.append(f"**[{tlabel(first, first['start'], multi_rec)}]** {md_escape(text)}\n\n")
    path.write_text("".join(lines), encoding="utf-8")


DISCLAIMER = {
    "pptx": "*Generated automatically from the slides and the transcription of the lecture. The assignment of the speech to "
            "the slides is approximate; check it against the timestamps.*\n\n",
    "video": "*Generated automatically from the recording, without the slides: each section is a distinct screen of the "
             "video (its picture, the text read on it by OCR, and what was said while it was shown). OCR text may contain "
             "errors; check it against the picture.*\n\n",
    "time": "*Generated automatically from the transcription of the recording, without slides: the lecture is cut into "
            "parts of a few minutes; the titles are the most distinctive words of each part.*\n\n",
}


def write_notes(path: Path, segs, slides, name: str, decks: list[Path], recs: list[Path], key_points: int, mode: str,
                extra: str = "") -> None:
    multi_rec, multi_deck = len(recs) > 1, len(decks) > 1
    by_slide: dict[int, list[dict]] = {i: [] for i in range(len(slides))}
    for s in segs:
        by_slide[s["slide"]].append(s)
    idf_texts = [slide_text(s) for s in slides]
    if not any(tokens(t) for t in idf_texts):  # nothing written on the slides: rarity across what was said instead
        idf_texts = [" ".join(s["text"] for s in by_slide[i]) for i in range(len(slides))]
    out = [yaml_header(f"Reading notes: {name}", sources_line(decks, recs, mode)), DISCLAIMER["pptx" if decks else mode] + extra]
    for i, sl in enumerate(slides):
        ss = by_slide[i]
        span = ""
        if ss:
            visits, cur = [], [ss[0]]
            for a, b in zip(ss, ss[1:]):
                if b["idx"] == a["idx"] + 1:
                    cur.append(b)
                else:
                    visits.append(cur)
                    cur = [b]
            visits.append(cur)
            txt = "; ".join(f"{tlabel(v[0], v[0]['start'], multi_rec)} – {tlabel(v[-1], v[-1]['end'], multi_rec)}" for v in visits[:3])
            if len(visits) > 3:
                txt += f"; +{len(visits) - 3} more visits"
            span = f" `[{txt}]`"
        out.append(f"\n## {md_escape(slide_ref(sl, multi_deck))}: {md_escape(sl['title'] or '(no title)')}{span}\n\n")
        if sl.get("image"):
            out.append(f"![]({sl['image']}){{width=85%}}\n\n")
        if sl["items"]:
            out.append("**On the slide**\n\n" if sl["kind"] in ("pptx", "pdf") else f"**On the screen** (read by OCR at {sl.get('picture_at', '?')})\n\n")
            for lvl, t in sl["items"]:
                out.append(f"{'    ' * min(lvl, 4)}- {md_escape(t)}\n")
            out.append("\n")
        if sl["notes"]:
            out.append("**Speaker notes (from the PPTX)**\n\n")
            out.append("\n\n".join(md_escape(t) for t in sl["notes"]) + "\n\n")
        if not ss:
            out.append("*Nothing in the recording was assigned to this slide (skipped, or discussed together with another one).*\n")
            continue
        text = " ".join(s["text"].strip() for s in ss)
        kp = key_sentences(text, idf_texts, key_points)
        if kp:
            out.append("**Key points from the lecture**\n\n")
            for s in kp:
                out.append(f"- {md_escape(s)}\n")
            out.append("\n")
        out.append("**What was said**\n\n")
        out.append("\n>\n".join(f"> **[{tlabel(f, f['start'], multi_rec)}]** {md_escape(ptext)}" for f, ptext, _ in paragraphs(ss)) + "\n\n")
    path.write_text("".join(out).rstrip("\n") + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------------- one lecture
def run_lecture(decks: list[Path], recs: list[Path], outdir: Path, name: str, args) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"output folder: {outdir}")
    if not recs:
        return slides_only_lecture(decks, outdir, name, args)
    stems = [safe_stem(r.stem, "recording") for r in recs]
    if len(set(stems)) != len(stems):  # same file name in different folders
        stems = [f"{i + 1:02d}-{st}" for i, st in enumerate(stems)]
    fstem = safe_stem(name, "lecture")
    if args.notes_only:  # the transcript of an earlier run is the input: nothing else is redone
        tmd = outdir / f"{fstem}.transcript.md"
        if not tmd.exists():
            sys.exit(f"error: --notes-only needs the transcript of an earlier run, and {tmd} does not exist: run once without it")
        print(f"--notes-only: using the transcript of an earlier run, {tmd.name}")
        return student_notes(decks, read_decks(decks) if decks else [], recs, outdir, fstem, name, tmd, args)
    if not args.whisper_json:
        todo = [r for r, st in zip(recs, stems) if args.force or not (outdir / f"{st}.json").exists()
                and not (r.parent / f"{r.stem} notes" / f"{safe_stem(r.stem, 'recording')}.json").exists()]
        if todo:
            require(["ffmpeg", asr_tool(args)], "to transcribe " + ", ".join(r.name for r in todo))

    slides = read_decks(decks) if decks else []
    if decks:
        print(f"{len(slides)} slides read from " + ", ".join(f"{d.name}" for d in decks))
        render_pdf_pages(slides, outdir, fstem)
        if not slides:
            sys.exit("error: the slides have no pages")
    else:
        print("no slides given: the sections will come from the video (or from the time)")
    segs: list[dict] = []
    speech_langs: list[str | None] = []
    for i, (rec, st) in enumerate(zip(recs, stems)):
        jpath = transcribe(rec, outdir, args, st, args.whisper_json[i] if args.whisper_json else None)
        data = json.loads(jpath.read_text(encoding="utf-8"))
        if args.language and data.get("language") and data["language"] != args.language:
            print(f"warning: {jpath.name} was transcribed as '{data['language']}', not '{args.language}' (--force to redo it)")
        part = [{"rec": i, "start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip(),
                 "compression_ratio": s.get("compression_ratio", 0)}
                for s in data.get("segments", []) if s.get("text", "").strip()]
        part, nrem, srem = drop_repetitions(part)
        if nrem:
            total = max(1.0, part[-1]["end"] if part else 1.0)
            print(f"removed {nrem} repeated segments ({hms(srem)} of {rec.name}): traces of whisper repetition loops")
            if srem > 0.05 * total:
                print(f"warning: {100 * srem / total:.0f}% of {rec.name} was lost in repetition loops; the speech there is "
                      "missing from the notes. Transcribe again with --force (the current settings avoid such loops).")
        # the language of the text itself (whisper reports the spoken one also when it translated to English)
        lang = detect_language(" ".join(s["text"] for s in part)) or data.get("language")
        speech_langs.append(lang)
        print(f"{rec.name}: {len(part)} speech segments, {hms(part[-1]['end']) if part else '0'} of speech, in {lang_name(lang)}")
        segs += part
    if not segs:
        sys.exit("error: the transcription is empty")
    REPORT.clear()
    spoken = ", ".join(dict.fromkeys(lang_name(x) for x in speech_langs))
    slide_lang = None
    if slides:
        slide_lang, how = slides_language(slides)
        print(f"languages: speech {spoken}; slides {lang_name(slide_lang)} ({how})")
        print(f"  -> {language_plan(slide_lang, speech_langs, args)}")

    if slides:
        path = align_with_slides(segs, slides, recs, stems, outdir, args, speech_langs, slide_lang)
        mode = "pptx"
    else:
        main_lang = Counter(x for x in speech_langs if x).most_common(1)
        slides, path, mode = sections_without_slides(segs, recs, stems, outdir, fstem, args, main_lang[0][0] if main_lang else None)
    for k, (s, sl) in enumerate(zip(segs, path)):
        s["slide"], s["idx"] = sl, k
    assigned = f"speech assigned to {len(set(path))} of {len(slides)} " + ("parts" if mode == "time" else "slides")
    print(assigned)

    tmd, nmd = outdir / f"{fstem}.transcript.md", outdir / f"{fstem}.notes.md"
    write_transcript(tmd, segs, slides, name, decks, recs, mode)
    extra = ("*The speech was matched to the slides through an English translation made by Whisper, because the slides are "
             "in English and the speech is not; the text below is the original speech.*\n\n") if any("align_text" in s for s in segs) else ""
    write_notes(nmd, segs, slides, name, decks, recs, args.key_points, mode, extra)
    print("wrote", tmd, "and", nmd)
    if args.latex == "yes" and not args.no_pdf:
        for md, toc in ((tmd, False), (nmd, True)):
            if to_pdf(md, args, toc):
                print("wrote", md.with_suffix(".pdf"))
    print("\nsummary")
    print(f"  speech:      {spoken}")
    if decks:
        print(f"  slides:      {len(slides)} from {len(decks)} deck(s), in {lang_name(slide_lang)}")
        print(f"  translation: {REPORT.get('translation', 'not attempted')}")
        print(f"  video:       {REPORT.get('video', '-')}")
    else:
        print("  slides:      none given; sections from the " + ("video" if mode == "video" else "time")
              + (f" (text on the screen in {REPORT['screen']}, read by OCR)" if mode == "video" and REPORT.get("screen") else ""))
    print(f"  result:      {assigned}")
    print(f"  output:      {outdir}")
    if args.student_notes:
        student_notes(decks, slides, recs, outdir, fstem, name, tmd, args)


def slides_only_lecture(decks: list[Path], outdir: Path, name: str, args) -> None:
    """No recording (it failed): reading notes from the slides and the --notes-extra documents alone."""
    fstem = safe_stem(name, "lecture")
    slides = read_decks(decks)
    if not slides:
        sys.exit("error: the slides have no pages")
    print(f"{len(slides)} slides read from " + ", ".join(d.name for d in decks) + "; no recording: notes from the slides alone")
    slide_lang, how = slides_language(slides)
    print(f"languages: slides {lang_name(slide_lang)} ({how})")
    print("\nsummary")
    print(f"  slides:      {len(slides)} from {len(decks)} deck(s), in {lang_name(slide_lang)}")
    print("  recording:   none; no transcript")
    print(f"  readings:    {len(args.notes_extra)} (--notes-extra)" if args.notes_extra else
          "  readings:    none (add the readings with --notes-extra: without a recording, they carry the explanations)")
    print(f"  output:      {outdir}")
    student_notes(decks, slides, [], outdir, fstem, name, None, args)


def align_with_slides(segs, slides, recs, stems, outdir, args, speech_langs: list[str | None], slide_lang: str | None) -> list[int]:
    scores = build_scores(segs, slides)
    text_path = align(scores, args.switch_penalty, args.allow_backward)  # by the words only
    pins: list[int | None] = [None] * len(segs)
    use_video = args.video_sync != "off"
    if use_video and not note_missing(["ffmpeg", "ffprobe", "tesseract", "pillow"], "to read the slide numbers in the video"):
        if args.video_sync == "on":
            require(["ffmpeg", "ffprobe", "tesseract", "pillow"], "--video-sync on")
        print("note: aligning by the words only")
        use_video = False
    if use_video:
        max_shown = max(sl["shown"] for sl in slides)
        debug_rows = []
        for i, (rec, st) in enumerate(zip(recs, stems)):
            d = read_slide_numbers(rec, outdir, st, args, max(sl["shown"] for sl in slides))
            if not d:
                continue
            raw = [x if x is not None and 1 <= x <= max_shown else None for x in d["numbers"]]
            big = [x > args.change_threshold for x in d["diffs"]]
            nums = clean_numbers(raw, big)
            mine = [(s["start"], s["end"], scores[k]) for k, s in enumerate(segs) if s["rec"] == i]

            def evidence(t0, t1, cand, mine=mine):  # how well the speech during [t0, t1) matches slide `cand`
                return sum(row[cand] for a, b, row in mine if a < t1 and b > t0)

            timeline = to_global(nums, slides, d["fps"], evidence)
            for k, s in enumerate(segs):
                if s["rec"] == i:
                    pins[k] = segment_slide(s, timeline, d["fps"])
            if args.video_debug:
                debug_rows += [(i + 1, f"{t / d['fps']:.1f}", raw[t] or "", nums[t] or "", timeline[t] + 1 if timeline[t] is not None else "", int(big[t]))
                               for t in range(len(nums))]
    scores, text_path = cross_language(segs, slides, recs, stems, outdir, args, speech_langs, slide_lang, pins, scores, text_path)
    discarded = 0  # segments the video placed, when its readings were judged to be misreadings and dropped
    if use_video:
        known = [k for k, v in enumerate(pins) if v is not None]
        if known:
            exact = sum(text_path[k] == pins[k] for k in known)
            near = sum(abs(text_path[k] - pins[k]) <= 1 for k in known)
            if args.video_sync == "auto" and len(known) < 0.25 * len(segs) and near < 0.2 * len(known):
                print(f"note: the slide numbers read from the video place only {len(known)} of {len(segs)} segments and almost never "
                      "agree with the words; they look like misreadings, so the words alone are used (--video-sync on to keep them)")
                pins, discarded = [None] * len(segs), len(known)
            print(f"slide known from the video for {len(known)} of {len(segs)} segments; the words alone point to the same slide for "
                  f"{100 * exact // len(known)}% of them ({100 * near // len(known)}% within one slide; low values mean the lecture went back and forth)")
        else:
            print("note: no slide number could be read from the video; aligning by the words only")
        if args.video_debug:
            dbg = outdir / f"{stems[0]}.slides.csv"
            dbg.write_text("recording,time_s,number_read,number_cleaned,slide,big_change\n" + "\n".join(",".join(map(str, r)) for r in debug_rows) + "\n")
            print("wrote", dbg)
    placed = sum(p is not None for p in pins)
    if not use_video:
        REPORT["video"] = "slide numbers in the video not used (matching by words only)"
    elif discarded:
        REPORT["video"] = (f"slide numbers read from the video, but ignored: they placed only {discarded} of {len(segs)} "
                           "speech segments and rarely agreed with the words (matching by words only)")
    elif placed:
        REPORT["video"] = f"slide numbers read from the video place {placed} of {len(segs)} speech segments"
    else:
        REPORT["video"] = "no slide number could be read from the video (matching by words only)"
    return align_pinned(scores, pins, args.switch_penalty, args.allow_backward)


REPORT: dict = {}  # what happened in the current lecture, for the summary printed at the end


def slides_language(slides: list[dict]) -> tuple[str | None, str]:
    """(language of the slides, how it was found): from their words or, with too little text, from the proofing
    language PowerPoint stores on each piece of text."""
    lang = detect_language(" ".join(" ".join([s["title"]] + [t for _, t in s["items"]] + s["notes"]) for s in slides))
    if lang:
        return lang, "from the text"
    tagged = Counter()
    for s in slides:
        tagged.update(s.get("tagged", {}))
    if tagged and tagged.most_common(1)[0][1] >= 0.7 * sum(tagged.values()):
        return tagged.most_common(1)[0][0], "from the language set in the PPTX"
    return None, "too little text to tell"


def language_plan(slide_lang: str | None, speech_langs: list[str | None], args) -> str:
    """One line saying whether a translation will be needed, printed before the long steps."""
    spoken = sorted({x for x in speech_langs if x})
    if not slide_lang or not spoken:
        return "the languages cannot be compared; the words are matched as they are"
    if spoken == [slide_lang]:
        return "same language: the words of the speech are matched directly to the slides"
    if args.cross_language == "off":
        return "different languages, but translation is off (--cross-language off)"
    if slide_lang != "en":
        return ("different languages, and whisper only translates into English: the slide numbers in the video will have to "
                "place the speech")
    if args.cross_language == "translate":
        return "different languages: an English translation of the speech will be made (--cross-language translate)"
    return ("different languages: an English translation of the speech will be made if the slide numbers in the video "
            "do not place at least 90% of it")


def cross_language(segs, slides, recs, stems, outdir, args, speech_langs, slide_lang, pins, scores, text_path):
    """Slides and speech in different languages share few words. If the slides are in English, match an English
    translation of the speech (whisper --task translate) instead, when the slide numbers of the video do not already
    place (almost) all of the speech. Returns the (possibly new) scores and word-only path."""
    other = [i for i, lang in enumerate(speech_langs) if lang and slide_lang and lang != slide_lang]
    if not other:
        REPORT["translation"] = "not needed (same language)" if slide_lang else "not attempted (slide language unknown)"
        return scores, text_path
    if args.cross_language == "off":
        REPORT["translation"] = "off (--cross-language off)"
        return scores, text_path
    spoken = ", ".join(sorted({lang_name(speech_langs[i]) for i in other}))
    if slide_lang != "en":
        print(f"note: the slides are in {lang_name(slide_lang)} and the speech in {spoken}; whisper can only translate into "
              "English, so the words cannot be matched across the languages. The slide numbers shown in the video are what "
              "places the speech here (make sure the slides show their numbers).")
        REPORT["translation"] = f"not possible (slides in {lang_name(slide_lang)}; whisper only translates into English)"
        return scores, text_path
    concerned = [k for k, s in enumerate(segs) if s["rec"] in other]
    if not concerned:  # the recordings in another language have no speech: nothing to translate
        REPORT["translation"] = "not needed (no speech in the recordings in another language)"
        return scores, text_path
    unplaced = sum(pins[k] is None for k in concerned) / len(concerned)
    if args.cross_language == "auto" and unplaced <= 0.1:
        placed = 100 - round(100 * unplaced)
        print(f"translation: not needed, the video places {placed}% of the speech (--cross-language translate to make one anyway)")
        REPORT["translation"] = f"not needed (the slide numbers in the video place {placed}% of the speech)"
        return scores, text_path
    print(f"translation: the slides are in English and the speech in {spoken}; matching an English translation of the speech "
          "to the slides (the transcript and the notes keep the original speech)")
    done = []
    for i in other:
        tsegs = translate_recording(recs[i], outdir, args, stems[i], speech_langs[i])
        if tsegs is None:
            continue
        done.append(recs[i].name)
        for s in segs:
            if s["rec"] == i:
                s["align_text"] = " ".join(t["text"] for t in tsegs if t["start"] < s["end"] and t["end"] > s["start"])
    model = translate_model_name(args)
    REPORT["translation"] = (f"used for {', '.join(done)} (model {model})" if done
                             else "attempted but failed (see the warning above)")
    scores = build_scores(segs, slides)
    return scores, align(scores, args.switch_penalty, args.allow_backward)


# ----------------------------------------------------------------------------------------------- student notes (Claude)
# --student-notes hands the transcript and the slides to Claude Code (the `claude` command line), with a prompt that
# describes how to write reading notes for the students: content checked against the recording, boxes, figures drawn
# from the slide data, review questions, glossary, and a PDF and DOCX built with the kit written below.

STUDENT_NOTES_PROMPT = r"""# Task: reading notes for the students of a recorded lecture

Prepare high-quality reading notes for the students of a recorded lecture, from its automatic transcription and its
slides. They must be excellent in content (faithful to what was taught, clear, complete, useful for revision and for
the course work) and visually appealing. Write them in neutral Pandoc Markdown that the kit in `build/` turns into a
typographically good PDF (via LaTeX) and a DOCX.

## Context

- Lecture: $lecture
- Language of the notes: **$language**. Keep technical terms in the form the field uses (often English).
- Context from the lecturer: $context

## Inputs (read them; do not modify them)

- **Transcript**: `$transcript`. Paragraphs start with timestamps such as **[00:12:34]**, or **[P2 00:12:34]** when the
  lecture was recorded in several parts. Lines like `> **…, slide 12** — title` mark the slide on screen; they were
  aligned automatically and are often, not always, right. The transcription is automatic (Whisper): expect
  misrecognised names and technical terms, and hallucinations over silences (repeated "Thank you", dots, loops).
- **Slides**: `inputs/slides.md`: the text of every slide, the speaker notes, and the data behind PPTX charts.
  Speaker notes are the lecturer's private notes: use them to understand the slides, never present them as said in
  class, and never reveal results that they say students should discover for themselves.
$screens- **Other documents**: $extras
$previous
## Output (in this folder)

- `$notes_file`: the notes, in Pandoc Markdown.
- `figures/`: `make_figures.py` (it imports `figures/style.py`) and the figures it writes, each as PNG and PDF.
- The PDF and DOCX, built with `sh build/build.sh` (do not change the kit, except the running header in
  `build/latex-header.tex`, which you should set to the course name and the lecture).
$changes_output
## Method

1. **Assess the transcript first**: duration, parts and speakers, breaks, quality, and gaps (stretches with no usable
   speech, hallucinations). Never fill a gap by inventing what was said; when slides cover a lost stretch, say that
   the section relies on the slides.
2. **Read everything.** For a transcript longer than about 40 minutes, split it by time into three to five parts at
   natural boundaries and run one subagent per part, in parallel. Ask each subagent for a structured report: a
   timeline (time range, topic, slides); for each topic, the explanation in clear prose with the examples, numbers and
   analogies, especially what was said beyond the slide text; questions and discussions with the class; activities
   and their outcomes; course organisation (assessment, deadlines, rules); up to eight short quotable statements;
   probable mis-transcriptions with corrections. Subagents cannot write files: they must return the report as their
   final reply. Read the slides and other documents yourself.
3. **Plan the structure by theme**, not strictly in the order of the lecture, and write the notes.
4. **Draw the figures** (see below).
5. **Verify**: check every section against the transcript (again with subagents per part if it is long), looking for
   statements the transcript does not support, slide content presented as spoken, wrong attributions, missing
   substance and wrong timestamps. Fix what they find.
6. **Build** with `sh build/build.sh`, render the pages (`pdftoppm -png -r 45 file.pdf prefix`), assemble them into a
   contact sheet with Pillow, look at it, and fix layout problems.

## Structure

- Front matter (unnumbered headings, `{-}`): "How to use these notes" (what the notes are, the four kinds of box, a
  note on sources: data and charts mostly from the slides, explanations and discussions from the lecture; and a note
  on gaps in the recording if there are any); "The lecture at a glance" (a takeaways box with eight to ten messages);
  course organisation, if the lecture covered it (assessment as a table, deadlines, rules).
- The body in parts (unnumbered `# Part I · …` headings) and numbered chapters, which are level-1 headings too
  (`# Chapter title`, numbered 1, 2, 3 across the parts), with sections at `##`. Each chapter starts with an italic line
  such as `*In the recording: 00:42–00:55. Slides: deck name, slides 9–12.*`. End major chapters with a takeaways box.
- A synthesis: comparisons, a table of options or methods when the subject lends itself to it.
- Review questions (10–15) with short answers, a glossary (Pandoc definition list), sources and further reading (the
  references on the slides and the readings of the lecture), and what comes next (next session, preparation).
- When an exercise or activity was planned but not run, include it as a self-study exercise. When the recording cuts
  off part of an activity, give a worked approach based on what was taught, clearly marked as prepared for the notes.

## Style

- Write for students who were in the room and for those who missed it: clear, concrete, precise. Short paragraphs,
  plain words, active voice, no filler, no marketing tone. Explain every concept the first time it appears.
- Keep the lecturer's voice where it helps: short quotations of memorable statements (lightly cleaned of filler words;
  translated if the lecture was in another language), examples and analogies used in class.
- Prefer tables for comparisons and lists of facts with several attributes; prefer prose for reasoning.
- Report class discussions in "From the class discussion" boxes: the question or point, then the substance of the
  answer. These are often the most valuable part of the notes.

## Conventions (the build kit depends on them)

- Pandoc Markdown only: no raw LaTeX or HTML.
- A YAML header in this pattern (adapt the words, keep the shape):

  ```yaml
  title: "<Specific title naming the subject of the lecture>"
  subtitle: "Lecture notes for <lecture or session> · <course name and year>"
  author: "$author"
  date: "Lecture of <day month year>"
  lang: en-GB
  keywords: [keyword one, keyword two, keyword three]
  abstract: |
    Two to four sentences on what the lecture covered and what the notes contain.
  ```

  The **title** names the subject of the lecture, specific and informative (a question or a main title with a
  subtitle-like second part both work); not "Lecture notes". The **subtitle** says which lecture(s) or session and
  which course: "Lecture notes for <lecture or session> · <course>". The **author** line gives the programme, the
  institution and the lecturer(s), separated by " · ". The **date** is "Lecture of <day month year>" (or "Session of
  …"). Set `lang` to the language of the notes. Use the same course name in the running header of
  `build/latex-header.tex`.
- Boxes are fenced divs with a title: `::: {.keyidea title="…"}` … `:::`. Classes: `keyidea` (central concepts),
  `example` (cases, figures, class activities), `discussion` (class discussions), `definition`, `note` (caveats on
  data, sources and gaps; additions prepared for the notes), `takeaways`.
- Figures: `![Caption that says what the figure shows.](figures/fig-name.png){width=95%}`. Refer to them in the text.
- Tables: pipe tables with a caption line `: Caption.` under them. The dashes of the separator line set the relative
  column widths: give long text columns more dashes.
- Unnumbered headings get `{-}`, and so do their subsections. Use `CO~2~` for subscripts, not Unicode subscripts.
- Quotations from slides or documents as block quotes (`>`) with the source on the last line.

## Figures

- Draw them with matplotlib in `figures/make_figures.py`, using `figures/style.py` (palette, fonts, and `save()`, which
  adds the source line and writes PNG at 300 dpi and PDF). Run it with `python3 figures/make_figures.py figures`.
- Redraw the charts of the slides from their data (in `inputs/slides.md`), and draw diagrams for the frameworks,
  processes, timelines and models of the lecture. Aim for six to twelve figures that each teach something. Do not
  copy pictures from the slides unless they carry an open licence that you cite.
- Every figure has a short title inside it and a source line (`save(fig, name, "Source: …")`).
- Check every figure visually (build a contact sheet with Pillow and look at it) and fix overlaps, clipped labels and
  crowding before using it.

## Faithfulness and privacy

- Say only what the lecture, the slides or the documents support. Keep three sources apart: what was **said** (the
  transcript), what is on the **slides**, and **background** you add. Never present slide data or speaker notes as
  spoken; label additions as "outside the lecture" or put them in a note box.
- Check numbers against the slides. When the lecturer misspoke, use the correct figure and say so in a note.
- Correct mis-transcribed names and terms silently when you are sure; otherwise leave them out.
- Participants are anonymous: describe them by role or sector only ("a participant from battery recycling"). Never
  give their names, personal details or anything that identifies them. Summarise introduction rounds only in
  aggregate. The lecturer and guest speakers may be named.
- Leave out unverified claims made in discussion (anecdotes about specific events, people or companies) unless they
  are clearly presented as a participant's view and matter for the topic. Label the lecturer's personal opinions.
- Do not reveal answers that the materials reserve for a future activity.

## Finish

End with a short report: the files written, the quality of the transcription and its gaps, what you could not
verify, and any choices the lecturer should review.
"""

STUDENT_NOTES_REVISE_PARTS = r"""- **Previous version of the notes**: `inputs/previous-notes.md` (one document for the whole lecture) or
  `inputs/previous-part*.md` (one per part), written earlier. Use it as the starting point for the parts: take over what
  holds up (text, structure, style, and the figures in `figures/`, renamed for the part where they are used) into the part
  it belongs to, check every section against the transcript, correct what the transcript contradicts, and remove what it
  does not support.
"""

STUDENT_NOTES_REVISE = r"""- **Previous version of the notes**: `inputs/previous-notes.md`, written from an earlier, worse transcription. Use it
  as the starting point: keep its structure, style and figures (in `figures/`) where they hold up, check every
  section against the new transcript, correct what the transcript contradicts, add what it reveals (especially
  stretches the old transcription had lost), and remove what it does not support.
"""

STUDENT_NOTES_REVISE_FROM_SLIDES = r"""- **Previous version of the notes**: `inputs/previous-notes.md`, written from the slides and readings alone, before the
  recording was available. Use it as the starting point: keep its structure, style, explanations and figures (in
  `figures/`) where they hold up, then rework it from the transcript: add what was said and discussed in class (with
  the "In the recording" lines and "From the class discussion" boxes described below), correct what the transcript
  contradicts, and remove the statement that the recording was not available.
"""

STUDENT_NOTES_PROMPT_SLIDES = r"""# Task: reading notes for the students of a lecture, from its slides (no recording)

Prepare high-quality reading notes for the students of a lecture whose recording is not available, from its slides
and the readings that go with it. They must be excellent in content (faithful to the slides and readings, clear,
complete, useful for revision and for the course work) and visually appealing. Write them in neutral Pandoc Markdown
that the kit in `build/` turns into a typographically good PDF (via LaTeX) and a DOCX.

## Context

- Lecture: $lecture
- Language of the notes: **$language**. Keep technical terms in the form the field uses (often English).
- Context from the lecturer: $context

## Inputs (read them; do not modify them)

- **Slides**: `inputs/slides.md`: the text of every slide, the speaker notes, and the data behind PPTX charts. This is
  the backbone of the notes: they must cover what the slides cover, in a sensible order.
- **Speaker notes** are the lecturer's own notes and the best evidence of what each slide was meant to convey. Use
  their content to explain the slides, without quoting them as notes. Leave out everything private in them: names of
  students, reminders such as "ask X" or "verify before teaching", timing and classroom logistics, and any result they
  say students should discover for themselves.
- **Readings and other documents**, the main source for the explanations, proofs, examples and definitions that the
  slides only hint at (cite them precisely, author and chapter or section, where you rely on them): $extras If there
  are none, rely on the slides and on well-established knowledge of the subject, labelled as such.
$previous
## Output (in this folder)

- `$notes_file`: the notes, in Pandoc Markdown.
- `figures/`: `make_figures.py` (it imports `figures/style.py`) and the figures it writes, each as PNG and PDF.
- The PDF and DOCX, built with `sh build/build.sh` (do not change the kit, except the running header in
  `build/latex-header.tex`, which you should set to the course name and the lecture).
$changes_output
## Method

1. **Read all the slides and their speaker notes**, then the readings. For long readings, run subagents in parallel,
   one per chapter or part, and ask each for a structured report: the main concepts and definitions, the key results
   and why they hold (proof ideas), the examples and figures worth reusing, common pitfalls, and exact references
   (chapter, section, page). Subagents cannot write files: they must return the report as their final reply.
2. **Map the slides to the readings**: for each group of slides, find the passages that explain them. Note any slide
   content the readings do not cover, and any important reading content the slides leave out (mention it briefly as
   further reading, unless it is needed to understand the slides).
3. **Plan the structure by theme**, following the logic of the slides, and write the notes. Explain more than the
   slides do: the notes must be readable by a student who did not attend.
4. **Draw the figures** (see below).
5. **Verify**: check every section against the slides and readings, looking for statements they do not support,
   wrong references, missing steps in explanations and errors in examples or formulas. Fix what you find.
6. **Build** with `sh build/build.sh`, render the pages (`pdftoppm -png -r 45 file.pdf prefix`), assemble them into a
   contact sheet with Pillow, look at it, and fix layout problems.

## Structure

- Front matter (unnumbered headings, `{-}`): "How to use these notes" (what the notes are, the kinds of box, and a
  note saying plainly that the recording of this lecture is not available, so the notes were written from the slides,
  the lecturer's notes and the readings: they explain what the slides cover but do not report what was said or
  discussed in class); "The lecture at a glance" (a takeaways box with eight to ten messages); course organisation,
  if the slides cover it.
- The body in parts (unnumbered `# Part I · …` headings) and numbered chapters, which are level-1 headings too
  (`# Chapter title`, numbered 1, 2, 3 across the parts), with sections at `##`. Each chapter starts with an italic line
  such as `*Slides: deck name, slides 9–12. Reading: Author et al., chapter 6.*`. End major chapters with a
  takeaways box.
- A synthesis when the subject lends itself to it (comparisons, a table of methods or results).
- Review questions (10–15) with short answers, a glossary (Pandoc definition list), sources and further reading (the
  references on the slides and the readings), and what comes next (next lecture, preparation), if known.
- Turn the questions and exercises on the slides into "Think about it" boxes or self-study exercises, with short
  answers or hints at the end when the readings provide them.

## Style

- Write for students who missed the lecture: clear, concrete, precise. Short paragraphs, plain words, active voice, no
  filler, no marketing tone. Explain every concept the first time it appears, and work through the examples of the
  slides step by step.
- Prefer tables for comparisons and lists of facts with several attributes; prefer prose for reasoning.
- Do not invent what the lecturer said, asked or answered in class: there is no record of it.

## Conventions (the build kit depends on them)

- Pandoc Markdown only: no raw LaTeX or HTML.
- A YAML header in this pattern (adapt the words, keep the shape):

  ```yaml
  title: "<Specific title naming the subject of the lecture>"
  subtitle: "Lecture notes for <lecture or session> · <course name and year>"
  author: "$author"
  date: "Lecture of <day month year>"
  lang: en-GB
  keywords: [keyword one, keyword two, keyword three]
  abstract: |
    Two to four sentences on what the lecture covered and what the notes contain.
  ```

  The **title** names the subject of the lecture, specific and informative (a question or a main title with a
  subtitle-like second part both work); not "Lecture notes". The **subtitle** says which lecture(s) or session and
  which course: "Lecture notes for <lecture or session> · <course>". The **author** line gives the programme, the
  institution and the lecturer(s), separated by " · ". The **date** is "Lecture of <day month year>" (or "Session of
  …"). Set `lang` to the language of the notes. Use the same course name in the running header of
  `build/latex-header.tex`.
- Boxes are fenced divs with a title: `::: {.keyidea title="…"}` … `:::`. Classes: `keyidea` (central concepts),
  `example` (worked examples, cases), `discussion` ("Think about it" questions from the slides), `definition`, `note`
  (caveats, references, background added for the notes), `takeaways`.
- Figures: `![Caption that says what the figure shows.](figures/fig-name.png){width=95%}`. Refer to them in the text.
- Tables: pipe tables with a caption line `: Caption.` under them. The dashes of the separator line set the relative
  column widths: give long text columns more dashes.
- Code and pseudocode in fenced code blocks with a language tag (for example ```` ```java ````). Mathematics in
  Pandoc's TeX math (`$x^2$`, `$$ … $$`), which works in both PDF and DOCX.
- Unnumbered headings get `{-}`, and so do their subsections. Use `CO~2~` for subscripts in text.
- Quotations from slides or documents as block quotes (`>`) with the source on the last line.

## Figures

- Draw them with matplotlib in `figures/make_figures.py`, using `figures/style.py` (palette, fonts, and `save()`, which
  adds the source line and writes PNG at 300 dpi and PDF). Run it with `python3 figures/make_figures.py figures`.
- Redraw the charts of the slides from their data (in `inputs/slides.md`), and draw diagrams for the algorithms,
  structures, processes, timelines and models of the lecture (for example executions, state spaces, data structures
  before and after an operation). Aim for six to twelve figures that each teach something. Do not copy pictures from
  the slides unless they carry an open licence that you cite.
- Every figure has a short title inside it and a source line (`save(fig, name, "Source: …")`).
- Check every figure visually (build a contact sheet with Pillow and look at it) and fix overlaps, clipped labels and
  crowding before using it.

## Faithfulness and privacy

- Say only what the slides, the lecturer's notes and the readings support. Label background you add from general
  knowledge as such (a note box or "outside the course materials"), and keep it short and correct.
- Check numbers, formulas and code against the slides and readings; when they disagree, follow the reading and say so
  in a note.
- Never include names of students or anything that identifies them, even if they appear in the speaker notes.
- Do not reveal answers that the materials reserve for a future activity.

## Finish

End with a short report: the files written, what the notes rely on (slides, speaker notes, readings), slide content
the readings did not cover, what you could not verify, and any choices the lecturer should review.
"""

STUDENT_NOTES_REVISE_SLIDES = r"""- **Previous version of the notes**: `inputs/previous-notes.md`. Use it as the starting point: keep its
  structure, style and figures (in `figures/`) where they hold up, check every section against the slides and readings,
  correct what they contradict, add what is missing, and remove what they do not support.
"""


KIT_BOXES_LUA = r'''-- Pandoc filter for the reading notes. (build-kit 2)
-- The author line is wrapped for LaTeX: it would otherwise run into the margin (see Meta below).
-- Boxes are fenced divs with one of the classes below and an optional title:
--   ::: {.keyidea title="The main idea"}
--   ...
--   :::
-- LaTeX: each becomes a tcolorbox environment defined in latex-header.tex.
-- DOCX:  each becomes a paragraph style of the reference document (e.g. "Key Idea").
-- HTML:  the div is kept, with its title as a first paragraph.
-- Images: for LaTeX, figures/x.png is replaced by figures/x.pdf (vector) when that file exists.
-- Headings: chapters belong at level 1, next to the unnumbered "# Part I · ..." headings. If every level-1 heading is
-- unnumbered and the chapters are level 2, all headings below level 1 move up one level, so that chapters are
-- numbered 1, 2, 3 and not 0.1, 0.2, 0.3.

local boxes = {
  keyidea = "Key Idea",
  example = "Example Box",
  discussion = "Discussion Box",
  definition = "Definition Box",
  note = "Note Box",
  takeaways = "Takeaways Box",
}

local latex_special = { ["\\"] = "\\textbackslash{}", ["~"] = "\\textasciitilde{}", ["^"] = "\\textasciicircum{}" }

local function latex_escape(s)
  -- one pass, so that the braces of \textbackslash{} are not escaped again
  return (s:gsub("[\\&%%$#_{}~^]", function(c) return latex_special[c] or ("\\" .. c) end))
end

local function file_exists(path)
  local f = io.open(path, "r")
  if f then f:close() return true end
  return false
end

function Div(el)
  for cls, style in pairs(boxes) do
    if el.classes:includes(cls) then
      local title = el.attributes["title"] or ""
      if FORMAT:match("latex") then
        local blocks = { pandoc.RawBlock("latex", "\\begin{" .. cls .. "box}{" .. latex_escape(title) .. "}") }
        for _, b in ipairs(el.content) do table.insert(blocks, b) end
        table.insert(blocks, pandoc.RawBlock("latex", "\\end{" .. cls .. "box}"))
        return blocks
      end
      if title ~= "" then
        table.insert(el.content, 1, pandoc.Para({ pandoc.Strong(pandoc.Str(title)) }))
      end
      if FORMAT:match("docx") then
        -- tight list items are Plain blocks, which Word renders in the "Compact" style; as paragraphs they take the box style
        el = el:walk({ Plain = function(b) return pandoc.Para(b.content) end })
        el.attributes["custom-style"] = style
        el.attributes["title"] = nil
      end
      return el
    end
  end
end

-- LaTeX sets the author in a one-line box, so a long author line ("Programme · Institution · Lecturer, with guest ...") runs
-- into the margin. Put it in a centred paragraph box as wide as the text, which breaks the line where it must.
local function wrap_author(a)
  local inlines = pandoc.utils.type(a) == "Inlines" and a or pandoc.Inlines({ pandoc.Str(pandoc.utils.stringify(a)) })
  local out = pandoc.Inlines({ pandoc.RawInline("latex", "\\parbox[t]{0.92\\linewidth}{\\centering ") })
  out:extend(inlines)
  out:insert(pandoc.RawInline("latex", "}"))
  return out
end

function Meta(meta)
  if not (FORMAT:match("latex") and meta.author) then return nil end
  if pandoc.utils.type(meta.author) == "List" then
    local wrapped = {}
    for i, a in ipairs(meta.author) do wrapped[i] = wrap_author(a) end
    meta.author = pandoc.MetaList(wrapped)
  else
    meta.author = wrap_author(meta.author)
  end
  return meta
end

function Pandoc(doc)
  local top, numbered_top, numbered_second = false, false, false
  doc:walk({ Header = function(h)
    local numbered = not h.classes:includes("unnumbered")
    if h.level == 1 then
      top = true
      numbered_top = numbered_top or numbered
    elseif h.level == 2 and numbered then
      numbered_second = true
    end
  end })
  if top and numbered_second and not numbered_top then
    return doc:walk({ Header = function(h)
      if h.level > 1 then h.level = h.level - 1 end
      return h
    end })
  end
end

function Image(el)
  if FORMAT:match("latex") and el.src:match("%.png$") then
    local pdf = el.src:gsub("%.png$", ".pdf")
    if file_exists(pdf) then el.src = pdf end
  end
  return el
end
'''

KIT_LATEX_HEADER = r'''% Look of the PDF made with xelatex (see build.sh).
\usepackage{xcolor}
\definecolor{navy}{HTML}{1F3A5F}
\definecolor{accent}{HTML}{2E7DBE}
\definecolor{pale}{HTML}{EEF3F8}
\definecolor{leaf}{HTML}{3E8E5E}
\definecolor{leafpale}{HTML}{EDF6F0}
\definecolor{amber}{HTML}{B7791F}
\definecolor{amberpale}{HTML}{FBF5EA}
\definecolor{ink}{HTML}{22303C}

% headings in the sans-serif font, in navy
\usepackage{titlesec}
\titleformat{\section}{\sffamily\Large\bfseries\color{navy}}{\thesection}{0.8em}{}
\titleformat{\subsection}{\sffamily\large\bfseries\color{navy}}{\thesubsection}{0.8em}{}
\titleformat{\subsubsection}{\sffamily\normalsize\bfseries\color{accent}}{\thesubsubsection}{0.8em}{}
\titlespacing*{\section}{0pt}{2.2ex plus 1ex minus .2ex}{1.2ex}

% boxes used by the notes (filter boxes.lua)
\usepackage[most]{tcolorbox}
\tcbset{notesbox/.style={enhanced, breakable, boxrule=0pt, arc=1.5mm, left=4mm, right=4mm, top=2mm, bottom=2mm,
  before skip=2ex, after skip=2ex, fonttitle=\sffamily\bfseries\small, coltitle=ink, attach title to upper={\par\smallskip},
  borderline west={1.2mm}{0pt}{#1}}}
\newtcolorbox{keyideabox}[1]{notesbox=accent, colback=pale, title={#1}}
\newtcolorbox{examplebox}[1]{notesbox=leaf, colback=leafpale, title={#1}}
\newtcolorbox{discussionbox}[1]{notesbox=amber, colback=amberpale, title={#1}}
\newtcolorbox{definitionbox}[1]{notesbox=navy, colback=white, boxrule=0.4pt, colframe=pale, title={#1}}
\newtcolorbox{notebox}[1]{notesbox=gray, colback=gray!6, title={#1}, fontupper=\small}
\newtcolorbox{takeawaysbox}[1]{notesbox=navy, colback=pale, title={#1}}

% tables and captions
\usepackage{caption}
\captionsetup{font={small,sf}, labelfont={bf,color=navy}, justification=raggedright, singlelinecheck=false}
\renewcommand{\arraystretch}{1.25}
\usepackage{etoolbox}
\AtBeginEnvironment{longtable}{\small\sffamily}

% running header: set the course and the lecture
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\sffamily\footnotesize\color{gray} $header_left}
\fancyhead[R]{\sffamily\footnotesize\color{gray} Reading notes}
\fancyfoot[C]{\sffamily\footnotesize\thepage}
\renewcommand{\headrulewidth}{0pt}
\usepackage{microtype}
\setlength{\emergencystretch}{3em}

% characters the text fonts may lack
\usepackage{newunicodechar}
\newunicodechar{→}{\ensuremath{\rightarrow}}
'''

KIT_PDF_YAML = r'''# pandoc --defaults build/pdf.yaml   (run from the reading-notes folder)
from: markdown
to: pdf
pdf-engine: xelatex
filters: [build/boxes.lua]
include-in-header: [build/latex-header.tex]
resource-path: [".", "figures"]
toc: true
toc-depth: 2
number-sections: true
variables:
  documentclass: article
  papersize: a4
  fontsize: 11pt
  geometry: "top=2.4cm, bottom=2.4cm, left=2.5cm, right=2.5cm"
  mainfont: "$mainfont"
  sansfont: "$sansfont"
  linestretch: 1.12
  colorlinks: true
  linkcolor: navy
  urlcolor: accent
  toccolor: navy
'''

KIT_DOCX_YAML = r'''# pandoc --defaults build/docx.yaml   (run from the reading-notes folder)
from: markdown
to: docx
filters: [build/boxes.lua]
reference-doc: build/reference.docx
resource-path: [".", "figures"]
toc: true
toc-depth: 2
number-sections: true
'''

KIT_BUILD_SH = r'''#!/bin/sh
# build-kit 2. Builds every *.reading-notes.md of this folder (one, or one per part of the lecture) as DOCX and, when
# LaTeX is available, as PDF (via LaTeX). Needs pandoc 3 and, for the PDF, xelatex.
cd "$(dirname "$0")/.." || exit 1
PDF=yes
if [ -e build/no-latex ]; then
  echo "PDF skipped: build/no-latex exists (lecture2notes.py makes it with --latex no; delete it to make the PDF)"; PDF=no
elif ! command -v xelatex >/dev/null 2>&1; then
  echo "PDF skipped: xelatex not found"; PDF=no
fi
for SRC in *.reading-notes.md; do
  [ -e "$SRC" ] || continue
  if [ "$PDF" = yes ]; then
    pandoc --defaults build/pdf.yaml "$SRC" -o "${SRC%.md}.pdf" && echo "wrote ${SRC%.md}.pdf"
  fi
  pandoc --defaults build/docx.yaml "$SRC" -o "${SRC%.md}.docx" && echo "wrote ${SRC%.md}.docx"
done
# LaTeX source of one file, to edit further:  pandoc --defaults build/pdf.yaml -t latex -s FILE.reading-notes.md -o FILE.tex
'''

KIT_STYLE_PY = r'''"""Shared look of the figures of the reading notes. In make_figures.py:

    import sys; from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent)); from style import *
    OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
    fig, ax = plt.subplots(figsize=(6.6, 3.6)); ...; title(ax, "What the figure shows"); save(fig, OUT, "fig-name", "Source: ...")
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

NAVY, BLUE, LIGHT, PALE = "#1F3A5F", "#2E7DBE", "#A9C8E8", "#EEF3F8"      # main colours, darkest to palest
GREEN, LEAFPALE, AMBER, AMBERPALE = "#3E8E5E", "#E3F1E8", "#B7791F", "#FBF1DE"
RED, REDPALE, GREY, INK = "#B5473A", "#F6E3E0", "#9AA5B1", "#22303C"
SERIES = [NAVY, BLUE, GREEN, AMBER, "#6FA9DA", "#5FA57A", GREY]           # for several series, in this order

plt.rcParams.update({
    "font.family": ["$figfont", "DejaVu Sans"], "font.size": 10, "axes.edgecolor": GREY, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "text.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "pdf.fonttype": 42,
})


def title(ax, text, pad=10):
    """The figure's own title, top left, in navy."""
    ax.set_title(text, loc="left", fontsize=11.5, fontweight="bold", color=NAVY, pad=pad)


def save(fig, out, name, source=None):
    """Write out/name.png (300 dpi) and out/name.pdf, with the source line under everything drawn."""
    from pathlib import Path

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if source:
        fig.canvas.draw()
        bottom = fig.get_tightbbox(fig.canvas.get_renderer()).y0 / fig.get_figheight()
        fig.text(0.01, bottom - 0.03, source, fontsize=7.5, color=GREY, ha="left", va="top")
    for ext in ("png", "pdf"):
        fig.savefig(out / f"{name}.{ext}", dpi=300, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
'''


TEX_SPECIAL = {"\\": r"\textbackslash{}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(s: str) -> str:
    """Text for a LaTeX argument (the running header): every special character escaped, in one pass."""
    return re.sub(r"[\\&%$#_{}~^]", lambda m: TEX_SPECIAL.get(m.group(), "\\" + m.group()), s)


def _font_available(name: str) -> bool:
    """Is the font family installed? fontconfig (fc-list) says; a Mac often has no fontconfig, so there the font folders
    are searched for a file named after the family (Charter.ttc, Avenir Next.ttc)."""
    if shutil.which("fc-list"):
        r = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
        return any(name.lower() == f.strip().lower() for line in r.stdout.splitlines() for f in line.split(","))
    if sys.platform == "darwin":
        want = name.lower().replace(" ", "")
        for folder in ("/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
                       str(Path.home() / "Library" / "Fonts")):
            try:
                if any(p.stem.lower().replace(" ", "") == want for p in Path(folder).iterdir()):
                    return True
            except OSError:
                continue
    return False


def kit_fonts() -> tuple[str, str]:
    """Text and heading fonts for the PDF: Charter and Avenir Next on macOS, TeX Gyre fonts (in TeX Live) elsewhere."""
    main = "Charter" if _font_available("Charter") else "TeX Gyre Pagella"
    sans = "Avenir Next" if _font_available("Avenir Next") else "TeX Gyre Heros"
    return main, sans


def make_reference_docx(dst: Path) -> bool:
    """A Word reference document with the box styles of boxes.lua, navy headings, Cambria text and Calibri headings."""
    if not ENV.get("pandoc"):  # no pandoc: the kit is written anyway and student_notes() has said what is missing
        return False
    r = subprocess.run([ENV["pandoc"], "--print-default-data-file", "reference.docx"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return False
    zin = zipfile.ZipFile(io.BytesIO(r.stdout))
    styles = zin.read("word/styles.xml").decode()

    def box(sid, name, border, fill, extra=""):
        return (f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{name}"/>'
                f'<w:basedOn w:val="BodyText"/><w:qFormat/><w:pPr><w:pBdr><w:left w:val="single" w:sz="24" w:space="8" '
                f'w:color="{border}"/></w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
                f'<w:spacing w:before="40" w:after="40"/><w:ind w:left="227" w:right="227"/></w:pPr><w:rPr>{extra}</w:rPr></w:style>')

    styles = styles.replace("</w:styles>", "".join([
        box("KeyIdea", "Key Idea", "2E7DBE", "EEF3F8"), box("ExampleBox", "Example Box", "3E8E5E", "EDF6F0"),
        box("DiscussionBox", "Discussion Box", "B7791F", "FBF5EA"), box("DefinitionBox", "Definition Box", "1F3A5F", "FFFFFF"),
        box("NoteBox", "Note Box", "9AA5B1", "F3F4F6", '<w:sz w:val="20"/>'), box("TakeawaysBox", "Takeaways Box", "1F3A5F", "EEF3F8"),
    ]) + "</w:styles>")
    for sid, size in (("Title", 44), ("Subtitle", 28), ("Heading1", 34), ("Heading2", 28), ("Heading3", 24), ("TOCHeading", None)):
        m = re.search(rf'(<w:style [^>]*w:styleId="{sid}".*?</w:style>)', styles, re.S)
        if not m:
            continue
        st = m.group(1)
        st2 = re.sub(r"<w:color [^>]*/>", "", st)
        rpr = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:color w:val="1F3A5F"/>' + \
              (f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>' if size else "")
        st2 = re.sub(r"<w:rFonts [^>]*/>", "", st2).replace("<w:rPr>", "<w:rPr>" + rpr, 1) if "<w:rPr>" in st2 \
            else st2.replace("</w:style>", f"<w:rPr>{rpr}</w:rPr></w:style>")
        styles = styles.replace(st, st2)
    styles = re.sub(r"(<w:docDefaults>.*?<w:rPrDefault><w:rPr>)",
                    r'\1<w:rFonts w:ascii="Cambria" w:hAnsi="Cambria" w:cs="Cambria" w:eastAsia="Cambria"/>', styles, count=1, flags=re.S)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, styles.encode() if item.filename == "word/styles.xml" else zin.read(item.filename))
    return True


def pptx_charts(path: Path) -> dict[str, list[str]]:
    """Slide part (e.g. "ppt/slides/slide7.xml", as in slide["part"]: the file name does not tell the position of the
    slide in the deck) -> the data series of its charts, as text lines (category: value)."""
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    out: dict[str, list[str]] = {}
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return out
    with z:
        for rel in sorted(n for n in z.namelist() if re.match(r"ppt/slides/_rels/slide\d+\.xml\.rels$", n)):
            num = rel.replace("/_rels/", "/")[:-len(".rels")]
            for m in re.finditer(r'Target="\.\./charts/(chart\d+\.xml)"', z.read(rel).decode(errors="ignore")):
                try:
                    root = ET.fromstring(z.read("ppt/charts/" + m.group(1)))
                except (KeyError, ET.ParseError):
                    continue
                kinds = [e.tag.replace(C, "") for e in root.iter() if e.tag.endswith("Chart") and e.tag != C + "chart"]
                lines = [f"chart ({', '.join(kinds[:2]) or 'chart'}):"]
                for ser in root.iter(C + "ser"):
                    tx = ser.find(C + "tx")
                    name = "".join(v.text or "" for v in tx.iter(C + "v")) if tx is not None else ""
                    cat, val = ser.find(C + "cat"), ser.find(C + "val")
                    cats = [p.findtext(C + "v", "") for p in cat.iter(C + "pt")] if cat is not None else []
                    vals = [p.findtext(C + "v", "") for p in val.iter(C + "pt")] if val is not None else []
                    pairs = ", ".join(f"{c}: {v}" for c, v in zip(cats, vals)) if cats else ", ".join(vals)
                    lines.append(f"  series {name!r}: {pairs}")
                out.setdefault(num, []).extend(lines)
    return out


def slides_markdown(decks: list[Path], slides: list[dict]) -> str:
    """Every slide of every deck as text, with the speaker notes and chart data, for the notes writer."""
    out = ["# Slides\n", "References: deck number and slide, e.g. [2.14] is slide 14 of deck 2.\n"]
    charts = {d: pptx_charts(path) for d, path in enumerate(decks) if path.suffix.lower() == ".pptx"}
    current = None
    for sl in slides:
        d = sl.get("deck", 0)
        if d != current:
            current = d
            out.append(f"\n## Deck {d + 1}: {decks[d].name}\n")
        k = sl.get("k", sl["n"])
        out.append(f"\n### [{d + 1}.{k}] {sl['title'] or '(no title)'}\n")
        out += [f"{'  ' * min(lvl, 3)}- {t}" for lvl, t in sl["items"]]
        for line in charts.get(d, {}).get(sl.get("part", ""), []):
            out.append(f"  {line}")
        if sl["notes"]:
            out.append("\n  SPEAKER NOTES (private, not said in class): " + " / ".join(sl["notes"]))
    return "\n".join(out) + "\n"


def document_text(path: Path) -> str | None:
    """Plain text of an extra document for the notes writer (PDF, PPTX, Markdown or text)."""
    ext = path.suffix.lower()
    if ext == ".pdf" and ENV.get("pdftotext"):
        r = subprocess.run([ENV["pdftotext"], "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, errors="replace")
        return r.stdout.replace("\f", "\n\n")
    if ext == ".pptx":
        return slides_markdown([path], [dict(sl, deck=0, k=sl["n"]) for sl in read_pptx(path)])
    if ext in (".md", ".txt", ".csv"):
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def fill_template(template: str, values: dict[str, str]) -> str:
    """Replace $name by values[name] for the known names, in one pass: a value that itself contains "$extras" is left
    alone, and every other "$" of the template (the math of the prompt, "$$") is kept."""
    names = "|".join(sorted(map(re.escape, values), key=len, reverse=True))
    return re.sub(rf"\$({names})\b", lambda m: values[m.group(1)], template)


PART_TIME = re.compile(r"(?:P\d+ )?\d+:\d\d(?::\d\d)?")  # 02:27, 01:05:30, or P2 00:10:00 for the 2nd of several recordings


def parse_parts(spec: str | None) -> tuple[int, list[str] | None]:
    """--notes-parts: a number N (Claude finds N parts) or the times where the parts after the first begin, e.g. "02:27" (two
    parts) or "01:00,02:30" (three). Returns (number of parts, the times or None). Raises ValueError for anything else."""
    if not spec or not spec.strip():
        return 1, None
    s = spec.strip()
    if s.isdigit() and int(s) >= 1:
        return int(s), None
    points = [t.strip() for t in s.split(",") if t.strip()]
    if points and all(PART_TIME.fullmatch(t) for t in points):
        return len(points) + 1, points
    raise ValueError(f"--notes-parts must be a number of parts (e.g. 2) or the times where the later parts begin (e.g. 02:27 or "
                     f"01:00,02:30), not {spec!r}")


def part_files(fstem: str, n: int) -> list[str]:
    """The notes files: one for the lecture, or <name>.partK.reading-notes.md for each of n parts."""
    return [f"{fstem}.reading-notes.md"] if n == 1 else [f"{fstem}.part{k}.reading-notes.md" for k in range(1, n + 1)]


def parts_prompt(files: list[str], points: list[str] | None, revising: bool) -> str:
    """The section added to the prompt when the lecture gets one set of notes per part."""
    n = len(files)
    how = (f"The parts begin at these times of the recording (the first at its start, the last ends with it): "
           f"{', '.join(points)}. Find the exact place of each cut in the transcript, at the pause or change of topic nearest to it."
           if points else
           f"Find the {n} natural parts of the lecture in the transcript: a change of speaker, a break, a change of activity or "
           "of subject. Say in each part's \"How to use these notes\" where it begins and ends in the recording.")
    prev = (" The previous version (see Inputs) covers the whole lecture in one document: split its content between the parts, "
            "and write `CHANGES.md` saying how. Do not delete or move the previous version's files (the `.md`, `.pdf` and `.docx` "
            "of the one-document notes): the script puts them away when you are done." if revising else "")
    return (f"\n## Separate notes for each part of the lecture\n\n"
            f"The user wants **{n} separate sets of notes, one for each part of the lecture, not one document**. This section "
            f"overrides the Output and Structure sections above wherever they differ.\n\n"
            f"- {how}\n"
            f"- Write {n} files: " + ", ".join(f"`{f}`" for f in files) + ". Each is complete and stands alone: its own YAML header "
            "(the title names the subject of that part; the subtitle says \"Lecture notes for part K of N of <the lecture> · <course>\"), "
            "\"How to use these notes\", \"at a glance\", chapters, a synthesis, review questions, glossary, sources and "
            "\"what comes next\" (for the last part, the next session; for the others, the next part). Chapter numbers start again at "
            "1 in each document. Put the course organisation in the part where it was said.\n"
            "- A part covers only its own stretch of the recording: do not repeat content between parts. Where a part depends on "
            "another, say so and refer to it (\"see part 2\").\n"
            "- Figures share the folder `figures/`: start every file name with the part (`fig-p1-…`, `fig-p2-…`); a single "
            "`make_figures.py` draws them all.\n"
            "- `sh build/build.sh` builds every `*.reading-notes.md` file of the folder, so one run builds all the parts.\n"
            "- Do the verification step (step 5) for each part separately, and look at the pages of each part."
            f"{prev}\n"
            "- The final report is per part: the files written, the time range, what you could not verify.\n")


# ----------------------------------------------------------------------------------------------- which AI writes the notes
# The student notes are written by a command-line AI that reads PROMPT.md and works in the folder: Claude Code, Google's
# Antigravity CLI (agy) or GitHub Copilot CLI. Each is run non-interactively, with the permissions it needs and no more
# than its command line can say (see ai_argv).
AI_TOOLS = {  # name -> (label, program, how to sign in)
    "claude": ("Claude Code", "claude", "run `claude` once in a terminal and use /login"),
    "agy": ("Antigravity CLI (agy)", "agy", "run `agy` once in a terminal and sign in"),
    "copilot": ("GitHub Copilot CLI", "copilot", "run `copilot` once in a terminal and sign in with /login"),
}
NOTES_COMMANDS = ("python3", "pandoc", "pdftoppm", "pdftotext", "pdfinfo", "ls", "mkdir", "cp", "wc", "head", "grep")
NOTES_SCRIPTS = ("sh build/build.sh", "./build/build.sh")
NOTES_TASK = "Read PROMPT.md in the current folder and carry out the task it describes, to the end."


def ai_program(name: str, args) -> str | None:
    """Where the program of an AI is: --notes-ai-command for the AI named with --notes-ai, --claude-command for Claude, else
    the usual name, looked up on PATH. None if it is not there."""
    cmd = AI_TOOLS[name][1]
    if args.notes_ai == name and args.notes_ai_command:
        cmd = args.notes_ai_command
    elif name == "claude" and args.claude_command != "claude":
        cmd = args.claude_command
    return shutil.which(cmd) or (str(Path(cmd).expanduser()) if Path(cmd).expanduser().is_file() else None)


def available_ais(args) -> dict[str, str]:
    found = {}
    for name in AI_TOOLS:
        path = ai_program(name, args)
        if path:
            found[name] = path
    return found


def choose_ai(args) -> tuple[str, str] | None:
    """(name, path) of the AI that writes the notes: the one named with --notes-ai; else the only one installed; else, with
    several, the user's choice in a terminal (without one, Claude Code if it is there). None: none (or --notes-ai none)."""
    if args.notes_ai == "none":
        return None
    found = available_ais(args)
    if args.notes_ai != "auto":
        if args.notes_ai in found:
            return args.notes_ai, found[args.notes_ai]
        print(f"note: --notes-ai {args.notes_ai}: the program `{AI_TOOLS[args.notes_ai][1]}` was not found")
        return None
    if not found:
        print("note: no command-line AI found (looked for " + ", ".join(f"`{p}`" for _, p, _ in AI_TOOLS.values()) + ")")
        return None
    names = list(found)
    if len(names) == 1:
        pick = names[0]
    elif sys.stdin.isatty() and sys.stdout.isatty():
        print("\nSeveral command-line AIs are installed. Which one should write the student notes?")
        for i, n in enumerate(names, 1):
            print(f"  {i}) {AI_TOOLS[n][0]}  ({found[n]})")
        try:
            reply = input("Number [1] (--notes-ai NAME skips this question): ").strip()
        except EOFError:
            reply = ""
        pick = names[int(reply) - 1] if reply.isdigit() and 1 <= int(reply) <= len(names) else names[0]
    else:
        pick = "claude" if "claude" in found else names[0]
        print(f"note: several command-line AIs are installed ({', '.join(names)}); using {AI_TOOLS[pick][0]} (--notes-ai NAME chooses)")
    return pick, found[pick]


def ai_argv(name: str, exe: str, args, notes_dir: Path) -> list[str]:
    """The command line that runs the AI on PROMPT.md without a person at the keyboard. Claude Code and Copilot take a list of
    the commands they may run; agy's command line has no such list, but in its print mode (no --mode) it runs commands and
    edits files in its workspace without asking, which is what the notes need."""
    if name == "claude":
        tools = ["Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Task", "Agent", "TodoWrite"]
        tools += [f"Bash({c}:*)" for c in NOTES_COMMANDS] + [f"Bash({s})" for s in NOTES_SCRIPTS]
        cmd = [exe, "-p", NOTES_TASK, "--permission-mode", "acceptEdits", "--allowedTools", *tools, "--add-dir", str(notes_dir),
               "--output-format", "stream-json", "--verbose"]
        if args.notes_budget:
            cmd += ["--max-budget-usd", f"{args.notes_budget:g}"]
    elif name == "agy":
        cmd = [exe, "-p", NOTES_TASK, "--add-dir", str(notes_dir), "--output-format", "stream-json"]
    else:  # copilot
        cmd = [exe, "-p", NOTES_TASK, "-s", "--no-ask-user", "--no-auto-update", "--add-dir", str(notes_dir), "--allow-tool", "write"]
        for c in NOTES_COMMANDS:
            cmd += ["--allow-tool", f"shell({c}:*)"]
        for s in NOTES_SCRIPTS:
            cmd += ["--allow-tool", f"shell({s})"]
    if args.notes_budget and name != "claude":
        print(f"note: --notes-budget is for Claude Code; {AI_TOOLS[name][0]} has no such limit")
    if args.notes_model:
        cmd += ["--model", args.notes_model]
    return cmd + shlex.split(args.notes_ai_args or "")


def read_ai_output(name: str, lines) -> dict | None:
    """Show what the AI is doing, line by line, and return its final result as {"result", "is_error", "num_turns",
    "total_cost_usd"} (None if it gave none). Claude Code and agy write JSON events; Copilot's text is the report."""
    final, text = None, []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            ev = None
        if not isinstance(ev, dict):
            if line.strip():
                text.append(line.rstrip())
                print("  " + line.rstrip()[:200])
            continue
        if name == "claude":
            if ev.get("type") == "assistant":
                for item in ev.get("message", {}).get("content", []):
                    if item.get("type") == "text" and item.get("text", "").strip():
                        print("  claude: " + " ".join(item["text"].split())[:220])
                    elif item.get("type") == "tool_use":
                        inp = item.get("input", {})
                        what = inp.get("description") or inp.get("file_path") or inp.get("command") or inp.get("pattern") or ""
                        print(f"  · {item.get('name')}: {str(what)[:150]}")
            elif ev.get("type") == "result":
                final = ev
        elif ev.get("event") == "step_update":  # agy
            step = ev.get("step_update", {})
            if step.get("step_type") == "tool" and step.get("state") == "ACTIVE":
                params = (step.get("tool_info") or {}).get("parameters") or {}
                what = next((v for v in params.values() if isinstance(v, str)), "")
                print(f"  · {step.get('tool_name')}: {what[:150]}")
        elif ev.get("event") == "result":
            r = ev.get("result", {})
            final = {"result": r.get("response", ""), "is_error": r.get("status") != "SUCCESS", "num_turns": r.get("num_turns"),
                     "total_cost_usd": None}
    if final is None and text and name == "copilot":
        final = {"result": "\n".join(text), "is_error": False, "num_turns": None, "total_cost_usd": None}
    return final


def notes_folder(outdir: Path, ai: str) -> Path:
    """The folder of the notes of one AI: reading-notes-<ai>, so that two AIs never overwrite each other. Claude Code may reuse
    a folder named just reading-notes, from before the name of the AI was part of it (but not when reading-notes-claude exists)."""
    own, legacy = outdir / f"reading-notes-{ai}", outdir / "reading-notes"
    if ai == "claude" and not own.exists() and legacy.exists():
        print(f"using the existing folder {legacy.name}, made before the AI's name was part of the folder name")
        return legacy
    return own


def screens_markdown(notes_md: str) -> str | None:
    """From the <name>.notes.md of a run without slides: for each distinct screen of the video, its heading (with the times it was
    shown) and the text read on it by OCR. The speech (in the transcript) and the pictures are left out. None if there is none."""
    out, keep, found = [], False, False
    for ln in notes_md.splitlines():
        if ln.startswith("## "):
            out.append("\n" + ln)
            keep = False
        elif ln.startswith("**On the screen"):
            found = keep = True
            out.append(ln)
        elif ln.startswith(("**Key points", "**What was said", "**Speaker notes", "*Nothing in the recording")):
            keep = False
        elif keep and ln.strip():
            out.append(ln)
    return ("# Screens of the video (text read by OCR)\n" + "\n".join(out) + "\n") if found else None


def student_notes(decks: list[Path], slides: list[dict], recs: list[Path], outdir: Path, fstem: str, name: str,
                  transcript: Path | None, args) -> None:
    """Prepare the reading-notes folder of the chosen AI (inputs, build kit, figure style, prompt) and run the AI on it. Without
    a transcript (the recording failed), the notes are written from the slides and the --notes-extra documents alone."""
    chosen = choose_ai(args)  # which command-line AI writes them (asked now, if several are installed)
    ai_name, ai_exe = chosen if chosen else ("claude", None)
    notes_dir = notes_folder(outdir, ai_name)  # one folder per AI, so that two AIs never overwrite each other
    for sub in ("build", "figures", "inputs"):
        (notes_dir / sub).mkdir(parents=True, exist_ok=True)
    n_parts, points = parse_parts(args.notes_parts)
    if n_parts > 1 and transcript is None:
        print("note: --notes-parts needs a recording; the notes from the slides alone are one document")
        n_parts, points = 1, None
    notes_files = part_files(fstem, n_parts)
    notes_file = notes_files[0]
    combined = f"{fstem}.reading-notes.md"  # the one-document version, which a run with parts replaces
    print(f"\npreparing the student notes in {notes_dir}" + (f" ({n_parts} parts) ..." if n_parts > 1 else " ..."))
    if chosen:
        print(f"the notes will be written by {AI_TOOLS[ai_name][0]} ({ai_exe})")

    # inputs: the transcript and the text of the screens (copied, so that the AI needs only this folder), slides, extra documents
    screens = None
    if transcript is not None:
        shutil.copyfile(transcript, notes_dir / "inputs" / "transcript.md")
        notes_md = outdir / f"{fstem}.notes.md"  # the notes of a run without slides: the text read on each screen
        if notes_md.exists():
            screens = screens_markdown(notes_md.read_text(encoding="utf-8"))
        if screens:
            (notes_dir / "inputs" / "screens.md").write_text(screens, encoding="utf-8")
        else:
            (notes_dir / "inputs" / "screens.md").unlink(missing_ok=True)
    real_slides = [sl for sl in slides if sl.get("kind") in ("pptx", "pdf")]
    (notes_dir / "inputs" / "slides.md").write_text(
        slides_markdown(decks, real_slides) if real_slides else "# Slides\n\nNo slides were given for this lecture.\n", encoding="utf-8")
    extras = []
    for f in args.notes_extra or []:
        p = Path(f).expanduser()
        if not p.is_file():
            print(f"warning: --notes-extra {f} not found, skipped")
            continue
        text = document_text(p)
        if text is None:
            print(f"warning: --notes-extra {p.name}: only PDF, PPTX, Markdown and text files can be read; skipped")
            continue
        dst = notes_dir / "inputs" / f"{safe_stem(p.stem, 'document')}.txt"
        dst.write_text(text, encoding="utf-8")
        extras.append(f"`inputs/{dst.name}` (from {p.name})")

    # previous version: revise it instead of starting from scratch
    previous = notes_dir / (combined if n_parts > 1 else notes_file)  # with parts: the one-document version, if there is one
    existing_parts = [notes_dir / f for f in notes_files if (notes_dir / f).exists()] if n_parts > 1 else []
    revising = (previous.exists() or bool(existing_parts)) and not args.notes_fresh
    marker = notes_dir / "inputs" / "SLIDES-ONLY"  # the notes there were written without a recording
    from_slides = revising and marker.exists() and transcript is not None and n_parts == 1
    if transcript is None:
        marker.write_text("These notes were written from the slides alone (no recording).\n", encoding="utf-8")
    if revising:
        for old in (notes_dir / "inputs").glob("previous-*.md"):
            old.unlink()
        if previous.exists():
            shutil.copyfile(previous, notes_dir / "inputs" / "previous-notes.md")
            shown = previous.name
        else:
            for k, p in enumerate(existing_parts, 1):
                shutil.copyfile(p, notes_dir / "inputs" / f"previous-part{k}.md")
            shown = ", ".join(p.name for p in existing_parts)
        if from_slides:
            print(f"the earlier {notes_file} was written from the slides alone: Claude will rework it with the transcript")
        print(f"an earlier version ({shown}) exists: Claude will revise it (inputs/previous-*.md) and write CHANGES.md "
              "(--notes-fresh to start from scratch)")

    # build kit (existing files are kept, so that changes made by hand or by Claude survive a new run)
    main_font, sans_font = kit_fonts()
    header = tex_escape(args.notes_course or name)
    kit = {
        notes_dir / "build" / "boxes.lua": KIT_BOXES_LUA,
        notes_dir / "build" / "latex-header.tex": KIT_LATEX_HEADER.replace("$header_left", header),
        notes_dir / "build" / "pdf.yaml": KIT_PDF_YAML.replace("$mainfont", main_font).replace("$sansfont", sans_font),
        notes_dir / "build" / "docx.yaml": KIT_DOCX_YAML,
        notes_dir / "build" / "build.sh": KIT_BUILD_SH.replace("$notes_file", notes_file),
        notes_dir / "figures" / "style.py": KIT_STYLE_PY.replace("$figfont", sans_font if sans_font == "Avenir Next" else "DejaVu Sans"),
    }
    for path, text in kit.items():
        old_kit = path.name in ("build.sh", "boxes.lua") and path.exists() and "build-kit 2" not in path.read_text(encoding="utf-8")
        if old_kit:  # an old build.sh builds one file only, an old boxes.lua lets a long author line run into the margin
            shutil.copyfile(path, path.with_name(path.name + ".old"))
        if not path.exists() or args.notes_fresh or old_kit:
            path.write_text(text, encoding="utf-8")
    (notes_dir / "build" / "build.sh").chmod(0o755)
    no_latex = notes_dir / "build" / "no-latex"
    if args.latex == "no":
        no_latex.write_text("lecture2notes.py ran with --latex no: build.sh makes the DOCX only. Delete this file to make the PDF.\n")
    else:
        no_latex.unlink(missing_ok=True)
    note_missing(["pandoc"], "to build the student notes as DOCX and PDF (the Markdown is written anyway)")
    ref = notes_dir / "build" / "reference.docx"
    if (not ref.exists() or args.notes_fresh) and not make_reference_docx(ref):
        print("warning: could not create build/reference.docx (pandoc); the DOCX will use Word's default styles")

    # the prompt
    template = STUDENT_NOTES_PROMPT if transcript else STUDENT_NOTES_PROMPT_SLIDES
    if args.notes_prompt:
        tp = Path(args.notes_prompt).expanduser()
        if not tp.is_file():
            sys.exit(f"error: --notes-prompt {tp} not found")
        template = tp.read_text(encoding="utf-8")
    try:
        rel_transcript = "inputs/transcript.md" if transcript else "none (the recording is not available)"
    except ValueError:
        rel_transcript = str(transcript)
    lecture = name + (" — slides: " + ", ".join(d.name for d in decks) if decks else "") + "; recording" + \
        ("s: " + ", ".join(r.name for r in recs) if len(recs) > 1 else ": " + recs[0].name if recs else ": none (it failed)")
    if transcript is None:
        revise = STUDENT_NOTES_REVISE_SLIDES
    elif n_parts > 1:
        revise = STUDENT_NOTES_REVISE_PARTS
    else:
        revise = STUDENT_NOTES_REVISE_FROM_SLIDES if from_slides else STUDENT_NOTES_REVISE
    changes = ("content from the recording, now available" if from_slides else
               "content recovered by the new transcription" if transcript else "new or corrected material from the slides and readings")
    prompt = fill_template(template, {
        "lecture": lecture, "language": args.notes_language,
        "context": args.notes_context or "none given; infer the course and audience from the slides.",
        "author": args.notes_author or "<programme> · <institution> · <lecturer(s)>, as the slides or the context above show "
                                       "them; leave this line out if neither does",
        "screens": ("- **Screens**: `inputs/screens.md`: the text read (by OCR) from each distinct screen of the recording, with the times at "
                    "which it was shown. With no slide files, this is what was on the slides. OCR makes mistakes: use it to know "
                    "what was on screen, and check numbers against what was said. It is long: look things up in it (by time or by topic) rather "
                    "than reading it all.\n") if screens else "",
        "transcript": rel_transcript, "extras": "; ".join(extras) if extras else "none.",
        "notes_file": notes_file if n_parts == 1 else f"{fstem}.part<K>.reading-notes.md",
        "previous": revise if revising else "",
        "changes_output": ("- `CHANGES.md`: what changed from the previous version and why (corrections, additions, "
                           f"{changes}).\n" if revising else "")})
    prompt += ("\n## Where to work\n\nWork only in this folder. Other folders next to it (for example `reading-notes-*`, made by other "
               "runs, possibly by other AIs) belong to those runs: do not read, change or delete them.\n")
    if ai_name != "claude":
        prompt += ("\n## About your tools\n\nThese instructions were written for Claude Code. Where they mention subagents (the Task or "
                   "Agent tools) or any tool you do not have, do that work yourself, step by step and in the same order, to the same "
                   "standard: in particular, do not skip the verification step. Read the transcript in pieces if it is long.\n")
    if n_parts > 1:
        prompt += parts_prompt(notes_files, points, revising)
    if args.latex == "no":
        prompt += ("\n## No PDF this time\n\nThis run has no LaTeX (none installed, or `--latex no`), so `sh build/build.sh` writes "
                   "the DOCX only. Skip the PDF everywhere above: do not try to make or look at one, and do not change `build/` to "
                   "force it. Check the layout through the contact sheet of the figures and a careful reading of the Markdown. "
                   "Say in your final report that no PDF was made.\n")
    (notes_dir / "PROMPT.md").write_text(prompt, encoding="utf-8")
    (notes_dir / "PROMPT-template.md").write_text(template, encoding="utf-8")  # unfilled: edit it, reuse it with --notes-prompt
    print(f"wrote {notes_dir / 'PROMPT.md'} (the instructions given to Claude; to change them for future runs, edit "
          "PROMPT-template.md and pass it with --notes-prompt)")

    if not chosen:
        why = "--notes-ai none" if args.notes_ai == "none" else "no command-line AI was found or chosen"
        print(f"note: not running an AI ({why}). To have the notes written, install one ({', '.join(f'`{p}`' for _, p, _ in AI_TOOLS.values())}) "
              f"and run this again, or open {notes_dir} in it yourself and ask it to follow PROMPT.md")
        return
    label = AI_TOOLS[ai_name][0]
    cmd = ai_argv(ai_name, ai_exe, args, notes_dir)
    print(f"running {label} in {notes_dir} (this takes a while: often 20-60 minutes; progress below) ...")
    proc = subprocess.Popen(cmd, cwd=notes_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace")
    final = read_ai_output(ai_name, proc.stdout)
    proc.wait()
    if final:
        cost = final.get("total_cost_usd")
        print(f"\n{label}'s report:\n" + (final.get("result") or "").strip())
        if cost is not None:
            print(f"(cost: about USD {cost:.2f}; {final.get('num_turns', '?')} turns)")
        elif final.get("num_turns"):
            print(f"({final['num_turns']} turns)")
        if final.get("is_error") or proc.returncode:
            text = (final.get("result") or "").lower()
            if any(k in text for k in ("authenticate", "login", "oauth", "api key", "credit", "sign in", "quota")):
                print(f"warning: {label} could not sign in or has no credit left. {AI_TOOLS[ai_name][2].capitalize()}, and run this "
                      "command again.")
            else:
                print(f"warning: {label} ended with an error")
    elif proc.returncode:
        print(f"warning: {label} failed (exit code {proc.returncode})")
    ok_run = bool(final) and not final.get("is_error")
    made = [notes_dir / f for f in notes_files if (notes_dir / f).exists()]
    if made and transcript is not None and marker.exists() and ok_run:
        marker.unlink()  # reworked with the recording
    for out_md in made:
        out_pdf = out_md.with_suffix(".pdf")
        print(f"student notes: {out_md}" + (f" and {out_pdf.name}" if out_pdf.exists() else
                                            " (PDF not built yet: sh build/build.sh)" if args.latex == "yes" else " (no PDF: --latex no)"))
    if not made:
        print(f"no student notes were written; the prepared folder is {notes_dir} (you can also open it in a command-line AI and "
              "ask it to follow PROMPT.md)")
    elif n_parts > 1 and len(made) < n_parts:
        print(f"warning: only {len(made)} of the {n_parts} parts were written")
    elif n_parts > 1 and ok_run:  # the one-document version is replaced by the parts: keep it, out of the way
        old = [notes_dir / f"{fstem}.reading-notes{ext}" for ext in (".md", ".pdf", ".docx")]
        old = [p for p in old if p.exists()]
        if old:
            keep = notes_dir / "superseded"
            keep.mkdir(exist_ok=True)
            for p in old:
                shutil.move(str(p), str(keep / p.name))
            print(f"the one-document version was moved to {keep.name}/ ({', '.join(p.name for p in old)})")


# ----------------------------------------------------------------------------------------------- main
def _common_prefix(items: list[str]) -> str:
    """The longest prefix shared by all the strings (os.path.commonprefix is deprecated)."""
    first, last = min(items), max(items)
    n = 0
    while n < min(len(first), len(last)) and first[n] == last[n]:
        n += 1
    return first[:n]


def lecture_label(recs: list[Path]) -> str:
    """A name made of the recordings' names (or the decks' names, without a recording), so that each set of recordings
    gets its own output folder."""
    stems = [r.stem for r in recs]
    label = "+".join(stems)
    if len(stems) > 1:  # "Lecture (PART 1)" + "Lecture (PART 2)" -> "Lecture (PART 1+2)"
        short = min(len(x) for x in stems)
        pre = len(_common_prefix(stems))
        while pre and stems[0][pre - 1].isdigit():  # never split a number: aula01+02, not aula01+2
            pre -= 1
        suf = min(len(_common_prefix([x[::-1] for x in stems])), short - pre)
        while suf and stems[0][len(stems[0]) - suf].isdigit():
            suf -= 1
        mids = [x[pre:len(x) - suf] for x in stems]
        if pre >= 4 and all(mids):
            label = stems[0][:pre] + "+".join(mids) + stems[0][len(stems[0]) - suf:]
    if len(label) > 160:
        h = hashlib.sha1("|".join(str(r.resolve()) for r in recs).encode()).hexdigest()[:6]
        label = f"{recs[0].stem}+{len(recs) - 1} more-{h}"
    return label


def classify(f: Path) -> str:
    ext = f.suffix.lower()
    if ext in SLIDE_EXT:
        return "deck"
    if ext in OTHER_SLIDES:
        sys.exit(f"error: {f.name}: {OTHER_SLIDES[ext]}")
    if ext not in VIDEO_EXT | AUDIO_EXT:
        print(f"note: {f.name}: unknown extension, treated as a recording (anything ffmpeg can read)")
    return "rec"


def recordings_in(folder: Path) -> list[Path]:
    """The recordings in a folder (not in its subfolders), by name; an audio file is skipped when a video of the same
    name is there (it is most likely the audio of that video)."""
    files = sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")), key=lambda p: p.name.lower())
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXT]
    vstems = {p.stem for p in videos}
    audios = [p for p in files if p.suffix.lower() in AUDIO_EXT and p.stem not in vstems]
    return sorted(videos + audios, key=lambda p: p.name.lower())


def plan_lectures(args) -> list[tuple[list[Path], list[Path], Path, str]]:
    decks: list[Path] = []
    recs: list[Path] = []
    folders = False
    for f in args.files:
        if f.is_dir():
            found = recordings_in(f)
            print(f"{f}: {len(found)} recording(s)" + (": " + ", ".join(p.name for p in found) if found else ""))
            recs += found
            folders = True
        elif not f.is_file():
            sys.exit(f"error: {f} not found")
        elif classify(f) == "deck":
            decks.append(f)
        else:
            recs.append(f)
    seen: set[Path] = set()
    recs = [r for r in recs if not (r.resolve() in seen or seen.add(r.resolve()))]  # each recording once
    pptx_stems = {d.stem for d in decks if d.suffix.lower() == ".pptx"}
    twins = [d for d in decks if d.suffix.lower() == ".pdf" and d.stem in pptx_stems]
    if twins:  # the same deck given as PPTX and as PDF: the PPTX has the speaker notes
        print("note: using the .pptx and ignoring " + ", ".join(d.name for d in twins) + " (same deck as PDF)")
        decks = [d for d in decks if d not in twins]
    if not recs:
        if not decks:
            sys.exit("error: give at least one recording, or the slides of a lecture whose recording failed")
        if args.whisper_json:
            sys.exit("error: --whisper-json needs a recording")
        if not args.student_notes:
            print("note: no recording given: only the student notes can be made from the slides, so --student-notes is on")
            args.student_notes = True
        label = lecture_label(decks)
        return [(decks, [], args.outdir or decks[0].parent / f"{label} notes", args.name or label)]
    separate = args.separate or folders
    if not separate or len(recs) == 1:
        if args.whisper_json and len(args.whisper_json) != len(recs):
            sys.exit(f"error: {len(args.whisper_json)} --whisper-json given for {len(recs)} recordings")
        if not decks and separate:
            decks = same_name_slides(recs[0])
        label = lecture_label(recs)
        return [(decks, recs, args.outdir or recs[0].parent / f"{label} notes", args.name or label)]
    if args.name or args.whisper_json:
        sys.exit("error: --name and --whisper-json apply to one lecture; not with --separate / a folder of recordings")
    lectures = []
    for r in recs:
        d = decks or same_name_slides(r)
        label = lecture_label([r])
        lectures.append((d, [r], (args.outdir or r.parent) / f"{label} notes", label))
    return lectures


def same_name_slides(rec: Path) -> list[Path]:
    for p in (rec.with_suffix(".pptx"), rec.with_suffix(".PPTX"), rec.with_suffix(".pdf"), rec.with_suffix(".PDF")):
        if p.is_file():
            print(f"using the slides {p.name} (same name as the recording)")
            return [p]
    return []


def resolve_latex(args) -> None:
    """Set args.latex to "yes" or "no": what the user chose, else yes if a LaTeX engine is installed (the one given with
    --pdf-engine, or xelatex, lualatex or pdflatex) and no if not. Without LaTeX no PDF is made, and the run says so."""
    engine = bool(shutil.which(args.pdf_engine)) if args.pdf_engine else bool(ENV.get("latex"))
    args.latex_found, args.latex_chosen = engine, bool(args.latex)
    if args.latex:
        return
    args.latex = "yes" if engine else "no"
    if not engine and not (args.check or args.setup):
        print("note: no LaTeX engine found, so no PDF files will be made (--latex no); the Markdown files are written. "
              f"To get PDFs, install LaTeX ({install_hint('latex')}) and run again.")


def main() -> int:
    if sys.version_info < (3, 9):
        sys.exit("error: python 3.9 or newer is needed")
    for stream in (sys.stdout, sys.stderr):  # a console that cannot show a character (Windows) gets "?" instead of an error
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("files", nargs="*", type=Path, help="recordings (MOV or anything ffmpeg reads, used in order), optionally .pptx or .pdf slides "
                    "(used in order), or folders (each recording in them is a lecture of its own); slides alone, when the recording "
                    "failed, give student notes from the slides (and --notes-extra readings)")
    ap.add_argument("--version", action="version", version=f"lecture2notes {__version__} ({__date__})")
    ap.add_argument("--check", action="store_true", help="check that the external tools are installed (and how to install them) and exit")
    ap.add_argument("--setup", action="store_true", help="check the external tools and offer to install the missing ones (not LaTeX), then exit")
    ap.add_argument("--install", choices=("ask", "yes", "no"), default="ask",
                    help="what to do when a needed program is missing: ask = offer to install it (Homebrew when possible, only in a "
                         "terminal; the default); yes = do not ask for each package (Homebrew itself is always asked); no = never install")
    ap.add_argument("--separate", action="store_true", help="each recording is a lecture of its own, with its own output folder")
    ap.add_argument("-o", "--outdir", type=Path, help="output folder (default: '<recording> notes' next to the recording); "
                    "with --separate, the folder where the per-lecture folders go")
    ap.add_argument("--name", help="name used in titles and file names (default: the recordings')")
    ap.add_argument("--engine", choices=("auto", "macwhisper", "mlx", "openai"), default="auto",
                    help="speech recognition: auto (the default) = on a Mac, MacWhisper if it is installed with its command-line tool, "
                         "else mlx-whisper on Apple Silicon, else openai-whisper; on Linux and Windows, openai-whisper. "
                         "macwhisper = the MacWhisper app (commercial) through its `mw` tool; mlx = mlx-whisper, fast on Apple "
                         "Silicon; openai = the openai-whisper command line")
    ap.add_argument("--mlx-command", help=f"the mlx_whisper program (default: mlx_whisper in PATH, else {MLX_VENV_BIN})")
    ap.add_argument("--model", default="turbo", help="whisper model: tiny, base, small, medium, large, turbo (default: turbo = "
                    "large-v3-turbo); with --engine mlx also a Hugging Face repository or a local model folder; with MacWhisper the "
                    "model selected in the app is used, unless this is one of its ids, provider:name (see `mw models list`)")
    ap.add_argument("--language", help="spoken language, e.g. en or pt (default: whisper detects it)")
    ap.add_argument("--language-switch", metavar="auto|PLAN",
                    help="the speech changes language inside a recording, so Whisper's single language per file would be wrong for "
                         "part of it. auto: the script finds where (it checks the language every minute, then puts each cut at "
                         "the nearest pause); or give the plan, e.g. \"pt 04:30 en\" = Portuguese until 4 min 30 s, then English "
                         "(\"pt 04:30 en 1:10:00 pt\" has three parts; times are m:ss or h:mm:ss). Each part is transcribed in its own "
                         "language; --language is not used")
    ap.add_argument("--whisper-args", help="extra arguments for the whisper program of the engine, as one quoted string "
                    "(with mlx, given as options of mlx_whisper.transcribe, e.g. \"--initial-prompt 'Lecture on algorithms'\")")
    ap.add_argument("--keep-context", action="store_true",
                    help="let whisper use the text of the previous window as context (more consistent wording, but long "
                         "recordings can fall into repetition loops; off by default)")
    ap.add_argument("--silence-threshold", type=float, default=2.0,
                    help="skip silences longer than this (seconds) where whisper seems to hallucinate (default 2; 0 = off)")
    ap.add_argument("--cross-language", choices=("auto", "translate", "off"), default="auto",
                    help="slides in English and speech in another language: match an English translation of the speech (made by "
                         "whisper) to the slides; auto = only if the slide numbers of the video do not already place the speech")
    ap.add_argument("--translate-model", help="whisper model for that translation (default: medium when --model is turbo, "
                    "which is not trained to translate; otherwise --model)")
    ap.add_argument("--whisper-json", action="append", help="use this whisper JSON instead of transcribing (repeat: one per recording, in order)")
    ap.add_argument("--force", action="store_true", help="transcribe (and look at the video) again even if the results exist")
    ap.add_argument("--keep-audio", action="store_true", help="keep the extracted .wav")
    ap.add_argument("--key-points", type=int, default=3, help="key sentences per slide in the notes (0 = none)")
    ap.add_argument("--switch-penalty", type=float, default=0.15, help="cost of changing slide in the alignment (higher = steadier)")
    ap.add_argument("--allow-backward", action="store_true", default=True,
                    help="allow the lecture to return to earlier slides when matching by words (the default; kept for old commands)")
    ap.add_argument("--forward-only", dest="allow_backward", action="store_false",
                    help="the lecture follows the slides strictly in order: matching by words never goes back")
    ap.add_argument("--video-sync", choices=("auto", "on", "off"), default="auto",
                    help="read the slide number shown in the recording (bottom right of the slide) to know which slide goes with the speech; "
                         "auto = if tesseract and PIL are available")
    ap.add_argument("--video-fps", type=float, default=1.0, help="frames per second looked at in the recording (default 1)")
    ap.add_argument("--number-box", help="where the slide number is, as fractions of the frame: x,y,w,h (default: bottom right corner of the slide, found automatically)")
    ap.add_argument("--change-threshold", type=float, default=10.0, help="mean grey-level change (0-255) between samples that counts as a big change")
    ap.add_argument("--video-debug", action="store_true", help="write <recording>.slides.csv with what was read from the video, sample by sample")
    ap.add_argument("--sections", choices=("auto", "video", "time"), default="auto",
                    help="without PPTX: sections from the distinct screens of the video, or parts of the speech by time (auto = video if it works)")
    ap.add_argument("--min-slide-seconds", type=float, default=4.0, help="without PPTX: shorter screens are merged with the next one (default 4)")
    ap.add_argument("--scene-threshold", type=float, default=1.5, help="without PPTX: percentage of the picture that must change to start a new slide (default 1.5)")
    ap.add_argument("--same-slide-threshold", type=float, default=0.5, help="without PPTX: two screens differing in less than this percentage of the picture are the same slide (default 0.5)")
    ap.add_argument("--chunk-minutes", type=float, default=5.0, help="without PPTX and without usable video: length of the parts (default 5)")
    ap.add_argument("--latex", choices=("yes", "no"), help="make the PDF files, which needs pandoc and LaTeX (default: yes if a LaTeX "
                    "engine is installed, no if not). With no, only Markdown is written (and, for the student notes, DOCX but no PDF)")
    ap.add_argument("--no-pdf", action="store_true", help="only write the Markdown files (the same as --latex no for the transcript and the notes)")
    ap.add_argument("--pdf-engine", help="pandoc PDF engine (default: xelatex, then lualatex, pdflatex)")
    ap.add_argument("--mainfont", help="main font for xelatex/lualatex")
    sn = ap.add_argument_group("student notes (written by a command-line AI: Claude Code, Antigravity agy or GitHub Copilot)")
    sn.add_argument("--student-notes", action="store_true",
                    help="also write reading notes for the students in <outdir>/reading-notes (Markdown, PDF, DOCX, figures), "
                         "with Claude Code; an existing version there is revised against the new transcription (on by default when "
                         "only slides are given)")
    sn.add_argument("--notes-language", default="English", help="language of the student notes (default English)")
    sn.add_argument("--notes-context", help="what the AI should know: course, audience, lecturer, e.g. \"MBA students; lecturer Prof. X\"")
    sn.add_argument("--notes-parts", metavar="N|TIMES",
                    help="write one set of notes for each part of the lecture instead of one document: a number of parts that "
                         "the AI finds in the transcript (e.g. 2), or the times where the later parts begin (e.g. 02:27, or "
                         "01:00,02:30 for three; with several recordings, e.g. \"P2 00:10:00\")")
    sn.add_argument("--notes-only", action="store_true",
                    help="only write the student notes, from the transcript of an earlier run in the same output folder: "
                         "nothing is transcribed, matched or written again (implies --student-notes)")
    sn.add_argument("--notes-course", help="course name for the running header of the PDF (default: the lecture name)")
    sn.add_argument("--notes-author", help="the author line of the notes, e.g. \"MSc in Biology · University X · Prof. Y\" "
                    "(default: the AI takes the programme, institution and lecturer from the slides or from --notes-context, "
                    "and leaves the line out if they are not shown)")
    sn.add_argument("--notes-extra", action="append", help="another document for the notes (PDF, PPTX, Markdown, text): readings, "
                    "case studies, slides given as handouts (repeat for several); essential when there is no recording")
    sn.add_argument("--notes-prompt", help="use this file as the prompt template instead of the built-in one "
                    "(start from the PROMPT-template.md written by any run)")
    sn.add_argument("--notes-ai", choices=("auto", "claude", "agy", "copilot", "none"), default="auto",
                    help="which command-line AI writes the notes: auto (the default) = the one that is installed, or, if several, "
                         "your choice in a terminal (without one, Claude Code); none = only prepare the folder and PROMPT.md, "
                         "run no AI")
    sn.add_argument("--notes-ai-command", metavar="PROGRAM", help="the program of the AI named with --notes-ai, if it is not on PATH")
    sn.add_argument("--notes-ai-args", metavar="ARGS", help="extra arguments for the AI program, as one quoted string "
                    "(e.g. \"--sandbox\" for agy)")
    sn.add_argument("--notes-model", help="model for the notes, as the chosen AI names it (default: its own default)")
    sn.add_argument("--notes-budget", type=float, help="maximum spend in USD for the notes (API accounts)")
    sn.add_argument("--notes-fresh", action="store_true", help="start the notes from scratch, ignoring a previous version and its kit")
    sn.add_argument("--claude-command", default="claude", help="the Claude Code program (default: claude); the same as "
                    "--notes-ai-command with --notes-ai claude")
    args = ap.parse_args()
    global INSTALL_MODE
    INSTALL_MODE = args.install
    ensure_brew_on_path()
    ENV.update(probe_env())
    resolve_latex(args)
    if args.mlx_command and args.engine == "auto":
        args.engine = "mlx"  # naming the mlx program is choosing mlx
    resolve_engine(args)
    if args.mlx_command:
        exe = shutil.which(args.mlx_command) or (args.mlx_command if Path(args.mlx_command).expanduser().is_file() else None)
        if not exe:
            ap.error(f"--mlx-command {args.mlx_command} not found")
        ENV["mlx_whisper"] = str(Path(exe).expanduser())
    try:
        parse_parts(args.notes_parts)
    except ValueError as e:
        ap.error(str(e))
    try:
        parse_language_switch(args.language_switch)
    except ValueError as e:
        ap.error(str(e))
    if args.language_switch and args.language:
        print("note: --language-switch gives the language of each part, so --language is not used")
    if args.notes_ai_command and args.notes_ai in ("auto", "none"):
        ap.error("--notes-ai-command needs --notes-ai claude, agy or copilot, to say which AI the program is")
    if args.notes_only:
        args.student_notes = True
    if args.setup:
        return setup(args)
    if args.check:
        return report_env(args)
    if not args.files:
        ap.error("give at least one recording, or slides alone (or --check)")
    if args.video_fps <= 0:
        ap.error("--video-fps must be positive")

    lectures = plan_lectures(args)
    failed = []
    for n, (decks, recs, outdir, name) in enumerate(lectures, 1):
        if len(lectures) > 1:
            print(f"\n=== lecture {n} of {len(lectures)}: {', '.join(r.name for r in recs)}" + (f" + {', '.join(d.name for d in decks)}" if decks else ""))
        try:
            run_lecture(decks, recs, outdir, name, args)
        except (SystemExit, subprocess.CalledProcessError) as e:
            if len(lectures) == 1:
                raise
            msg = e.code if isinstance(e, SystemExit) else f"command failed: {' '.join(map(str, e.cmd))[:200]}"
            print(msg if isinstance(msg, str) else f"error (exit code {msg})", file=sys.stderr)
            failed.append(recs[0].name)
    if failed:
        print(f"\n{len(failed)} of {len(lectures)} lectures failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\ninterrupted")
    except subprocess.CalledProcessError as e:
        sys.exit(f"error: command failed (exit code {e.returncode}): {' '.join(map(str, e.cmd))[:300]}")
