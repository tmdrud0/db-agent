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
