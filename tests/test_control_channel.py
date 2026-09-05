import json
import tempfile
import unittest
from pathlib import Path

from nms_scanner.control_channel import CommandTail, CommandWriter


class ControlChannelTests(unittest.TestCase):
    def test_writer_and_tail_exchange_ordered_authenticated_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.jsonl"
            writer = CommandWriter(path, "a" * 32)
            writer.initialize()
            tail = CommandTail(path, "a" * 32)
            writer.send("primary")
            writer.send("upload")
            self.assertEqual(tail.poll(), ["primary", "upload"])
            self.assertEqual(tail.poll(), [])

    def test_invalid_foreign_and_replayed_commands_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.jsonl"
            path.write_text(
                "bad json\n"
                + json.dumps({"token": "wrong", "sequence": 1, "action": "stop"})
                + "\n"
                + json.dumps({"token": "good", "sequence": 2, "action": "primary"})
                + "\n"
                + json.dumps({"token": "good", "sequence": 2, "action": "stop"})
                + "\n"
                + json.dumps({"token": "good", "sequence": 3, "action": "unknown"})
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(CommandTail(path, "good").poll(), ["primary"])

    def test_partial_line_is_held_until_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.jsonl"
            path.write_bytes(b'{"token":"good","sequence":1')
            tail = CommandTail(path, "good")
            self.assertEqual(tail.poll(), [])
            with path.open("ab") as stream:
                stream.write(b',"action":"stop"}\n')
            self.assertEqual(tail.poll(), ["stop"])


if __name__ == "__main__":
    unittest.main()
