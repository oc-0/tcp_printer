import unittest
from pathlib import Path


class WindowsTaskScriptTests(unittest.TestCase):
    def test_uses_powershell_interactive_logon_type(self):
        script = (Path(__file__).parents[1] / "deploy" / "register-windows-task.ps1").read_text(encoding="utf-8")
        self.assertIn("-LogonType Interactive", script)
        self.assertNotIn("InteractiveToken", script.split("#", 1)[0])
        self.assertIn("-RunLevel Limited", script)


if __name__ == "__main__":
    unittest.main()
