"""Tests of lecture2notes.py. Standard library only; run from the repository folder with

    python -m unittest discover -s tests -v

The smoke test at the end needs ffmpeg (it is skipped without it): it makes a short video, gives the script a fake
Whisper transcription and checks the files written. Nothing here needs Whisper, a GPU, LaTeX or the network.
"""
import argparse
import builtins
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "lecture2notes.py"
spec = importlib.util.spec_from_file_location("lecture2notes_under_test", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Terminal(io.StringIO):
    """A stand-in for a console: isatty() is True."""

    def isatty(self):
        return True


class PureFunctions(unittest.TestCase):
    def test_align_forward_and_backward(self):
        scores = [[1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 0], [0, 0, 1], [0, 1, 0]]
        self.assertEqual(m.align(scores, 0.15, False), [0, 0, 1, 1, 1, 1])  # never goes back
        self.assertEqual(m.align(scores, 0.15, True), [0, 0, 1, 1, 2, 1])  # may return to an earlier slide

    def test_clean_numbers_removes_a_one_sample_misreading(self):
        self.assertEqual(m.clean_numbers([1, 1, 7, 1, 1, None, None, 2, 2], [False] * 9), [1, 1, 1, 1, 1, None, None, 2, 2])

    def test_drop_repetitions(self):
        segs = [{"text": "Yes.", "start": i, "end": i + 1} for i in range(4)] + [{"text": "Next", "start": 4, "end": 5}]
        kept, removed, _ = m.drop_repetitions(segs)
        self.assertEqual((len(kept), removed), (2, 3))

    def test_tex_escape(self):
        self.assertEqual(m.tex_escape("Aula_01 & 50%"), r"Aula\_01 \& 50\%")
        self.assertEqual(m.tex_escape("a\\b"), r"a\textbackslash{}b")

    def test_fill_template_is_one_pass(self):
        out = m.fill_template("L=$lecture X=$extras $$ math $x$", {"lecture": "talk $extras", "extras": "E"})
        self.assertEqual(out, "L=talk $extras X=E $$ math $x$")

    def test_lecture_label(self):
        self.assertEqual(m.lecture_label([Path("aula01.mov"), Path("aula02.mov")]), "aula01+02")
        self.assertEqual(m.lecture_label([Path("Lecture (PART 1).mov"), Path("Lecture (PART 2).mov")]), "Lecture (PART 1+2)")

    def test_accented_stop_words_are_filtered(self):
        for w in ("são", "então", "está", "até"):
            self.assertIsNone(m._token(w), w)
        self.assertEqual(m._token("concurrency"), "concurrency")

    def test_detect_language(self):
        self.assertEqual(m.detect_language("The lecture is about the threads and the locks that we use in this course."), "en")
        self.assertEqual(m.detect_language("A aula é sobre as threads e os locks que nós usamos neste curso para a programação."), "pt")

    def test_differs_matches_changed(self):
        import random

        rnd = random.Random(1)
        for _ in range(100):
            a = bytes(rnd.choice((0, 100, 255)) for _ in range(400))
            b = bytes(x if rnd.random() < 0.9 else rnd.choice((0, 255)) for x in a)
            for pct in (0.5, 1.5, 10, 30):
                self.assertEqual(m._differs(a, b, pct), m._changed(a, b) > pct)

    def test_parse_srt(self):
        text = "1\n00:00:01,000 --> 00:00:03,500\nHello\nworld\n\n2\n00:01:00,5 --> 00:01:02,000\nSecond one\n"
        self.assertEqual(m.parse_srt(text), [{"start": 1.0, "end": 3.5, "text": "Hello world"},
                                             {"start": 60.5, "end": 62.0, "text": "Second one"}])

    def test_parse_mw_json_milliseconds_and_seconds(self):
        data = json.dumps({"text": "x", "segments": [{"id": 0, "start": 0, "end": 1600, "text": " Hi "},
                                                     {"id": 1, "start": 1600, "end": 4000, "text": "There"}]})
        segs = m.parse_mw_json(data, audio_seconds=4.0)  # milliseconds, as documented
        self.assertEqual([(s["start"], s["end"], s["text"]) for s in segs], [(0.0, 1.6, "Hi"), (1.6, 4.0, "There")])
        data = json.dumps({"segments": [{"start": 0.0, "end": 1.6, "text": "Hi"}, {"start": 1.6, "end": 3.9, "text": "There"}]})
        self.assertEqual(m.parse_mw_json(data, audio_seconds=4.0)[1]["end"], 3.9)  # numbers as long as the audio: seconds

    def test_stale_reason_uses_the_record_next_to_the_json(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rec, js = d / "a.mov", d / "a.json"
            rec.write_bytes(b"x" * 10)
            js.write_text("{}")
            self.assertIsNone(m.stale_reason(js, rec, "turbo", None))  # no record: trusted
            m.write_meta(js, {"source": m.source_fingerprint(rec), "model": "turbo", "language": "pt"})
            self.assertIsNone(m.stale_reason(js, rec, "turbo", "pt"))
            self.assertIn("model", m.stale_reason(js, rec, "large", None))
            self.assertIn("'pt'", m.stale_reason(js, rec, "turbo", "en"))
            self.assertIsNone(m.stale_reason(js, rec, None, None))  # MacWhisper without an explicit model: nothing to compare
            rec.write_bytes(b"x" * 11)
            self.assertIn("not the file", m.stale_reason(js, rec, "turbo", None))


class NotesInParts(unittest.TestCase):
    def test_parse_parts(self):
        self.assertEqual(m.parse_parts(None), (1, None))
        self.assertEqual(m.parse_parts("1"), (1, None))
        self.assertEqual(m.parse_parts("3"), (3, None))
        self.assertEqual(m.parse_parts("02:27"), (2, ["02:27"]))
        self.assertEqual(m.parse_parts("01:00, 02:30:15"), (3, ["01:00", "02:30:15"]))
        self.assertEqual(m.parse_parts("P2 00:10:00"), (2, ["P2 00:10:00"]))
        for bad in ("abc", "0", "2:7", "01:00,x"):
            with self.assertRaises(ValueError, msg=bad):
                m.parse_parts(bad)

    def test_the_kit_is_prepared_without_pandoc(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(m.ENV, {"pandoc": None}):
            self.assertFalse(m.make_reference_docx(Path(d) / "reference.docx"))  # no crash, no file

    @unittest.skipUnless(shutil.which("pandoc"), "needs pandoc")
    def test_a_long_author_line_is_wrapped_for_latex(self):
        with tempfile.TemporaryDirectory() as d:
            lua, md = Path(d) / "boxes.lua", Path(d) / "a.md"
            lua.write_text(m.KIT_BOXES_LUA, encoding="utf-8")
            md.write_text('---\ntitle: T\nauthor: "A long author line · with a separator, which would run into the margin"\n---\n\nText\n',
                          encoding="utf-8")
            r = subprocess.run(["pandoc", str(md), "--lua-filter", str(lua), "-t", "latex", "-s"], capture_output=True,
                               text=True, encoding="utf-8")
            self.assertIn("\\author{\\parbox[t]{0.92\\linewidth}{\\centering A long author line", r.stdout)

    def test_part_files(self):
        self.assertEqual(m.part_files("L", 1), ["L.reading-notes.md"])
        self.assertEqual(m.part_files("L", 2), ["L.part1.reading-notes.md", "L.part2.reading-notes.md"])

    def test_parts_prompt(self):
        files = m.part_files("L", 2)
        text = m.parts_prompt(files, ["02:27"], revising=True)
        for needle in ("2 separate sets", "L.part1.reading-notes.md", "L.part2.reading-notes.md", "02:27", "fig-p1", "CHANGES.md",
                       "Do not delete or move the previous version"):
            self.assertIn(needle, text)
        free = m.parts_prompt(files, None, revising=False)
        self.assertIn("Find the 2 natural parts", free)
        self.assertNotIn("CHANGES.md", free)

    @unittest.skipIf(sys.platform == "win32", "runs the kit's sh script")
    def test_build_script_builds_every_notes_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "build").mkdir()
            (d / "build" / "build.sh").write_text(m.KIT_BUILD_SH)
            (d / "bin").mkdir()
            fake = d / "bin" / "pandoc"  # writes an empty file where -o says
            fake.write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then shift; : > "$1"; fi; shift; done\n')
            (d / "bin" / "xelatex").write_text("#!/bin/sh\n")
            for f in (fake, d / "bin" / "xelatex"):
                f.chmod(0o755)
            for name in ("A.part1.reading-notes.md", "A.part2.reading-notes.md"):
                (d / name).write_text("# x\n")
            env = {"PATH": f"{d / 'bin'}:/usr/bin:/bin"}
            subprocess.run(["sh", str(d / "build" / "build.sh")], env=env, check=True, capture_output=True)
            self.assertEqual(sorted(p.name for p in d.glob("*.pdf")), ["A.part1.reading-notes.pdf", "A.part2.reading-notes.pdf"])
            self.assertEqual(len(list(d.glob("*.docx"))), 2)
            for p in list(d.glob("*.pdf")) + list(d.glob("*.docx")):
                p.unlink()
            (d / "build" / "no-latex").write_text("x")  # --latex no: the DOCX only
            subprocess.run(["sh", str(d / "build" / "build.sh")], env=env, check=True, capture_output=True)
            self.assertEqual(len(list(d.glob("*.pdf"))), 0)
            self.assertEqual(len(list(d.glob("*.docx"))), 2)


class AIChoice(unittest.TestCase):
    """Which command-line AI writes the student notes, how it is run, and how its output is read."""

    def args(self, **kw):
        base = dict(notes_ai="auto", notes_ai_command=None, claude_command="claude", notes_ai_args=None, notes_model=None,
                    notes_budget=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def present(self, *names):
        return mock.patch.object(m.shutil, "which", lambda c, *a, **k: f"/bin/{c}" if c in names else None)

    def quiet(self):
        return mock.patch.object(sys, "stdout", io.StringIO())

    def test_only_the_installed_ones_are_found(self):
        with self.present("agy"):
            self.assertEqual(m.available_ais(self.args()), {"agy": "/bin/agy"})
        with self.present():
            self.assertEqual(m.available_ais(self.args()), {})

    def test_one_installed_is_used_and_none_means_none(self):
        with self.present("agy"), self.quiet():
            self.assertEqual(m.choose_ai(self.args()), ("agy", "/bin/agy"))
            self.assertIsNone(m.choose_ai(self.args(notes_ai="none")))
            self.assertIsNone(m.choose_ai(self.args(notes_ai="copilot")))  # named, but not installed
        with self.present(), self.quiet():
            self.assertIsNone(m.choose_ai(self.args()))

    def test_several_without_a_terminal(self):
        with self.present("agy", "claude", "copilot"), self.quiet(), mock.patch.object(sys, "stdin", io.StringIO()):
            self.assertEqual(m.choose_ai(self.args())[0], "claude")
        with self.present("copilot", "agy"), self.quiet(), mock.patch.object(sys, "stdin", io.StringIO()):
            self.assertEqual(m.choose_ai(self.args())[0], "agy")  # no Claude: the first in the list
            self.assertEqual(m.choose_ai(self.args(notes_ai="copilot"))[0], "copilot")

    def test_several_in_a_terminal_ask(self):
        with self.present("agy", "claude", "copilot"), mock.patch.object(sys, "stdin", Terminal()), \
                mock.patch.object(sys, "stdout", Terminal()) as out:
            with mock.patch.object(builtins, "input", lambda prompt="": "2"):
                self.assertEqual(m.choose_ai(self.args())[0], "agy")  # the order of the menu: claude, agy, copilot
            with mock.patch.object(builtins, "input", lambda prompt="": ""):
                self.assertEqual(m.choose_ai(self.args())[0], "claude")  # Enter: the first
            with mock.patch.object(builtins, "input", lambda prompt="": "9"):
                self.assertEqual(m.choose_ai(self.args())[0], "claude")  # not a choice: the first
        self.assertIn("Which one should write", out.getvalue())

    def test_command_lines(self):
        out = Path("/out")
        agy = m.ai_argv("agy", "/x/agy", self.args(notes_model="m1", notes_ai_args="--sandbox"), out)  # out: the AI's own folder
        self.assertEqual(agy[:2], ["/x/agy", "-p"])
        self.assertIn("stream-json", agy)
        self.assertNotIn("--mode", agy)  # accept-edits would deny the shell commands in print mode
        self.assertEqual(agy[agy.index("--add-dir") + 1], str(out))  # a Path: \\out on Windows
        self.assertEqual(agy[-3:], ["--model", "m1", "--sandbox"])
        claude = m.ai_argv("claude", "/x/claude", self.args(notes_budget=5), out)
        self.assertEqual(claude[claude.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("Bash(python3:*)", claude)
        self.assertIn("Bash(sh build/build.sh)", claude)
        self.assertEqual(claude[claude.index("--max-budget-usd") + 1], "5")
        copilot = m.ai_argv("copilot", "/x/copilot", self.args(), out)
        self.assertIn("-s", copilot)
        self.assertIn("shell(python3:*)", copilot)
        self.assertIn("shell(sh build/build.sh)", copilot)
        self.assertEqual(copilot[copilot.index("--allow-tool") + 1], "write")

    def read(self, name, lines):
        with mock.patch.object(sys, "stdout", io.StringIO()) as out:
            final = m.read_ai_output(name, lines)
        return final, out.getvalue()

    def test_reading_agy_events(self):
        lines = [json.dumps({"event": "init", "init": {"tools": ["x"]}}),
                 json.dumps({"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE", "tool_name": "run_command",
                                                                    "tool_info": {"parameters": {"CommandLine": "echo hi"}}}}),
                 json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "DONE\n", "num_turns": 1}}) + "\n"]
        final, shown = self.read("agy", lines)
        self.assertEqual((final["result"], final["is_error"], final["num_turns"]), ("DONE\n", False, 1))
        self.assertIn("run_command: echo hi", shown)
        final, _ = self.read("agy", [json.dumps({"event": "result", "result": {"status": "ERROR", "response": "boom"}})])
        self.assertTrue(final["is_error"])

    def test_reading_claude_events_and_plain_text(self):
        lines = [json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.md"}}]}}),
                 json.dumps({"type": "result", "result": "Finished", "is_error": False, "total_cost_usd": 1.5, "num_turns": 3})]
        final, shown = self.read("claude", lines)
        self.assertEqual((final["result"], final["total_cost_usd"]), ("Finished", 1.5))
        self.assertIn("Read: a.md", shown)
        final, shown = self.read("copilot", ["Both parts are written.\n", "Checked.\n"])  # Copilot's text is the report
        self.assertEqual(final["result"], "Both parts are written.\nChecked.")
        self.assertIsNone(self.read("agy", ["not json\n"])[0])


class NotesFolders(unittest.TestCase):
    def test_one_folder_per_ai_and_claude_may_reuse_the_old_name(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sys, "stdout", io.StringIO()):
            d = Path(d)
            self.assertEqual(m.notes_folder(d, "agy"), d / "reading-notes-agy")
            self.assertEqual(m.notes_folder(d, "claude"), d / "reading-notes-claude")  # nothing there yet: a new folder
            (d / "reading-notes").mkdir()  # a folder from before the AI's name was part of it
            self.assertEqual(m.notes_folder(d, "claude"), d / "reading-notes")  # Claude Code reuses it
            self.assertEqual(m.notes_folder(d, "agy"), d / "reading-notes-agy")  # the others never touch it
            self.assertEqual(m.notes_folder(d, "copilot"), d / "reading-notes-copilot")
            (d / "reading-notes-claude").mkdir()  # once the new one exists, it wins
            self.assertEqual(m.notes_folder(d, "claude"), d / "reading-notes-claude")

    def test_screens_are_taken_from_the_notes_of_a_run_without_slides(self):
        notes = ("---\ntitle: x\n---\n\n## Slide 1: Intro `[00:00:05 – 00:01:00]`\n\n![](pics/slide-001.png){width=85%}\n\n"
                 "**On the screen** (read by OCR at 00:00:30)\n\n- Welcome\n- Agenda\n\n**Key points from the lecture**\n\n- A key sentence.\n\n"
                 "**What was said**\n\n> **[00:00:05]** Hello everybody.\n\n## Slide 2: Second `[00:01:00 – 00:02:00]`\n\n"
                 "*Nothing in the recording was assigned to this slide.*\n")
        text = m.screens_markdown(notes)
        self.assertIn("## Slide 1: Intro", text)
        self.assertIn("- Welcome", text)
        self.assertIn("- Agenda", text)
        for left_out in ("A key sentence", "Hello everybody", "slide-001.png"):
            self.assertNotIn(left_out, text)
        self.assertIsNone(m.screens_markdown("## Slide 1: x\n\nNo screens read here.\n"))


class EngineChoice(unittest.TestCase):
    def resolve(self, platform, machine="arm64", macwhisper=None, engine="auto"):
        args = argparse.Namespace(engine=engine)
        with mock.patch.object(sys, "platform", platform), mock.patch.object(m.platform, "machine", lambda: machine), \
                mock.patch.dict(m.ENV, {"macwhisper": macwhisper}), mock.patch.object(Path, "is_dir", lambda self: False):
            m.resolve_engine(args)
        return args.engine

    def test_defaults_by_platform(self):
        self.assertEqual(self.resolve("darwin", macwhisper="/usr/local/bin/mw"), "macwhisper")
        self.assertEqual(self.resolve("darwin"), "mlx")
        self.assertEqual(self.resolve("darwin", machine="x86_64"), "openai")
        self.assertEqual(self.resolve("linux", machine="x86_64"), "openai")
        self.assertEqual(self.resolve("win32", machine="AMD64"), "openai")
        self.assertEqual(self.resolve("linux", macwhisper="/usr/local/bin/mw"), "openai")  # MacWhisper exists only on the Mac

    def test_an_explicit_choice_is_kept(self):
        self.assertEqual(self.resolve("darwin", macwhisper="/usr/local/bin/mw", engine="openai"), "openai")
        self.assertEqual(self.resolve("linux", engine="mlx"), "mlx")


class MacWhisper(unittest.TestCase):
    def run_transcribe(self, outputs):
        """Call macwhisper_transcribe with a fake `mw` that answers with `outputs`, one (returncode, stdout) per call."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wav, out = d / "a.wav", d / "a.json"
            wav.write_bytes(b"\0" * 32000 * 4)  # four seconds of audio
            calls = []
            answers = iter(outputs)

            def fake_run(cmd, **kw):
                calls.append(cmd)
                code, text = next(answers)
                return mock.Mock(returncode=code, stdout=text)

            args = argparse.Namespace(language="pt", model="turbo")
            with mock.patch.dict(m.ENV, {"macwhisper": "/fake/mw"}), mock.patch.object(m.subprocess, "run", fake_run), \
                    mock.patch.object(sys, "stdout", io.StringIO()):
                m.macwhisper_transcribe(wav, out, args)
            return calls, json.loads(out.read_text(encoding="utf-8"))

    def test_json_when_available(self):
        js = json.dumps({"segments": [{"start": 0, "end": 1600, "text": "Olá"}, {"start": 1600, "end": 3000, "text": "mundo"}]})
        calls, data = self.run_transcribe([(0, js)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:5], ["/fake/mw", "transcribe", calls[0][2], "--format", "json"])
        self.assertIn("--language", calls[0])
        self.assertNotIn("--model", calls[0])  # "turbo" is not a MacWhisper model id: the app's selected model is used
        self.assertEqual([s["text"] for s in data["segments"]], ["Olá", "mundo"])
        self.assertEqual(data["segments"][0]["end"], 1.6)

    def test_srt_when_json_needs_pro(self):
        srt = "1\n00:00:00,000 --> 00:00:01,500\nHello there\n"
        calls, data = self.run_transcribe([(1, ""), (0, srt)])
        self.assertEqual([c[c.index("--format") + 1] for c in calls], ["json", "srt"])
        self.assertEqual(data["segments"][0]["text"], "Hello there")

    def test_error_when_nothing_comes_out(self):
        with self.assertRaises(SystemExit):
            self.run_transcribe([(1, ""), (1, "")])

    def test_a_model_id_is_passed_on(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "a.wav"
            wav.write_bytes(b"\0" * 64000)
            seen = []

            def fake_run(cmd, **kw):
                seen.append(cmd)
                return mock.Mock(returncode=0, stdout=json.dumps({"segments": [{"start": 0, "end": 900, "text": "x"}]}))

            args = argparse.Namespace(language=None, model="whisperkit:openai_whisper-small")
            with mock.patch.dict(m.ENV, {"macwhisper": "/fake/mw"}), mock.patch.object(m.subprocess, "run", fake_run), \
                    mock.patch.object(sys, "stdout", io.StringIO()):
                m.macwhisper_transcribe(wav, Path(d) / "a.json", args)
            self.assertEqual(seen[0][seen[0].index("--model") + 1], "whisperkit:openai_whisper-small")
            self.assertNotIn("--language", seen[0])


class InstallOffer(unittest.TestCase):
    """require() with a fake terminal, a fake brew and scripted answers: no program is really installed."""

    def scenario(self, answers, *, brew=True, mode="ask", tty=True, missing=("ffmpeg",)):
        m.INSTALL_MODE, ran, shown = mode, [], []
        m._declined.clear()
        state = {"brew": brew, "present": set()}
        replies = iter(answers)

        def fake_run(cmd, *a, **k):
            ran.append(" ".join(map(str, cmd)))
            if "install.sh" in ran[-1]:
                state["brew"] = True
            state["present"].update(missing)
            return mock.Mock(returncode=0)

        def fake_input(prompt=""):
            shown.append(prompt)
            return next(replies)

        console = Terminal() if tty else io.StringIO()
        with mock.patch.object(m.subprocess, "run", fake_run), \
                mock.patch.object(m, "probe_env", lambda: {t: ("/fake/" + t if t in state["present"] else None) for t in m.INSTALL}), \
                mock.patch.object(m.shutil, "which", lambda x, *a, **k: "/opt/homebrew/bin/brew" if x == "brew" and state["brew"] else None), \
                mock.patch.object(builtins, "input", fake_input), mock.patch.object(sys, "stdin", console), \
                mock.patch.object(sys, "stdout", console), mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.dict(m.ENV, {t: None for t in m.INSTALL}):
            try:
                m.require(list(missing), "to go on")
                exited = False
            except SystemExit:
                exited = True
        return ran, shown, exited

    def test_yes_installs_with_brew(self):
        ran, shown, exited = self.scenario(["y"])
        self.assertEqual((ran, exited, len(shown)), (["/opt/homebrew/bin/brew install ffmpeg"], False, 1))

    def test_no_installs_nothing(self):
        ran, shown, exited = self.scenario(["n"])
        self.assertEqual((ran, exited), ([], True))

    def test_nothing_without_a_terminal_or_with_install_no(self):
        self.assertEqual(self.scenario([], tty=False), ([], [], True))
        self.assertEqual(self.scenario([], mode="no"), ([], [], True))

    def test_homebrew_is_offered_first_and_always_asked(self):
        ran, shown, exited = self.scenario(["y", "y"], brew=False)
        self.assertFalse(exited)
        self.assertIn("install.sh", ran[0])
        self.assertTrue(ran[-1].endswith("install ffmpeg"))
        ran, shown, exited = self.scenario(["y"], brew=False, mode="yes")  # --install yes: packages not asked, Homebrew is
        self.assertEqual(len(shown), 1)
        ran, shown, exited = self.scenario(["y", "n"], brew=False)
        self.assertEqual((ran, exited), ([], True))

    def test_latex_is_never_offered(self):
        self.assertEqual(self.scenario([], missing=("latex",)), ([], [], True))


class CommandLine(unittest.TestCase):
    def run_script(self, *args, **kw):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, **kw)

    def test_version_and_help(self):
        r = self.run_script("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn(m.__version__, r.stdout)
        self.assertEqual(self.run_script("--help").returncode, 0)

    def test_check_runs_and_names_the_engine(self):
        r = self.run_script("--check")  # the exit code depends on what is installed; the report must be there
        self.assertIn("engine:", r.stdout)
        self.assertIn("python", r.stdout)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class SmokeTest(unittest.TestCase):
    def test_a_lecture_without_slides(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:d=6:r=5", str(d / "v.mp4")], check=True)
            (d / "w.json").write_text(json.dumps({"language": "en", "segments": [
                {"start": 0, "end": 3, "text": " Hello and welcome to this lecture about algorithms and sorting."},
                {"start": 3.5, "end": 6, "text": " Today we discuss merge sort and quick sort in detail."}]}), encoding="utf-8")
            r = subprocess.run([sys.executable, "-B", str(SCRIPT), str(d / "v.mp4"), "--whisper-json", str(d / "w.json"),
                                "-o", str(d / "out"), "--video-sync", "off", "--latex", "no", "--install", "no"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            notes = (d / "out" / "v.notes.md").read_text(encoding="utf-8")
            self.assertIn("merge sort", notes)
            self.assertTrue((d / "out" / "v.transcript.md").exists())
            self.assertFalse(list((d / "out").glob("*.pdf")))  # --latex no: no PDF


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class NotesPartsEndToEnd(unittest.TestCase):
    """--notes-only and --notes-parts: the prompt and the folder are prepared (Claude Code itself is not run)."""

    def run_script(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args, "--install", "no", "--notes-ai", "none"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)

    def test_parts_from_an_earlier_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:d=6:r=5", str(d / "v.mp4")], check=True)
            (d / "w.json").write_text(json.dumps({"language": "en", "segments": [
                {"start": 0, "end": 3, "text": " Hello and welcome to this lecture about algorithms and sorting."}]}), encoding="utf-8")
            out = d / "out"
            # without an earlier run there is nothing to use
            r = self.run_script(str(d / "v.mp4"), "-o", str(out), "--notes-only")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("does not exist", r.stdout + r.stderr)
            # the earlier run, then notes in two parts from it, replacing a one-document version
            r = self.run_script(str(d / "v.mp4"), "--whisper-json", str(d / "w.json"), "-o", str(out), "--video-sync", "off", "--latex", "no")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            notes = out / "reading-notes"
            notes.mkdir()
            (notes / "v.reading-notes.md").write_text("# The old single document\n", encoding="utf-8")
            r = self.run_script(str(d / "v.mp4"), "-o", str(out), "--notes-only", "--notes-parts", "00:03")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            prompt = (notes / "PROMPT.md").read_text(encoding="utf-8")
            self.assertIn("2 separate sets of notes", prompt)
            self.assertIn("v.part1.reading-notes.md", prompt)
            self.assertIn("v.part2.reading-notes.md", prompt)
            self.assertIn("00:03", prompt)
            self.assertIn("previous-part", prompt)  # the revise text for parts
            self.assertEqual((notes / "inputs" / "previous-notes.md").read_text(encoding="utf-8"), "# The old single document\n")
            self.assertIn("build-kit 2", (notes / "build" / "build.sh").read_text(encoding="utf-8"))
            self.assertTrue((notes / "inputs" / "transcript.md").exists())  # the AI works from its own folder only
            self.assertIn("inputs/transcript.md", prompt)
            self.assertIn("Work only in this folder", prompt)
            self.assertFalse((out / "v.notes.md").stat().st_mtime > (notes / "PROMPT.md").stat().st_mtime)  # nothing was rewritten


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe") and sys.platform != "win32", "needs ffmpeg and sh")
class AIEndToEnd(unittest.TestCase):
    """The whole control flow with a fake `agy` (a shell script that writes the notes file and emits agy's events)."""

    def test_notes_written_by_a_fake_agy(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:d=6:r=5", str(d / "v.mp4")], check=True)
            (d / "w.json").write_text(json.dumps({"language": "en", "segments": [
                {"start": 0, "end": 3, "text": " Hello and welcome to this lecture about algorithms and sorting."}]}), encoding="utf-8")
            fake = d / "fake-agy"
            fake.write_text('#!/bin/sh\n'
                            'echo \'{"event":"step_update","step_update":{"step_type":"tool","state":"ACTIVE","tool_name":"run_command",'
                            '"tool_info":{"parameters":{"CommandLine":"echo hi"}}}}\'\n'
                            'echo "# the notes" > v.reading-notes.md\n'
                            'echo \'{"event":"result","result":{"status":"SUCCESS","response":"All done here","num_turns":2}}\'\n')
            fake.chmod(0o755)
            run = lambda *a: subprocess.run([sys.executable, "-B", str(SCRIPT), str(d / "v.mp4"), "-o", str(d / "out"), "--install", "no", *a],
                                            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
            r = run("--whisper-json", str(d / "w.json"), "--video-sync", "off", "--latex", "no")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = run("--notes-only", "--notes-ai", "agy", "--notes-ai-command", str(fake))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("written by Antigravity CLI (agy)", r.stdout)
            self.assertIn("run_command: echo hi", r.stdout)
            self.assertIn("All done here", r.stdout)
            self.assertIn("student notes:", r.stdout)
            folder = d / "out" / "reading-notes-agy"  # one folder per AI
            self.assertEqual((folder / "v.reading-notes.md").read_text(encoding="utf-8"), "# the notes\n")
            self.assertFalse((d / "out" / "reading-notes").exists())
            prompt = (folder / "PROMPT.md").read_text(encoding="utf-8")
            self.assertIn("About your tools", prompt)  # the note for AIs that are not Claude Code


if __name__ == "__main__":
    unittest.main()
