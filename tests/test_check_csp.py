"""``tools/check_csp.py`` — the checker has to agree with the browser.

The point of the tool is to answer "is the header the only problem?", so its
policy parsing is what matters: `script-src` falls back to `default-src`, a host
source covers its paths, and a wildcard covers everything. Getting any of that
wrong would clear a host that still blocks the page.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from check_csp import allowed, directives, effective  # noqa: E402


class DirectiveTests(unittest.TestCase):
    def test_a_policy_splits_on_semicolons(self):
        rules = directives("default-src 'self'; script-src 'self' data:; img-src *")
        self.assertEqual(rules["script-src"], ["'self'", "data:"])
        self.assertEqual(rules["img-src"], ["*"])
        self.assertEqual(len(rules), 3)

    def test_empty_segments_are_ignored(self):
        # nginx headers routinely end in ``;`` -- a trailing empty directive
        # must not become ``{“”: []}`` and shadow anything.
        self.assertEqual(directives("default-src 'self'; ;"), {"default-src": ["'self'"]})

    def test_script_src_falls_back_to_default_src(self):
        rules = directives("default-src 'self' https://cdn.jsdelivr.net")
        self.assertEqual(effective(rules, "script-src"), ["'self'", "https://cdn.jsdelivr.net"])

    def test_an_explicit_directive_wins(self):
        rules = directives("default-src *; script-src 'self'")
        self.assertEqual(effective(rules, "script-src"), ["'self'"])

    def test_no_policy_at_all_is_unrestricted(self):
        self.assertIsNone(effective(directives(""), "script-src"))


class SourceTests(unittest.TestCase):
    def test_an_exact_source_matches(self):
        self.assertTrue(allowed(["'self'", "data:"], "data:"))
        self.assertFalse(allowed(["'self'"], "data:"))

    def test_a_wildcard_covers_everything(self):
        self.assertTrue(allowed(["*"], "https://storage.googleapis.com"))

    def test_a_host_covers_its_paths(self):
        self.assertTrue(
            allowed(["https://storage.googleapis.com"], "https://storage.googleapis.com")
        )
        self.assertTrue(
            allowed(["https://storage.googleapis.com"], "https://storage.googleapis.com/x")
        )

    def test_a_longer_host_is_not_a_prefix(self):
        self.assertFalse(
            allowed(["https://storage.googleapis.com.evil.test"],
                    "https://storage.googleapis.com")
        )

    def test_a_trailing_slash_source_is_a_prefix(self):
        self.assertTrue(allowed(["https://cdn.jsdelivr.net/"], "https://cdn.jsdelivr.net"))

    def test_scheme_only_sources_cover_that_scheme(self):
        # `https:` is a legal source and does allow any https host in the browser,
        # so a host that uses it needs no change.
        self.assertTrue(allowed(["https:"], "https://cdn.jsdelivr.net"))
        self.assertFalse(allowed(["https:"], "data:"))

    def test_the_served_policy_really_is_checked(self):
        """The exact header a working host sends must pass every requirement."""
        from check_csp import CONNECT_SOURCES, SCRIPT_SOURCES

        policy = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "data: blob: https://cdn.jsdelivr.net; connect-src 'self' "
            "https://teachablemachine.withgoogle.com https://storage.googleapis.com "
            "https://cdn.jsdelivr.net ws: wss:"
        )
        rules = directives(policy)
        for want in (*SCRIPT_SOURCES, *CONNECT_SOURCES):
            directive = "script-src" if want in SCRIPT_SOURCES else "connect-src"
            with self.subTest(source=want):
                self.assertTrue(allowed(effective(rules, directive) or [], want))

    def test_the_strict_policy_that_failed_really_fails(self):
        policy = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://static.cloudflareinsights.com https://www.google.com "
            "https://www.gstatic.com; connect-src 'self' "
            "https://cloudflareinsights.com ws: wss:"
        )
        rules = directives(policy)
        self.assertFalse(allowed(effective(rules, "script-src") or [], "data:"))
        self.assertFalse(
            allowed(effective(rules, "connect-src") or [],
                    "https://storage.googleapis.com")
        )


if __name__ == "__main__":
    unittest.main()
