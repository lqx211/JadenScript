"""How the extension namespaces are spelled.

Two rules the machine relies on, and the reason they are tests rather than
comments:

* a reporter with **no arguments** is a *property* -- ``WebSocket.closingCode``
  reads like ``Sensing.answer`` -- and a call with empty parentheses means the
  same thing, so both spellings compile and neither becomes a variable read;
* a command with **several forms** dispatches on the argument count. Closing a
  WebSocket is three blocks in the extension (no reason, code, code+reason), so
  ``closeConnection()``, ``closeConnection(1000)`` and
  ``closeConnection(1000, "bye")`` have to reach three different opcodes.

The decompiler writes back the canonical name, never the legacy one.
"""

from __future__ import annotations

import unittest

from tests.helpers import compile_source, opcodes
from jswc import extensions


def build(body: str):
    codegen, project = compile_source(f"class T {{ public void go() {{ // hook: green flag\n{body}\n}} }}")
    return codegen, project


def blocks(project, opcode: str) -> list[dict]:
    for target in project.targets:
        found = [b for b in target.blocks.values() if b.opcode == opcode]
        if found:
            return found
    return []


class WebSocketNamingTests(unittest.TestCase):
    def test_connect_to_is_new_instance(self) -> None:
        _, project = build('WebSocket.connectTo("ws://127.0.0.1:1");')
        self.assertEqual(len(blocks(project, "gsaWebsocket_newInstance")), 1)

    def test_a_zero_argument_reporter_is_a_property(self) -> None:
        _, project = build("_x = WebSocket.closingCode;")
        self.assertEqual(len(blocks(project, "gsaWebsocket_closeCode")), 1,
                         "closingCode must be the reporter, not a variable read")

    def test_the_call_spelling_means_the_same_thing(self) -> None:
        _, project = build("_x = WebSocket.closingCode();")
        self.assertEqual(len(blocks(project, "gsaWebsocket_closeCode")), 1)

    def test_a_declared_variable_of_that_name_still_wins(self) -> None:
        codegen, project = compile_source(
            'public WebSocket.closingCode = 5;\n'
            "class T { public void go() { // hook: green flag\n_x = WebSocket.closingCode;\n} }"
        )
        self.assertEqual(blocks(project, "gsaWebsocket_closeCode"), [],
                         "a project variable shadows the extension reporter")

    def test_the_state_reporters_use_the_new_names(self) -> None:
        _, project = build(
            "_a = WebSocket.receivedMessageData;\n"
            "_b = WebSocket.connectionErrored;\n"
            "_c = WebSocket.isConnectionClosed;\n"
            "_d = WebSocket.closingMessage;\n"
            "WebSocket.sendMessage(\"hi\");"
        )
        for opcode in ("gsaWebsocket_messageData", "gsaWebsocket_hasErrored",
                       "gsaWebsocket_isClosed", "gsaWebsocket_closeMessage",
                       "gsaWebsocket_sendMessage"):
            self.assertEqual(len(blocks(project, opcode)), 1, opcode)

    def test_close_connection_dispatches_on_the_argument_count(self) -> None:
        _, project = build(
            "WebSocket.closeConnection();\n"
            "WebSocket.closeConnection(1000);\n"
            "WebSocket.closeConnection(1000, \"bye\");"
        )
        self.assertEqual(len(blocks(project, "gsaWebsocket_closeWithoutReason")), 1)
        self.assertEqual(len(blocks(project, "gsaWebsocket_closeWithCode")), 1)
        self.assertEqual(len(blocks(project, "gsaWebsocket_closeWithReason")), 1)

    def test_three_arguments_is_an_error(self) -> None:
        codegen, _ = build('WebSocket.closeConnection(1, "a", "b");')
        self.assertTrue(any("E0076" in d for d in codegen.diagnostics), codegen.diagnostics)

    def test_the_reason_argument_comes_second(self) -> None:
        _, project = build('WebSocket.closeConnection(1000, "bye");')
        block = blocks(project, "gsaWebsocket_closeWithReason")[0]
        self.assertIn("CODE", block.inputs)
        self.assertIn("REASON", block.inputs)


class PointerlockNamingTests(unittest.TestCase):
    def test_set_pointer_lock_literal_one_is_the_menu_value(self) -> None:
        _, project = build("Pointerlock.setPointerLock(1);")
        menu = blocks(project, "pointerlock_menu_enabled")[0]
        self.assertEqual(menu.fields["enabled"][0], "true")
        self.assertIn("enabled", blocks(project, "pointerlock_setLocked")[0].inputs)

    def test_literal_zero_is_the_other_menu_value(self) -> None:
        _, project = build("Pointerlock.setPointerLock(0);")
        menu = blocks(project, "pointerlock_menu_enabled")[0]
        self.assertEqual(menu.fields["enabled"][0], "false")

    def test_an_expression_is_passed_through(self) -> None:
        codegen, project = build("_n = 1;\nPointerlock.setPointerLock(_n);")
        self.assertEqual(len(blocks(project, "pointerlock_setLocked")), 1)
        self.assertTrue(any("W0004" in d for d in codegen.diagnostics),
                        "a computed picker is worth a warning")

    def test_is_pointer_locked_is_a_property(self) -> None:
        for body in ("_x = Pointerlock.isPointerLocked;", "_x = Pointerlock.isPointerLocked();"):
            with self.subTest(body=body):
                _, project = build(body)
                self.assertEqual(len(blocks(project, "pointerlock_isLocked")), 1)


class TurboWarpBlocksTests(unittest.TestCase):
    """Keyboard and mouse input come from the built-in ``tw`` extension."""

    def test_the_last_key_is_a_property(self) -> None:
        _, project = build("_k = TurboWarp.lastKeyPressed;")
        self.assertEqual(len(blocks(project, "tw_getLastKeyPressed")), 1)

    def test_a_mouse_button_pins_the_menu(self) -> None:
        _, project = build("_a = TurboWarp.primaryButtonDown;\n_b = TurboWarp.secondaryButtonDown;")
        menus = blocks(project, "tw_menu_mouseButton")
        self.assertEqual(sorted(m.fields["mouseButton"][0] for m in menus), ["0", "2"])

    def test_an_expression_picks_the_button_at_run_time(self) -> None:
        _, project = build("_n = 0;\n_a = TurboWarp.isMouseButtonDown(_n);")
        self.assertEqual(len(blocks(project, "tw_getButtonIsDown")), 1)


class CanonicalSpellingTests(unittest.TestCase):
    def test_the_decompiler_writes_the_new_names(self) -> None:
        for opcode, expected in (
            ("gsaWebsocket_newInstance", "WebSocket.connectTo"),
            ("gsaWebsocket_messageData", "WebSocket.receivedMessageData"),
            ("gsaWebsocket_hasErrored", "WebSocket.connectionErrored"),
            ("gsaWebsocket_isClosed", "WebSocket.isConnectionClosed"),
            ("gsaWebsocket_closeCode", "WebSocket.closingCode"),
            ("gsaWebsocket_closeMessage", "WebSocket.closingMessage"),
            ("gsaWebsocket_closeWithoutReason", "WebSocket.closeConnection"),
            ("gsaWebsocket_closeWithCode", "WebSocket.closeConnection"),
            ("gsaWebsocket_closeWithReason", "WebSocket.closeConnection"),
            ("pointerlock_setLocked", "Pointerlock.setPointerLock"),
            ("pointerlock_isLocked", "Pointerlock.isPointerLocked"),
        ):
            with self.subTest(opcode=opcode):
                namespace, method, _call = extensions.reverse(opcode)
                self.assertEqual(f"{namespace}.{method}", expected)

    def test_the_old_names_still_compile(self) -> None:
        _, project = build(
            'WebSocket.newInstance("ws://x");\n'
            "_a = WebSocket.messageData;\n"
            "_b = WebSocket.isClosed;\n"
            "WebSocket.closeWithoutReason();"
        )
        self.assertEqual(len(blocks(project, "gsaWebsocket_newInstance")), 1)
        self.assertEqual(len(blocks(project, "gsaWebsocket_closeWithoutReason")), 1)

    def test_the_highlighters_know_the_members(self) -> None:
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        grammar = json.loads((root / "vscode-jsw" / "syntaxes" / "jsw.tmLanguage.json")
                             .read_text(encoding="utf-8"))
        patterns = grammar["repository"]["namespaces"]["patterns"]
        member = next(p for p in patterns if "known extension member" in p.get("comment", ""))
        for name in ("closingCode", "connectTo", "closeConnection", "sendMessage",
                     "isPointerLocked", "setPointerLock"):
            self.assertIn(name, member["match"], name)

        vim = (root / "nvim" / "syntax" / "jsw.vim").read_text(encoding="utf-8")
        rule = next(line for line in vim.splitlines() if "jswExtensionMember /" in line)
        for name in ("closingCode", "setPointerLock", "isPointerLocked"):
            self.assertIn(name, rule, name)


if __name__ == "__main__":
    unittest.main()
