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

    def test_registration_creates_persistent_lan_firewall_rule_once(self):
        script = (Path(__file__).parents[1] / "deploy" / "register-windows-task.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-Administrator", script)
        self.assertIn("Start-Process", script)
        self.assertIn("-Verb RunAs", script)
        self.assertIn("Get-ConfiguredPort", script)
        self.assertIn("New-NetFirewallRule", script)
        self.assertIn("-Profile Any", script)
        self.assertIn("-RemoteAddress LocalSubnet", script)

    def test_monitor_keeps_per_run_logs_and_state(self):
        monitor = (Path(__file__).parents[1] / "deploy" / "run-windows-task.ps1").read_text(encoding="utf-8")
        self.assertIn('$runId =', monitor)
        self.assertIn('server-{0}-attempt{1}.stdout.log', monitor)
        self.assertIn('server-{0}-attempt{1}.stderr.log', monitor)
        self.assertIn('state.json', monitor)
        self.assertIn('last-recovery.json', monitor)
        self.assertIn('monitor_status', monitor)
        self.assertIn('monitor_status -eq "stopped"', monitor)
        self.assertIn('monitor.log', monitor)
        self.assertIn('ExitCode', monitor)
        self.assertIn('Collect-RecentApplicationErrors', monitor)
        self.assertIn('LookbackMinutes 60', monitor)
        self.assertIn('Write-RecoveryReport', monitor)
        self.assertIn('IncludeEvents', monitor)
        self.assertIn('MaxEvents 50', monitor)
        self.assertIn('Get-WinEvent', monitor)

    def test_monitor_has_log_retention_controls(self):
        monitor = (Path(__file__).parents[1] / "deploy" / "run-windows-task.ps1").read_text(encoding="utf-8")
        self.assertIn("LogRetentionDays", monitor)
        self.assertIn("MaxLogRuns", monitor)
        self.assertIn("Remove-OldLogs", monitor)
        self.assertIn("Rotate-MonitorLog", monitor)


if __name__ == "__main__":
    unittest.main()
