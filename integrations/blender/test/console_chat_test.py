"""Run without Blender: python integrations/blender/test/console_chat_test.py."""

from contextlib import contextmanager
from pathlib import Path
from queue import Queue
import sys
from types import SimpleNamespace as NS
import unittest

sys.modules["bpy"] = NS()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from opencode_link import _console_opencode as console


class ConsoleTests(unittest.TestCase):
    def test_banner_reports_ready_once_with_model(self):
        console.bpy.data = NS(filepath="/project/shot.blend")
        chat = console.Chat()
        rows = []
        append = lambda kind, text: rows.append((kind, text))
        chat._event({"event": "session", "data": {"id": None}}, append)
        self.assertEqual(rows, [])
        chat._event({"event": "session", "data": {"id": "ses_shot", "model": "provider/model"}}, append)
        self.assertEqual(rows, [
            ("INFO", "New conversation attached."),
            ("INFO", "OpenCode | shot.blend | Ready | agent: default | model: provider/model"),
        ])

    def test_lines(self):
        lines = console.Lines()
        self.assertEqual(lines.feed("## Heading\r\n**hel"), ["Heading"])
        self.assertEqual(lines.feed("lo**\n``"), ["hello"])
        self.assertEqual(lines.feed("`py\nx = '**'\n```\nTail"), ["    x = '**'"])
        self.assertEqual(lines.feed("", end=True), ["Tail"])
        self.assertEqual(lines.feed("## prose\n"), ["prose"])

    def test_timer_broadcasts_complete_lines_only_to_opencode_areas(self):
        context = console.bpy.context = NS()

        @contextmanager
        def override(**values):
            context.area = values["area"]
            yield

        def area(language):
            result = NS(type="CONSOLE", spaces=NS(active=NS(language=language)), rows=[],
                        regions=[NS(type="WINDOW")], redraws=0)
            result.tag_redraw = lambda: setattr(result, "redraws", result.redraws + 1)
            return result

        first, second, python = area("opencode"), area("opencode"), area("python")
        context.window_manager = NS(windows=[NS(screen=NS(areas=[first, python])), NS(screen=NS(areas=[second]))])
        context.temp_override = override
        console.bpy.ops = NS(console=NS(scrollback_append=lambda **row: context.area.rows.append(row)))
        chat = console.chat = console.Chat()
        chat.pipes = NS(out=Queue(), inbox=Queue())
        chat.state = "Working"
        console._sync = lambda append: None

        def add(event, **data):
            chat.pipes.out.put({"event": event, "data": data})

        add("assistant_delta", text="first\npar")
        self.assertEqual(console._drain(), console.INTERVAL)
        self.assertEqual(first.rows, [{"text": "first", "type": "OUTPUT"}])
        add("assistant_delta", text="tial")
        add("tool", name="blender_context", state="completed", duration_ms=258)
        add("assistant_end")
        console._drain()
        self.assertEqual(first.rows[-2:], [
            {"text": "Tool: blender_context — completed (0.26 s)", "type": "INFO"},
            {"text": "partial", "type": "OUTPUT"},
        ])
        self.assertEqual(second.rows, first.rows)
        self.assertEqual(first.redraws, 2)
        self.assertEqual(python.rows, [])
        add("exit", message="")
        self.assertEqual(console._drain(), console.INTERVAL)
        self.assertIsNone(chat.pipes)
        self.assertEqual(chat.state, "Connecting")


if __name__ == "__main__":
    unittest.main()
