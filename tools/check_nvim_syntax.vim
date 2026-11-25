" Checks the generated JadenScript syntax file in a real Neovim.
"
"   nvim --headless -u NONE -N -S tools/check_nvim_syntax.vim
"
" Each case names a line and a snippet; the group is read at the snippet's
" first column. Every failure prints one line and the script exits non-zero.

set nocompatible
let s:root = fnamemodify(expand('<sfile>:p'), ':h:h')
" Only this plugin and the stock runtime: a copy of the plugin in
" ~/.config/nvim would otherwise be sourced alongside this one, and whichever
" file defined a group last would decide what is being checked.
execute 'set runtimepath=' . fnameescape(s:root . '/nvim') . ',' . $VIMRUNTIME
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
      \ '        gtm.load("x");',
      \ '        _s = gtm.status;',
      \ '        Facemesh.getX(1);',
      \ '        _l = GoogleTeachableMachine.getImageLabel;',
      \ '        keySim.press("space", 0.2);',
      \ '        keySim.clickLeft(-100, 20);',
      \ '        keySim.holdRight(1, 2);',
      \ '    cloud score = 0;',
      \ '        l.delete(1);',
      \ '        for i in 1..10 { }',
      \ '        x = (a ? b : c);',
      \ 'metadata monitor Stage.score at 9,9 100x24 minimized;',
      \ 'stage: rotation=all, video=on-flipped, tempo=120;',
      \ '    private void slide(start, steps) {',
      \ '        loose { Looks.show(); } // hook: when [timer] > 2',
      \ '        // proccode draw %s at 1,2',
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
      \ [5, 'slider', 'jswMonitorMode'],
      \ [5, 'shown', 'jswMetadataFlag'],
      \ [5, 'range', 'jswMetadata'],
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
      \ [17, 'gtm', 'jswNamespace'],
      \ [17, 'load(', 'jswFunctionCall'],
      \ [18, 'status', 'jswExtensionMember'],
      \ [19, 'Facemesh', 'jswNamespace'],
      \ [19, 'getX(', 'jswFunctionCall'],
      \ [20, 'getImageLabel', 'jswExtensionMember'],
      \ [21, 'keySim', 'jswNamespace'],
      \ [21, 'press(', 'jswFunctionCall'],
      \ [22, 'clickLeft(', 'jswFunctionCall'],
      \ [23, 'holdRight(', 'jswFunctionCall'],
      \ [24, 'cloud', 'jswModifier'],
      \ [24, 'score', 'jswIdentifier'],
      \ [25, 'delete(', 'jswFunctionCall'],
      \ [26, 'in', 'jswRepeat'],
      \ [27, '?', 'jswNoTernary'],
      \ [28, 'monitor', 'jswMetadataVerb'],
      \ [28, 'minimized', 'jswMetadataFlag'],
      \ [29, 'rotation', 'jswTargetKey'],
      \ [29, 'all', 'jswTargetValue'],
      \ [29, 'on-flipped', 'jswTargetValue'],
      \ [29, 'tempo', 'jswTargetKey'],
      \ [30, 'start,', 'jswParameter'],
      \ [30, 'steps)', 'jswParameter'],
      \ [31, 'loose', 'jswLoose'],
      \ [31, 'timer]', 'jswHookMenu'],
      \ [32, '%s', 'jswProccodeHole'],
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
