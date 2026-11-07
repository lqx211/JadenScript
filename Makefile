PY ?= python3

.PHONY: help test hello demo assets rules sample npdemo decompile roundtrip grammar vscode-data check vscode-ext nvim-syntax nvim-check anim3d anim3d-show anim3d-embed tmreal rv64 rv64-oracle rv64-disk rv64-bios rv64-timer rv64-font rv64-glyph rv64-cprobe rv64-smode rv64-opensbi rv64-opensbi-sb3 rv64-opensbi-boot pen-dot pen-map clean

help:
	@echo "make test     - run the unit test suite"
	@echo "make hello    - compile examples/hello.jsw to build/hello.sb3"
	@echo "make demo     - compile examples/demo.jsw to build/demo.sb3"
	@echo "make assets   - compile the asset-import demo"
	@echo "make rules    - compile the language-rules demo"
	@echo "make sample   - compile the 828-line reference file to build/sample.sb3"
	@echo "make npdemo   - compile the self-testing mini NotePlayer demo"
	@echo "make anim3d   - compile the pen-only 3D engine demo"
	@echo "make anim3d-show - regenerate the running order from wem_example"
	@echo "make anim3d-embed - refresh the copies spliced into the host projects"
	@echo "make decompile- sb3 -> jsw for the demos and the reference project"
	@echo "make roundtrip- decompile + recompile and run the equality tests"
	@echo "make grammar  - regenerate the VS Code TextMate grammar"
	@echo "make vscode-data - regenerate the completion database"
	@echo "make vscode-ext - link vscode-jsw/ into ~/.vscode/extensions"
	@echo "make nvim-syntax - regenerate the Neovim plugin in nvim/"
	@echo "make nvim-check - highlight a fixture with a real nvim"
	@echo "make tmreal   - tokenise the grammar with real TextMate (needs npm install)"
	@echo "make rv64     - build the RV64 machine with the ISA payload"
	@echo "make rv64-disk- build it with the block-device payload instead"
	@echo "make rv64-bios - build it with the BIOS payload instead"
	@echo "make rv64-timer- build it with the CLINT/trap payload instead"
	@echo "make rv64-cprobe - build the compressed-instruction probe"
	@echo "make rv64-smode   - build the privilege-boundary probe"
	@echo "make rv64-opensbi - boot QEMU's OpenSBI in the reference model"
	@echo "make rv64-opensbi-sb3 - wrap it for the Scratch machine"
	@echo "make rv64-opensbi-boot - POST, then boot the staged firmware"
	@echo "make rv64-glyph- build it with the one-letter alignment test"
	@echo "make rv64-font  - rebuild the CP437 glyph table"
	@echo "make pen-dot   - build the minimal pen measurement project"
	@echo "make pen-map   - build the dot-placement probe"
	@echo "make rv64-oracle - diff the Python reference against qemu-system-riscv64"
	@echo "make check    - parse + diagnose the reference file"
	@echo "make clean    - remove build artifacts"

test:
	$(PY) -m unittest discover -s tests -t . -v

hello:
	$(PY) -m jswc build examples/hello.jsw -o build/hello.sb3

demo:
	$(PY) -m jswc build examples/demo.jsw -o build/demo.sb3

assets:
	$(PY) -m jswc build examples/assets_demo.jsw -o build/assets_demo.sb3

rules:
	$(PY) -m jswc build examples/rules_demo.jsw -o build/rules_demo.sb3

sample:
	$(PY) -m jswc build sample.jsw -o build/sample.sb3 \
		--enable commentMetadata --allow-undefined

npdemo:
	$(PY) -m jswc build examples/npdemo.jsw -o build/npdemo.sb3

anim3d:
	$(PY) -m jswc build anim3d/core.jsw -o build/preview/anim3d.sb3

anim3d-show:
	$(PY) tools/gen_anim3d_show.py

anim3d-embed:
	$(PY) tools/gen_anim3d_show.py
	$(PY) tools/embed_anim3d.py examples/anim3d_host.jsw
	$(PY) tools/embed_anim3d.py userspace/wem_np4_26b5/wem_np4_26b5.jsw
	$(PY) -m jswc build examples/anim3d_host.jsw -o build/preview/anim3d_host.sb3

rv64:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld start.S -o payload.elf
	$(PY) tools/gen_rv64_image.py userspace/rv64/payload.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/rv64/rv64.sb3

# the disk payload reads through the block window, so it needs the WebSocket
# server running: python3 tools/rv64_disk.py userspace/rv64/disk.img 65433
rv64-disk:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld disk.S -o disk.elf
	$(PY) tools/gen_rv64_image.py userspace/rv64/disk.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/rv64/rv64.sb3

# the BIOS: power-on self test on the 80x30 text plane, then halt
rv64-bios:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld bios.S -o bios.elf
	$(PY) tools/gen_rv64_image.py userspace/rv64/bios.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/rv64/rv64.sb3

# the whole interrupt path: CLINT -> MTIP -> trap -> handler -> mret
rv64-timer:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld timer.S -o timer.elf
	$(PY) tools/gen_rv64_image.py userspace/rv64/timer.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/rv64/rv64.sb3

# the CP437 glyph table: ASCII kept, box drawing and arrows generated
rv64-font:
	$(PY) tools/gen_rv64_font.py

# the privilege boundary: mret into S-mode, an SBI ecall, a delegated trap
rv64-smode:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld smode.S -o smode.elf
	$(PY) tools/gen_boot_image.py --payload userspace/rv64/smode.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/probe/smode.sb3
	$(PY) tools/gen_rv64_image.py userspace/rv64/bios.elf

# ---- i386: the 80386 line -------------------------------------------------
# The firmware and a boot sector are flat binaries out of nasm; the image
# composer puts the ROM at 0xF0000 and a staged disk at 0x30000.
i386:
	cd userspace/i386 && nasm -f bin bios.asm -o bios.bin -l bios.lst
	cd userspace/i386 && nasm -f bin mbr.asm -o mbr.bin -l mbr.lst
	cd userspace/i386 && nasm -f bin meow.asm -o meow.bin -l meow.lst
	$(PY) tools/i386_tones.py
	$(PY) tools/i386_image.py --check
	$(PY) -m jswc build userspace/i386/i386.jsw -o build/i386/i386.sb3 --target turbowarp

# One source, two builds. This one is what could be published to scratch.mit.edu:
# built-ins only, no extension that TurboWarp would allow.
i386-scratch: i386
	$(PY) -m jswc build userspace/i386/i386.jsw -o build/i386/i386-scratch.sb3 --target scratch

# the Scratch machine against the model, instruction for instruction
i386-check:
	$(PY) tools/i386_jsw_check.py --steps 30000

# the 386's protected mode, proved on the smallest program that needs it: a
# GDT, a far jump and a store through a descriptor whose base is not its
# selector. Both machines run the same bytes.
i386-pm:
	cd userspace/i386 && nasm -f bin pm16.asm -o pm16.bin
	$(PY) tools/i386_jsw_check.py --code userspace/i386/pm16.bin --steps 40 --no-build

# the smallest thing that deserves the name "kernel": protected mode, a page
# directory and a page table, CR0.PG, and six characters stored through them.
i386-kernel:
	cd userspace/i386 && nasm -f bin kernel.asm -o kernel.bin
	$(PY) tools/i386_jsw_check.py --code userspace/i386/kernel.bin --steps 6000 --no-build

# the same comparison into the setup utility: Delete stops the countdown and
# opens the setup straight away
i386-setup-check:
	$(PY) tools/i386_jsw_check.py --keys 0x53 --steps 900000 --no-build

# and through the boot menu: M stops the countdown, Down twice reaches "Reboot"
i386-menu-check:
	$(PY) tools/i386_jsw_check.py --keys 0x32,0x50,0x50 --steps 900000 --no-build

# paging, end to end: M-mode builds a table and turns satp on, S-mode reads the
# same word under two names and then faults on purpose into stvec
rv64-sv39:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld sv39.S -o sv39.elf
	$(PY) tools/gen_boot_image.py --payload userspace/rv64/sv39.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/probe/sv39.sb3
	$(PY) tools/gen_rv64_image.py userspace/rv64/bios.elf

# one letter only: the pixel-alignment test for the text plane
rv64-glyph:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld glyph.S -o glyph.elf
	$(PY) tools/gen_rv64_image.py userspace/rv64/glyph.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/rv64/rv64.sb3

# the compressed-instruction payload: run the C extension on the real machine.
# The BIOS image is put back afterwards so the tree stays bootable.
rv64-cprobe:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld cprobe.S -o cprobe.elf
	$(PY) tools/gen_boot_image.py --payload userspace/rv64/cprobe.elf
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/probe/cprobe.sb3
	$(PY) tools/gen_rv64_image.py userspace/rv64/bios.elf

# boot QEMU's OpenSBI blob in the reference model and report where it stops
rv64-opensbi:
	$(PY) tools/rv64_opensbi.py

# post, then hand off to the staged firmware the way a board does:
# make rv64-opensbi-boot -> build/probe/opensbi-boot.sb3
rv64-opensbi-boot:
	cd userspace/rv64 && zig cc -target riscv64-freestanding -nostdlib -static \
		-Wl,-T,link.ld bios.S -o bios.elf
	$(PY) tools/gen_boot_image.py
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/probe/opensbi-boot.sb3
	$(PY) tools/gen_rv64_image.py userspace/rv64/bios.elf

# the same firmware, wrapped for the Scratch machine: make rv64-opensbi-sb3
rv64-opensbi-sb3:
	$(PY) tools/gen_opensbi_image.py
	$(PY) -m jswc build userspace/rv64/rv64.jsw -o build/probe/opensbi.sb3
	$(PY) tools/gen_rv64_image.py userspace/rv64/bios.elf

# where a pen dot lands: integer vs half-integer coordinates
pen-map:
	$(PY) -m jswc build examples/pen_map.jsw -o build/pen_map.sb3

# the smallest pen test: four dots ten units apart and one letter
pen-dot:
	$(PY) -m jswc build examples/pen_dot.jsw -o build/pen_dot.sb3

rv64-oracle: rv64
	qemu-system-riscv64 -machine virt -cpu rv64 -bios none \
		-kernel userspace/rv64/payload.elf -serial file:/tmp/qemu_rv64.txt \
		-display none -no-reboot
	$(PY) tools/rv64_ref.py userspace/rv64/payload.elf

decompile: demo assets rules
	$(PY) -m jswc decompile build/demo.sb3 -o build/decompiled/demo.jsw
	$(PY) -m jswc decompile build/rules_demo.sb3 -o build/decompiled/rules_demo.jsw
	$(PY) -m jswc decompile sample_np4.26_sb3/project.json -o build/decompiled/sample.jsw

roundtrip: decompile test

grammar:
	$(PY) tools/gen_tm_grammar.py

vscode-data:
	$(PY) tools/gen_vscode_data.py

nvim-syntax:
	$(PY) tools/gen_vim_syntax.py

nvim-check:
	nvim --headless -u NONE -N -S tools/check_nvim_syntax.vim

tmreal:
	@node tools/check_grammar_real.js $(ARGS)

vscode-ext:
	@dest="$$HOME/.vscode/extensions/jaden-script"; \
	mkdir -p "$$HOME/.vscode/extensions"; \
	rm -rf "$$dest"; \
	ln -s "$(CURDIR)/vscode-jsw" "$$dest"; \
	echo "linked $$dest -> $(CURDIR)/vscode-jsw"; \
	echo "reload VS Code (Cmd+Shift+P -> Developer: Reload Window) to pick it up"

check:
	$(PY) -m jswc check sample.jsw

clean:
	rm -rf build __pycache__ jswc/__pycache__ tests/__pycache__
