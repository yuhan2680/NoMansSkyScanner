import types
import unittest
from unittest.mock import Mock

from nms_scanner.attach_framework import attach_entrypoint


class AttachTests(unittest.TestCase):
    def test_completion_cannot_terminate_game_and_globals_are_not_patched(self):
        original_os = types.SimpleNamespace(kill=Mock(), getpid=lambda: 22)
        original_pymem = types.SimpleNamespace(Pymem=Mock(return_value="connected"))
        namespace = {"os": original_os, "pymem": original_pymem, "REMOVE_SELF": True}
        exec(
            "def entry(name):\n"
            "    result = pymem.Pymem(name, exact_match=True)\n"
            "    try:\n"
            "        os.kill(123, 15)\n"
            "    except RuntimeError:\n"
            "        pass\n"
            "    return result, REMOVE_SELF, os.getpid()\n",
            namespace,
        )
        attached = attach_entrypoint(namespace["entry"], 123)
        self.assertEqual(attached("NMS.exe"), ("connected", False, 22))
        original_pymem.Pymem.assert_called_once_with(123)
        original_os.kill.assert_not_called()
        self.assertTrue(namespace["REMOVE_SELF"])
        self.assertIs(namespace["os"], original_os)
        with self.assertRaises(ValueError):
            attached("other.exe")


if __name__ == "__main__":
    unittest.main()
