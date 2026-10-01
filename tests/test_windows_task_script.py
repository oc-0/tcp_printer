import unittest
from pathlib import Path


class WindowsTaskScriptTests(unittest.TestCase):
    def test_uses_powershell_interactive_logon_type(self):
        script = (Path(__file__).parents[1] / "deploy" / "register-windows-task.ps1").read_text(encoding="utf-8")
        self.assertIn("-LogonType Interactive", script)
        self.assertNotIn("InteractiveToken", script.split("#", 1)[0])
        self.assertIn("-RunLevel Limited", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("-WindowStyle Hidden", script)

    def test_task_uses_health_monitor(self):
        root = Path(__file__).parents[1]
        register = (root / "deploy" / "register-windows-task.ps1").read_text(encoding="utf-8")
        monitor = (root / "deploy" / "run-windows-task.ps1").read_text(encoding="utf-8")
        self.assertIn("run-windows-task.ps1", register)
        self.assertIn("/health", monitor)
        self.assertIn("Start-Child", monitor)
        self.assertIn("MaxHealthFailures", monitor)


if __name__ == "__main__":
    unittest.main()
