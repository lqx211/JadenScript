PY ?= python3

.PHONY: help install uninstall test hello demo assets rules sample npdemo np5 np5-release np5-bank np5-piano np5-song np5-voices np5-meta serve i386-boot i386-memz np5-font np5-hex np5-probe np-scratch scratch-audit scratch-slim decompile roundtrip grammar vscode-data check vscode-ext nvim-syntax nvim-check anim3d anim3d-show anim3d-embed tmreal rv64 rv64-oracle rv64-disk rv64-bios rv64-timer rv64-font rv64-glyph rv64-cprobe rv64-smode rv64-opensbi rv64-opensbi-sb3 rv64-opensbi-boot pen-dot pen-map clean

# TurboWarp loads a build with
#   https://turbowarp.org/?project_url=http://localhost:8000/build/<x>/<x>.sb3
# and that fetch is cross-origin, so plain `python3 -m http.server` fails with no
# useful error. The root matters too: the i386 project loads tools/i386mem.js
# beside the .sb3.
serve:
	$(PY) tools/serve.py --port 8000

help:
	@echo "make install  - install the jswc command (./install.sh; needs no sudo)"
	@echo "make uninstall- remove that command and its private venv"
	@echo "make test     - run the unit test suite"
	@echo "make hello    - compile examples/hello.jsw to build/hello.sb3"
	@echo "make demo     - compile examples/demo.jsw to build/demo.sb3"
	@echo "make assets   - compile the asset-import demo"
	@echo "make rules    - compile the language-rules demo"
	@echo "make sample   - compile the 828-line reference file to build/sample.sb3"
	@echo "make npdemo   - compile the self-testing mini NotePlayer demo"
	@echo "make np5-bank - bake the GM2 sample bank NotePlayer V5 imports"
	@echo "make np5-piano- rebake only the piano, from the 3D keyboard example"
	@echo "make np5-font - decode the rv64 console font into pen strokes"
	@echo "make np5      - build the NotePlayer V5 project (bank + song)"
	@echo "make np5-release - the same project with names obfuscated, for upload"
	@echo "make i386-obf - the i386 machine with names hidden (integers stay inline)"
	@echo "make i386-memz - stage the MEMZ sample disk again and rebuild"
	@echo "make i386-boot BOOT=sector.bin | DISK=disk.img - boot something else'
	@echo "make np5-song - load another MIDI: make np5-song SONG=x.mid"
	@echo "make np5-voices - re-assign the baked song's samples to the current bank"
	@echo "make np5-meta - re-read the baked song's title/size/lyrics from its own hex"
	@echo "make serve    - serve this directory on :8000 for TurboWarp's project_url"
	@echo "                (needs CORS: turbowarp.org fetches localhost cross-origin)"
	@echo "make np5-hex  - a MIDI as hex, to paste into the project's input box"
	@echo "make np5-probe- headless health check: tick rate, voices, clones"
	@echo "make np-scratch - the Scratch edition: lowered, slimmed, audited"
	@echo "make scratch-audit - say what would stop scratch.mit.edu taking a project"
	@echo "make scratch-slim  - convert the note player's sounds to mp3 (202 -> 12 MB)"
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

# NotePlayer V5's sample bank: one sprite per GM family, looped samples baked
# to a playable length (Scratch has no loop points), and the note -> sample
# table the compiler bakes into the event stream. `SF2=` picks another font.
SF2 ?= $(HOME)/Documents/MuseScore3/SoundFonts/gm.sf2
#
# GM programs 0..7 (the piano family) then get the 3D keyboard example's piano:
# 128 samples, one per MIDI note, taken straight out of that project. `PIANO=`
# picks another per-key set: a directory of `1.wav`..`128.wav`, or an .sb3 with a
# sprite whose sounds are named by number.
PIANO ?= userspace/MIDIPlayer_Example_3DKeyboard.sb3
# `--sustain 3`: a sustaining sample is looped out to three seconds, and that
# length is also how often the player re-articulates a longer note (the voice
# replays the file when it runs out -- Scratch cannot loop a sound). Three
# seconds covers every note in ordinary music; a second and a half covered
# almost none of the held ones, which went silent instead of growing.
np5-bank:
	@test -f "$(SF2)" || { echo "the sound font is not there: $(SF2)"; \
	  echo "point SF2= at one of:"; ls -1 $(HOME)/Documents/MuseScore3/SoundFonts/*.sf2 2>/dev/null | sed 's/^/  /'; exit 2; }
	$(PY) tools/sf2_bank.py inspect $(SF2)
	$(PY) tools/sf2_bank.py build $(SF2) -o userspace/np5/bank \
	    --sustain 3 --voice ../voice.inc.jsw --prefix bank/
	$(PY) tools/sf2_bank.py piano --bank userspace/np5/bank --keys $(PIANO) \
	    --voice ../voice.inc.jsw --prefix bank/

# Only the piano. The other 449 samples came from a SoundFont that may not still
# be on the machine; this needs nothing but the piano samples themselves.
np5-piano:
	$(PY) tools/sf2_bank.py piano --bank userspace/np5/bank --keys $(PIANO) \
	    --voice ../voice.inc.jsw --prefix bank/

# The HUD's font: the same CP437 bitmap the rv64 console draws with, decoded
# into horizontal runs so a glyph is thirteen pen strokes, not 120 bit tests.
np5-font:
	$(PY) tools/font_runs.py userspace/rv64/assets/lists/font.jsa -o userspace/np5/assets/lists

# The player itself. It needs bank/synth.gm.jsw and song/song.jsw, both
# generated: the first by np5-bank, the second by np5-song.
np5:
	$(PY) -m jswc build userspace/np5/main.jsw -o build/np5.sb3

# The same project with every sprite, variable and custom block renamed. It is
# what goes to scratch.mit.edu; a plain `np5` build is what you can still read
# in the editor, so this is a separate target rather than a default.
# Integer literals are hidden too (the default `--obf-ints auto` gives each one
# a variable whose value is the constant): a note player runs on timers, not on
# constants, so the measured cost there is 0.89x -- it was 0.80x when the
# integers went into the string lists instead.
np5-release:
	$(PY) -m jswc build userspace/np5/main.jsw -o build/np5-release.sb3 \
	    --enable doObfuscation

# Load a song: `make np5-song SONG=~/Downloads/x.mid`. Ticks are 1/16 beat, so
# the conversion bakes every tempo, program and bend into the event stream and
# the runtime only walks it.
SONG ?=
# track: the example's own colour source. A single-track MIDI exports (format
# 0) then give every note one hue, which is why `auto` and `channel` exist.
HUE ?= channel
# A black MIDI asks for thousands of simultaneous voices. THIN caps the note-ons
# in one tick and VOICES caps how many may sound at once; the notes that are
# held are kept first, because those are what the piece sounds like.
THIN ?= 0
VOICES ?= 0
np5-song:
	@test -n "$(SONG)" || { echo "usage: make np5-song SONG=path/to/song.mid [HUE=…] [THIN=N] [VOICES=N]"; exit 2; }
	$(PY) tools/midi_to_fl2.py convert "$(SONG)" -o userspace/np5/song \
	    --bank userspace/np5/bank/bank.json --prefix song/ --hue $(HUE) \
	    --thin $(THIN) --voices $(VOICES)
	$(PY) -m jswc build userspace/np5/main.jsw -o build/np5.sb3

# Re-voice the baked song. The converter assigns each note a sample slot so that
# two notes sounding together never share one (scratch-audio has a single player
# per sound per sprite, so a shared slot means the second note cuts the first).
# A song baked before that existed, or baked against an older bank, only needs
# this -- the notes themselves do not change.
np5-voices:
	$(PY) tools/midi_to_fl2.py reallocate userspace/np5/song \
	    --bank userspace/np5/bank/bank.json

# Refresh what the debug block says about the baked song: its name, its size and
# its lyrics. None of it can be recovered from the note lists, and a published
# project cannot read a file -- so a song baked before the block existed is
# brought up to date from the copy of the MIDI it already carries (`Song.hex`,
# written by np5-hex) rather than from a file nobody has any more.
np5-meta:
	$(PY) tools/midi_to_fl2.py meta --hex userspace/np5/song/builtin.jsw \
	    -o userspace/np5/song --prefix song/
	$(PY) -m jswc build userspace/np5/main.jsw -o build/np5.sb3

# The project asks for a song every time it starts: paste the contents of this
# file into the box, or press Enter to play the built-in one. Hex and not Base64
# because Scratch compares a string to a string by trying numbers first and then
# lowercasing, so "0" equals " " and "A" equals "a".
np5-hex:
	@test -n "$(SONG)" || { echo "usage: make np5-hex SONG=path/to/song.mid"; exit 2; }
	@mkdir -p build
	$(PY) tools/midi_to_hex.py "$(SONG)" -o build/song.hex
	-which pbcopy >/dev/null 2>&1 && pbcopy < build/song.hex && echo "  copied to the clipboard"

# Script-side health check: is the clock keeping time, does every clone retire?
# Audio is absent, so this is not a listening test -- it cannot hear a wrong
# pitch, only a stopped clock or a clone that never dies.
np5-probe:
	node tools/np5_probe.js build/np5.sb3 $${SECONDS:-5} $${FPS:-60}

# Can scratch.mit.edu take this project, and if not, what stops it? The audit
# reads the opcode list out of the VM, so it knows what vanilla Scratch runs.
scratch-audit:
	$(PY) tools/scratch_audit.py build/npdemo.sb3 --vm scratch-vm-develop

# The Scratch edition of the note player: D3, the pseudocode pane, the console
# and named timers are lowered or left out by `--target scratch`, and the
# samples become mp3 on the way out. One source, two builds.
np-scratch:
	$(PY) -m jswc build userspace/wem_np4_26b5/wem_np4_26b5.jsw \
	    -o build/wem_np4_26b5.scratch.sb3 --target scratch
	$(PY) tools/scratch_slim.py build/wem_np4_26b5.scratch.sb3 \
	    -o build/wem_np4_26b5.scratch.slim.sb3 --bitrate 128k
	$(PY) tools/scratch_audit.py build/wem_np4_26b5.scratch.slim.sb3 ${AUDIT_VM}

# ... and the one blocker that is only a file format: sounds as mp3. Scratch
# takes 50 MB a project and 10 MB a sound; the note player is 202 MB of WAV.
scratch-slim:
	$(PY) tools/scratch_slim.py userspace/wem_np4_26b5/wem_np4_26b5.sb3 \
	    -o build/wem_np4_26b5.slim.sb3 --bitrate 128k

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
# What the staged disk holds. A whole raw disk image fits: sector 0 is the boot
# sector and the sectors after it are whatever it reads back with int 13h. The
# sample that ships is MEMZ -- its bootkit in sector 0, the cat in sectors 1-4,
# 128 sectors of room -- so the machine boots the animation on every start.
# `make i386-boot DISK=x.img` stages anything else.
MEMZ ?= userspace/i386/memz.img

# The firmware and a boot sector are flat binaries out of nasm; the image
# composer puts the ROM at 0xF0000 and a staged disk at 0x30000.
i386:
	cd userspace/i386 && nasm -f bin bios.asm -o bios.bin -l bios.lst
	cd userspace/i386 && nasm -f bin mbr.asm -o mbr.bin -l mbr.lst
	cd userspace/i386 && nasm -f bin meow.asm -o meow.bin -l meow.lst
	cd userspace/i386 && nasm -f bin ea32.asm -o ea32.bin -l ea32.lst
	cd userspace/i386 && nasm -f bin apiprobe.asm -o apiprobe.bin -l apiprobe.lst
	cd userspace/i386 && nasm -f bin mode13.asm -o mode13.bin -l mode13.lst
	cd userspace/i386 && nasm -f bin mode4.asm -o mode4.bin -l mode4.lst
	cd userspace/i386 && nasm -f bin dlprobe.asm -o dlprobe.bin -l dlprobe.lst
	cd userspace/i386 && nasm -f bin biosprobe.asm -o biosprobe.bin -l biosprobe.lst
	cd userspace/i386 && nasm -f bin waitprobe.asm -o waitprobe.bin -l waitprobe.lst
	$(PY) tools/i386_tones.py
	# the disassembler's tables: one asset and one list of names, both out of
	# tools/i386_dis.py so the two decoders cannot drift apart
	$(PY) tools/i386_dis.py --emit userspace/i386/assets/lists/dis.jsa
	$(PY) tools/i386_image.py --disk $(MEMZ) --check
	@echo "  -> disk.jsa holds $(MEMZ) (128 sectors, sector 0 = 0xAA55)"
	mkdir -p build/i386/tftp
	cp userspace/i386/meow.bin build/i386/tftp/boot.nbp
	$(PY) -m jswc build userspace/i386/i386.jsw -o build/i386/i386.sb3 --target turbowarp

# Every demo program the test-suite steps through. `i386` builds the firmware and
# the boot sectors; these are the small programs the CPU tests run in both
# machines, and a clone that only ran `i386` had sixty tests saying "assemble me
# first".
i386-extras:
	cd userspace/i386 && nasm -f bin pm16.asm -o pm16.bin -l pm16.lst
	cd userspace/i386 && nasm -f bin kernel.asm -o kernel.bin -l kernel.lst
	cd userspace/i386 && nasm -f bin sertest.asm -o sertest.bin -l sertest.lst
	cd userspace/i386 && nasm -f bin hw686.asm -o hw686.bin -l hw686.lst
	cd userspace/i386 && nasm -f bin linuxboot.asm -o linuxboot.bin -l linuxboot.lst
	cd userspace/i386 && nasm -f bin kstub32.asm -o kstub32.bin -l kstub32.lst
	cd userspace/i386 && nasm -f bin linuxdisk.asm -o linuxdisk.bin -l linuxdisk.lst
	cd userspace/i386 && nasm -f bin linuxdisk2.asm -o linuxdisk2.bin -l linuxdisk2.lst
	cd userspace/i386 && nasm -f bin fputest.asm -o fputest.bin -l fputest.lst
	cd userspace/i386 && nasm -f bin linuxinit.asm -o linuxinit.bin -l linuxinit.lst

# The kernel a real boot is measured on: Debian's 686 build. It is fetched
# because it is fifty megabytes, and decompressed on the host because the guest
# doing it itself would spend a day of emulated time before Linux printed a
# character. What comes out is the ELF a bootloader would have produced.
DEB_KERNEL ?= https://deb.debian.org/debian/pool/main/l/linux-signed-i386/linux-image-6.1.0-50-686_6.1.176-1_i386.deb

i386-vmlinux:
	mkdir -p build/i386
	@test -f build/i386/linux-image.deb || curl -fsSL -o build/i386/linux-image.deb $(DEB_KERNEL)
	$(PY) tools/mkvmlinux.py build/i386/linux-image.deb -o build/i386/vmlinux.elf
	@echo "now: python3 tools/linux_boot.py build/i386/vmlinux.elf 400000"

# GRUB, with a kernel inside it, in an image this machine's BIOS can boot: the
# menu, the keyboard and the twenty-seven megabytes of nothing a rescue floppy
# spends on modules are all the same, and now `/boot/vmlinuz` is on the volume.
i386-linuxiso: i386-extras
	mkdir -p build/i386
	@test -f build/i386/linux-image.deb || curl -fsSL -o build/i386/linux-image.deb $(DEB_KERNEL)
	$(PY) tools/mklinuxiso.py build/i386/linux-image.deb -o build/i386/linux.iso
	@echo "now: $(PY) tools/i386_disk.py build/i386/linux.iso"

# The network, in two processes: the wire the machine dials, and (for a real
# LAN address) the same wire attached to a physical interface as root.
i386-net:
	$(PY) tools/i386_net.py

IFACE ?= en10
i386-net-bridge:
	sudo $(PY) tools/i386_net.py --mode bridge --iface $(IFACE)

# iPXE is GPL and 400 KiB: it is fetched, not vendored, and only the TFTP
# directory needs it.
ipxe:
	mkdir -p build/i386/tftp
	curl -fsSL https://boot.ipxe.org/ipxe.pxe -o build/i386/tftp/ipxe.pxe
	@ls -l build/i386/tftp/ipxe.pxe

# One source, two builds. This one is what could be published to scratch.mit.edu:
# built-ins only, no extension that TurboWarp would allow.
# Boot a sector that was written elsewhere. `i386` rebuilds the disk from the
# repository's own mbr.bin, so this stages the sector *after* that and rebuilds:
#   make i386-boot BOOT=~/Downloads/nyan.bin
# then reload the TurboWarp page and click the flag.
# The hidden build of the machine. `--obf-ints none` is not cosmetic: hiding an
# integer costs a reporter at every use, and measured on this project that is
# 1.44x with the default form (one variable per constant) and 3.3x with the list
# form, against 1.0x for leaving the numbers inline. Names, decoys, split string
# pools and state machines are free by comparison. A CPU is literal-heavy and
# the point of it is speed, so it keeps the integers and hides everything else.
# `i386` (plain) and this build are frame-exact against each other for 120
# deterministic steps.
i386-obf:
	$(PY) -m jswc build userspace/i386/i386.jsw -o build/i386/i386-obf.sb3 \
	    --target turbowarp --enable doObfuscation --obf-ints none

# Stage the sample again after staging something else.
i386-memz:
	$(PY) tools/i386_image.py --disk $(MEMZ)
	$(PY) -m jswc build userspace/i386/i386.jsw -o build/i386/i386.sb3 --target turbowarp
	@echo "  -> build/i386/i386.sb3 boots $(MEMZ)"

i386-boot:
	@test -n "$(BOOT)$(DISK)" || { echo "usage: make i386-boot BOOT=sector.bin | DISK=disk.img"; exit 2; }
	$(PY) tools/i386_image.py $(if $(DISK),--disk "$(DISK)",--boot "$(BOOT)")
	$(PY) -m jswc build userspace/i386/i386.jsw -o build/i386/i386.sb3 --target turbowarp
	@echo "  -> build/i386/i386.sb3 boots $(if $(DISK),$(DISK),$(BOOT))"

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

# The command, not the checkout: `./install.sh --check` says what it would do.
# A private venv keeps PEP 668 ("externally managed") out of the way, and the
# editable install means an edit under jswc/ is live on the next run.
install:
	./install.sh

uninstall:
	./install.sh --uninstall

clean:
	rm -rf build __pycache__ jswc/__pycache__ tests/__pycache__
