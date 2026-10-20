"""Exercise the evaluation key loader without Docker or actual credentials."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
$ErrorActionPreference = 'Stop'
$taskTokens = $null; $taskErrors = $null
$taskAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:DB_AGENT_TEST_SCRIPT, [ref]$taskTokens, [ref]$taskErrors)
if ($taskErrors.Count) { throw 'PowerShell syntax error' }
$taskFunction = $taskAst.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Import-AgentApiKey'
}, $true)
if (!$taskFunction) { throw 'Key loader was not found' }
. ([scriptblock]::Create($taskFunction.Extent.Text))
Import-AgentApiKey -PythonExe $env:DB_AGENT_TEST_PYTHON -EnvPath $env:DB_AGENT_TEST_ENV
if ([string]$env:CMD_API_KEY -cne [string]$env:DB_AGENT_TEST_EXPECTED) {
    throw 'Unexpected key selection'
}
if ($env:DB_AGENT_TEST_OTHER -ne 'inherited') { throw 'Unrelated variable changed' }
Write-Output 'ok'
"""


MODE_GUARD_HARNESS = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:DB_AGENT_TEST_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'PowerShell syntax error' }
$guard = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-AgentModeEnvironment'
}, $true)
if (!$guard) { throw 'Mode guard was not found' }
. ([scriptblock]::Create($guard.Extent.Text))
$env:AGENT_MODE = $env:DB_AGENT_TEST_MODE
try {
    Assert-AgentModeEnvironment
    Write-Output 'allowed'
} catch {
    Write-Output "rejected: $($_.Exception.Message)"
}
Write-Output "value: $env:AGENT_MODE"
"""


class AgentModeGuardTests(unittest.TestCase):
    def check_guard(self, mode: str) -> str:
        child_env = os.environ.copy()
        child_env.update({
            'DB_AGENT_TEST_SCRIPT': str(ROOT / 'scripts/run-evaluation.ps1'),
            'DB_AGENT_TEST_MODE': mode,
            'PYTHONUTF8': '1',
        })
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', MODE_GUARD_HARNESS],
            cwd=ROOT, env=child_env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_empty_and_supported_modes_are_allowed(self):
        for mode in ('', 'tools', ' tools '):
            with self.subTest(mode=mode):
                self.assertIn('allowed', self.check_guard(mode))

    def test_removed_or_unknown_modes_are_rejected_with_the_value_and_fix(self):
        for mode in ('fixed', 'FIXED', 'unknown'):
            with self.subTest(mode=mode):
                output = self.check_guard(mode)
                self.assertIn('rejected', output)
                self.assertIn(mode, output)
                self.assertIn('Remove-Item Env:AGENT_MODE', output)

    def test_the_guard_does_not_clear_the_environment(self):
        self.assertIn('value: fixed', self.check_guard('fixed'))


class EvaluationEnvTests(unittest.TestCase):
    def check_loader(self, content, inherited_key, expected):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / '.env'
            if content is not None:
                env_path.write_text(content, encoding='utf-8-sig')
            child_env = os.environ.copy()
            child_env.pop('CMD_API_KEY', None)
            if inherited_key is not None:
                child_env['CMD_API_KEY'] = inherited_key
            child_env.update({
                'DB_AGENT_TEST_SCRIPT': str(ROOT / 'scripts/run-evaluation.ps1'),
                'DB_AGENT_TEST_PYTHON': sys.executable,
                'DB_AGENT_TEST_ENV': str(env_path),
                'DB_AGENT_TEST_EXPECTED': expected,
                'DB_AGENT_TEST_OTHER': 'inherited',
                'PYTHONUTF8': '1',
            })
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', HARNESS],
                cwd=directory, env=child_env, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ok')
        self.assertNotIn('fake-', result.stdout + result.stderr)

    def test_dotenv_key_is_loaded_without_output(self):
        self.check_loader(
            '# Comment\nCMD_API_KEY="fake-dotenv-key"\nDB_AGENT_TEST_OTHER=dotenv\n',
            None, 'fake-dotenv-key',
        )

    def test_existing_environment_key_takes_priority(self):
        self.check_loader('CMD_API_KEY=fake-dotenv-key\n', 'fake-inherited-key', 'fake-inherited-key')

    def test_missing_dotenv_leaves_the_key_unset(self):
        self.check_loader(None, None, '')

    def test_dotenv_without_key_leaves_the_key_unset(self):
        self.check_loader('DB_AGENT_TEST_OTHER=dotenv\n', None, '')


if __name__ == '__main__':
    unittest.main()
