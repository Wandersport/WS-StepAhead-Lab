"""Packaging and migration regressions, no network and no live-order use."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class MigrationTests(unittest.TestCase):
    def test_windows_publisher_disables_interactive_pager(self):
        text=(ROOT/'publish-to-github.ps1').read_text(encoding='utf-8')
        self.assertIn('git --no-pager diff --cached --name-only',text)
        self.assertIn('git --no-pager diff --cached --quiet',text)
        self.assertIn('git branch -M $branch',text)
        self.assertIn('Unsafe or unexpected staged file',text)
        self.assertNotIn('git push --force',text)
    def test_git_ignore_local_data_and_venv(self):
        ignore=(ROOT/'.gitignore').read_text(encoding='utf-8')
        for entry in ['data/','.venv/','.env']:
            self.assertIn(entry,ignore)
    def test_mac_launcher_requires_compatible_python(self):
        launch=(ROOT/'start.command').read_text(encoding='utf-8')
        self.assertIn('sys.version_info >= (3,11)',launch)
        self.assertIn('exec .venv/bin/stepahead start',launch)
        self.assertIn('PAPER ONLY',launch)
    def test_line_ending_policy(self):
        attrs=(ROOT/'.gitattributes').read_text(encoding='utf-8')
        self.assertIn('*.py text eol=lf',attrs)
        self.assertIn('*.bat text eol=crlf',attrs)

if __name__=='__main__':unittest.main()
