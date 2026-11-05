from __future__ import annotations

import unittest

from jswc.lexer import tokenize
from jswc.tokens import Kind, LexError


def kinds(source: str):
    return [(t.kind, t.value) for t in tokenize(source).tokens if t.kind is not Kind.EOF]


class LexerTests(unittest.TestCase):
    def test_identifiers_keywords_and_punctuation(self):
        self.assertEqual(
            kinds("public void Player.start(){}"),
            [
                (Kind.KEYWORD, "public"),
                (Kind.KEYWORD, "void"),
                (Kind.IDENT, "Player"),
                (Kind.PUNCT, "."),
                (Kind.IDENT, "start"),
                (Kind.PUNCT, "("),
                (Kind.PUNCT, ")"),
                (Kind.PUNCT, "{"),
                (Kind.PUNCT, "}"),
            ],
        )

    def test_operators_are_longest_match_first(self):
        self.assertEqual(
            [value for kind, value in kinds("a**b>=c!=d&&e++") if kind is Kind.OP],
            ["**", ">=", "!=", "&&", "++"],
        )

    def test_string_escapes(self):
        result = tokenize(r'"a\x20b\"c\\d\n"')
        self.assertEqual(result.tokens[0].kind, Kind.STRING)
        self.assertEqual(result.tokens[0].value, 'a b"c\\d\n')

    def test_char_literal(self):
        result = tokenize("case 'space':")
        self.assertEqual(result.tokens[0].value, "case")
        char = next(t for t in result.tokens if t.kind is Kind.CHAR)
        self.assertEqual(char.value, "space")

    def test_newlines_are_suppressed_inside_parentheses(self):
        tokens = tokenize("f(\n  1,\n  2\n)")
        self.assertNotIn(Kind.NEWLINE, [t.kind for t in tokens.tokens])

    def test_newlines_are_emitted_at_top_level(self):
        tokens = tokenize("a\nb")
        self.assertEqual([t.kind for t in tokens.tokens], [Kind.IDENT, Kind.NEWLINE, Kind.IDENT, Kind.EOF])

    def test_backslash_line_continuation(self):
        tokens = tokenize('a = "x" + \\\n    "y"')
        self.assertNotIn(Kind.NEWLINE, [t.kind for t in tokens.tokens])

    def test_comments_are_collected_not_tokenized(self):
        result = tokenize("x = 1; // trailing\n/* block\ncomment */\ny = 2;")
        self.assertEqual(len(result.comments), 2)
        self.assertEqual(result.comments[0].text, "trailing")
        self.assertTrue(result.comments[1].block)
        self.assertNotIn("trailing", [t.value for t in result.tokens])

    def test_positions_are_one_based(self):
        tokens = tokenize("ab\n  cd")
        second = next(t for t in tokens.tokens if t.value == "cd")
        self.assertEqual((second.line, second.col), (2, 3))

    def test_numbers(self):
        values = [t.value for t in tokenize("1 1.5 1e3 0xFF").tokens if t.kind is Kind.NUMBER]
        self.assertEqual(values, ["1", "1.5", "1e3", "0xFF"])

    def test_unterminated_string_is_an_error(self):
        with self.assertRaises(LexError):
            tokenize('x = "oops')


if __name__ == "__main__":
    unittest.main()
