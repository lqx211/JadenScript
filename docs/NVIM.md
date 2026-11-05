# Neovim / Vim

Yes — and the repo now ships a plugin instead of a snippet to paste.

```
nvim/
  ftdetect/jsw.vim     *.jsw -> filetype jsw
  ftplugin/jsw.vim     // comments, .jsw suffix search
  syntax/jsw.vim       the highlight rules
```

`nvim/syntax/jsw.vim` is **generated** from `jswc/keywords.py` by
`tools/gen_vim_syntax.py` (`make nvim-syntax`), the same source the VS Code
grammar and the completion database use. A test regenerates it and compares, so
a keyword added to the compiler cannot silently disappear from the editor.

## Install

Copy the folder (or link it):

```sh
# Neovim
cp -r nvim/syntax nvim/ftdetect nvim/ftplugin ~/.config/nvim/

# or keep one copy and point Neovim at the repo
echo 'set runtimepath+=/path/to/JS2Twrp/nvim' >> ~/.config/nvim/init.vim
```

Vim 8 works the same way with `~/.vim/`. `:set ft?` on a `.jsw` file should
answer `filetype=jsw`, and `:syntax list jswKeyword` should list the words.

`make nvim-check` runs a real `nvim` over a fixture and asserts the syntax
group of 36 tokens — the only way to know a Vim regex really matches.

## What is highlighted

| group | links to | covers |
| --- | --- | --- |
| `jswNamespace` | `Structure` | `Looks`, `Pen`, `JSON`, `Variables`, `microbit`, … |
| `jswFunctionCall` | `Function` | `say(`, `setPenSize(`, `main(` |
| `jswBuiltin` | `Function` | `join`, `lengthOf`, `string`, `float`, … |
| `jswTemp` | `Identifier` | `_temp` |
| `jswMember` | `Identifier` | the `notes` in `Player.notes` |
| `jswClassName` | `Type` | the name after `class` |
| `jswRepeat` / `jswConditional` / `jswStatement` | `Repeat` / `Conditional` / `Statement` | `for`, `if`, `return` |
| `jswAnnotationWord` | `SpecialComment` | `// proccode`, `// hook:`, `// Sprite:` |
| `jswTarget` / `jswTargetKey` / `jswMetadata` | `PreProc` / `Identifier` / `Statement` | `Empty: name=…`, `metadata … at 1,2` |

Colours therefore come from your colourscheme; override any group in
`after/syntax/jsw.vim` if you want a specific hue:

```vim
hi jswNamespace guifg=#7fd1ff
hi jswTemp      guifg=#c792ea gui=italic
```

## Recommendations

**Diagnostics** — `jswc check --format=efm` prints one
`file:line:col: T: message (CODE)` line per diagnostic, which is a plain
`errorformat`, so no language server is needed:

```lua
-- efm-langserver
require("lspconfig").efm.setup({
  filetypes = { "jsw" },
  init_options = { documentFormatting = false, languages = { jsw = { {
    lintCommand = "jswc check --format=efm %:p",
    lintFormat  = "%f:%l:%c: %t: %m",
    lintSource  = "jswc",
  } } } },
})
```

```lua
-- nvim-lint
require("lint").linters.jswc = {
  cmd = "jswc",
  args = { "check", "--format=efm" },
  stdin = false,
  stream = "stdout",
  ignore_exitcode = true,
  parser = require("lint.parser").from_errorformat("%f:%l:%c: %t: %m", { source = "jswc" }),
}
require("lint").linters_by_ft = { jsw = { "jswc" } }
```

`--format=json` gives the same rows as objects
(`{"file", "line", "col", "code", "severity", "message"}`) if you would rather
build the list yourself. Both machine formats stay silent on a clean file and
exit 0, so a linter never shows stale noise.

**Completion** — the namespace/member data lives in
`vscode-jsw/data/jsw.json` and is plain JSON, so a small `omnifunc` can read
it. Until that exists, `cmp-buffer` and `cmp-nvim-lsp` cover most of it, and
`:help i_CTRL-N` still works.

**Treesitter** — a real parser would give better structural selection and
folding, but it needs a `grammar.js` plus a compiled C library in every
user's tree. The language is keyword-heavy and regex-friendly, so the shipped
syntax file covers the visible cases; the generator can emit Treesitter
queries later without changing this plugin.

**Formatting** — `jswc` writes the decompiled source in its own canonical
style, so `:!jswc decompile % -o %` is a usable "format" (it re-emits the same
project, and the A/B comparison is what the round-trip tests check).
