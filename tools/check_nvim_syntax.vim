" Checks the generated JadenScript syntax file in a real Neovim.
"
"   nvim --headless -u NONE -N -S tools/check_nvim_syntax.vim
"
" Each case names a line and a snippet; the group is read at the snippet's
" first column. Every failure prints one line and the script exits non-zero.

set nocompatible
let s:root = fnamemodify(expand('<sfile>:p'), ':h:h')
execute 'set runtimepath+=' . fnameescape(s:root . '/nvim')
filetype on
set filetype=jsw
syntax on

let s:lines = [
      \ '#enable_feature<namespacedNames>;',
      \ 'public Player.score = 0;',
      \ 'class NpCore { // Sprite: NpCore (12,34) size=100 ns=Np core',
      \ 'Empty: name="Empty", x=36, y=28, namespace="Empty";',
      \ 'metadata slider Stage.score at 7,8 120x30 shown range 0..10;',
      \ '    public void go(int n) {',
      \ '        x = join("a", b) + 2;',
      \ '        n = lengthOf(x);',
      \ '        items[2] = 0x1F;',
      \ '        _temp = f"hello {x} world";',
      \ '        Looks.say("text // not a comment");',
      \ '        Pen.setPenSize(2);',
      \ '        JSON.arrayGet(Player.notes, 1);',
      \ '        while (1) { call Player.main(); } // proccode draw %s at 1,2',
      \ '    }',
      \ '}',
      \ ]

" (line, snippet, expected group)
let s:expect = [
      \ [1, '#enable_feature', 'jswDirective'],
      \ [1, 'namespacedNames', 'jswFeature'],
      \ [2, 'public', 'jswModifier'],
      \ [2, 'Player', 'jswIdentifier'],
      \ [3, 'class', 'jswDeclaration'],
      \ [3, 'NpCore {', 'jswClassName'],
      \ [3, '//', 'jswComment'],
      \ [3, 'Sprite:', 'jswAnnotationWord'],
      \ [4, 'Empty', 'jswTarget'],
      \ [4, 'name=', 'jswTargetKey'],
      \ [4, '"Empty"', 'jswString'],
      \ [5, 'metadata', 'jswDeclaration'],
      \ [5, 'slider', 'jswMetadata'],
      \ [5, 'Stage', 'jswNamespace'],
      \ [6, 'void', 'jswType'],
      \ [6, 'go(', 'jswFunctionCall'],
      \ [7, 'join', 'jswBuiltin'],
      \ [7, '"a"', 'jswString'],
      \ [7, '+', 'jswOperator'],
      \ [8, 'lengthOf', 'jswBuiltin'],
      \ [9, 'items', 'jswIdentifier'],
      \ [9, '0x1F', 'jswNumber'],
      \ [10, '_temp', 'jswTemp'],
      \ [10, 'f"hello', 'jswFString'],
      \ [10, '{x}', 'jswHole'],
      \ [11, 'Looks', 'jswNamespace'],
      \ [11, 'say(', 'jswFunctionCall'],
      \ [11, '"text', 'jswString'],
      \ [11, '//', 'jswString'],
      \ [12, 'setPenSize', 'jswFunctionCall'],
      \ [13, 'JSON', 'jswNamespace'],
      \ [14, 'while', 'jswRepeat'],
      \ [14, 'call', 'jswStatement'],
      \ [14, 'main(', 'jswFunctionCall'],
      \ [13, 'notes', 'jswMember'],
      \ [14, 'proccode', 'jswAnnotationWord'],
      \ ]

call setline(1, s:lines)

let s:failures = 0
for [s:line, s:snippet, s:want] in s:expect
  let s:col = stridx(getline(s:line), s:snippet) + 1
  if s:col < 1
    let s:failures += 1
    echo printf('FAIL %d: snippet %s not on the line', s:line, s:snippet)
    continue
  endif
  let s:name = synIDattr(synID(s:line, s:col, 1), 'name')
  if s:name !=# s:want
    let s:failures += 1
    echo printf('FAIL %d:%d %-14s want %-20s got %s', s:line, s:col, s:snippet,
          \ s:want, empty(s:name) ? '(none)' : s:name)
  endif
endfor

if s:failures > 0
  echo s:failures . ' of ' . len(s:expect) . ' expectations failed'
  cquit 1
endif
echo 'nvim syntax OK (' . len(s:expect) . ' scopes)'
qall!
