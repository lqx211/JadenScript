/*
 * i386mem — the 80386 board's own memory, as one flat array.
 *
 * The board wants 128 MiB, the way the RISC-V one has a gigabyte. A Scratch
 * list cannot hold that: 128 MiB is thirty-two million words, and a project
 * with a list that size is a project nobody can open. So the TurboWarp build
 * of this machine allocates real memory here, and the Scratch build keeps what
 * fits in a list. One source, two builds — `#ifdef` decides, see syntax.md.
 *
 * An .sb3 cannot carry JavaScript: the format is project.json plus assets, and
 * an extension is a *URL* in `extensionURLs`. This file is that URL, served by
 * the same little server the project comes from:
 *
 *     python3 tools/serve_cors.py
 *
 * The port is not arbitrary. TurboWarp loads an extension without asking
 * permission only from `https://extensions.turbowarp.org/` and `localhost:8000`;
 * any other URL stops the project at "Downloading assets (2/2) …" behind a
 * modal, and dismissing it kills the editor with "Permission to load extension
 * denied". So the server has to run on 8000:
 *
 *     http://localhost:8000/tools/i386mem.js
 *
 * The blocks are machine-shaped on purpose. The old path decoded a byte out of
 * a list with a divide and a shift for every read; here a byte read is one
 * `Uint8Array` index, which is also why the TurboWarp build is the fast one.
 */
class I386Mem {
    constructor(runtime) {
        this.runtime = runtime;
        this.buf = new Uint8Array(0);
        this.view = new DataView(this.buf.buffer);
        this.reads = 0;
        this.writes = 0;
    }

    getInfo() {
        const number = def => ({ type: Scratch.ArgumentType.NUMBER, defaultValue: def });
        return {
            id: 'i386mem',
            name: 'I386 Memory',
            color1: '#4b6f9e',
            color2: '#3a5679',
            blocks: [
                {
                    opcode: 'alloc',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'allocate [BYTES] bytes',
                    arguments: { BYTES: number(134217728) }
                },
                {
                    opcode: 'size',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'size'
                },
                {
                    opcode: 'read8',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'byte at [ADDR]',
                    arguments: { ADDR: number(0) }
                },
                {
                    opcode: 'write8',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'set byte at [ADDR] to [VALUE]',
                    arguments: { ADDR: number(0), VALUE: number(0) }
                },
                {
                    opcode: 'read16',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'halfword at [ADDR]',
                    arguments: { ADDR: number(0) }
                },
                {
                    opcode: 'write16',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'set halfword at [ADDR] to [VALUE]',
                    arguments: { ADDR: number(0), VALUE: number(0) }
                },
                {
                    opcode: 'read32',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'word at [ADDR]',
                    arguments: { ADDR: number(0) }
                },
                {
                    opcode: 'write32',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'set word at [ADDR] to [VALUE]',
                    arguments: { ADDR: number(0), VALUE: number(0) }
                },
                {
                    opcode: 'fill',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'fill [LEN] bytes at [ADDR] with [VALUE]',
                    arguments: { ADDR: number(0), LEN: number(0), VALUE: number(0) }
                },
                {
                    opcode: 'load',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'load [LEN] bytes at [ADDR] from list [LIST]'
                },
                {
                    opcode: 'allocdisk',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'allocate [BYTES] bytes of disk',
                    arguments: { BYTES: number(16777216) }
                },
                {
                    opcode: 'hexload',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'load hex [TEXT] into the disk at [ADDR]'
                },
                {
                    opcode: 'diskread8',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'disk byte at [ADDR]',
                    arguments: { ADDR: number(0) }
                },
                {
                    opcode: 'save',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'save [LEN] bytes at [ADDR] into list [LIST]'
                },
                {
                    opcode: 'stats',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'reads and writes'
                }
            ]
        };
    }

    /*
     * Ask for memory and take what the browser will actually give. A 128 MiB
     * request can fail on a loaded machine, and throwing here kills the whole
     * project, so the ask is halved until a buffer fits; the board reads `size`
     * back and reports what it really got. The board says so on the POST screen
     * rather than pretending.
     */
    alloc(args) {
        let bytes = Math.max(0, Math.floor(Number(args.BYTES) || 0));
        for (;;) {
            try {
                this.buf = new Uint8Array(bytes);
                break;
            } catch (err) {
                if (bytes < (1 << 20)) {
                    this.buf = new Uint8Array(0);
                    break;
                }
                bytes = Math.floor(bytes / 2);
            }
        }
        this.view = new DataView(this.buf.buffer);
        this.reads = 0;
        this.writes = 0;
        return this.buf.length;
    }

    size() {
        return this.buf.length;
    }

    read8(args) {
        const at = Number(args.ADDR) >>> 0;
        this.reads += 1;
        return at < this.buf.length ? this.buf[at] : 0;
    }

    write8(args) {
        const at = Number(args.ADDR) >>> 0;
        if (at < this.buf.length) {
            this.buf[at] = Number(args.VALUE) & 0xFF;
            this.writes += 1;
        }
    }

    /* Little-endian throughout: the 386 stores a word low byte first, and a
     * halfword that straddles the end of memory is half there and half open
     * bus, which is what a real board does rather than throwing. */
    read16(args) {
        const at = Number(args.ADDR) >>> 0;
        this.reads += 1;
        return this.buf[at] | ((this.buf[at + 1] || 0) << 8);
    }

    write16(args) {
        const at = Number(args.ADDR) >>> 0;
        const value = Number(args.VALUE) & 0xFFFF;
        if (at < this.buf.length) {
            this.buf[at] = value & 0xFF;
        }
        if (at + 1 < this.buf.length) {
            this.buf[at + 1] = value >>> 8;
            this.writes += 1;
        }
    }

    read32(args) {
        const at = Number(args.ADDR) >>> 0;
        this.reads += 1;
        if (at + 4 > this.buf.length) {
            return 0;
        }
        return this.view.getUint32(at, true);
    }

    write32(args) {
        const at = Number(args.ADDR) >>> 0;
        if (at + 4 <= this.buf.length) {
            this.view.setUint32(at, Number(args.VALUE) >>> 0, true);
            this.writes += 1;
        }
    }

    fill(args) {
        const at = Number(args.ADDR) >>> 0;
        const len = Math.max(0, Math.min(Number(args.LEN) | 0, this.buf.length - at));
        this.buf.fill(Number(args.VALUE) & 0xFF, at, at + len);
    }

    /* The boot image and the disk arrive as Scratch lists, because that is how
     * an .sb3 can carry bytes at all. This is the one crossing. */
    load(args) {
        const list = args.LIST;
        if (!list || typeof list.length !== "number" || typeof list.at !== "function") {
            return;
        }
        const at = Number(args.ADDR) >>> 0;
        const len = Math.max(0, Math.min(Number(args.LEN) | 0, this.buf.length - at,
                                              list.length));
        for (let i = 0; i < len; i++) {
            this.buf[at + i] = Number(list.at(i)) & 0xFF;
        }
    }

    save(args) {
        const list = args.LIST;
        if (!list || typeof list.replace !== "function") {
            return;
        }
        const at = Number(args.ADDR) >>> 0;
        const len = Math.max(0, Math.min(Number(args.LEN) | 0, this.buf.length - at));
        const out = new Array(len);
        for (let i = 0; i < len; i++) {
            out[i] = this.buf[at + i];
        }
        list.replace(1, out);
    }

    /* The disk, in a buffer of its own. Guest RAM and the drive are different
     * things on a real board and the guest's RAM is already the whole of the
     * buffer above; sixteen megabytes of sectors in the same array would be
     * sixteen megabytes the guest could not use.
     */
    allocdisk(args) {
        const bytes = Math.max(0, Number(args.BYTES) | 0);
        this.disk = new Uint8Array(bytes);
        return bytes;
    }

    /* A reply from tools/i386_disk.py is `D <hex>`: one sector, or sixty-four
     * of them if the request asked for that many. Decoding it here is the
     * difference between a boot and an afternoon -- the same text parsed with
     * `letterOf` in Scratch is a hundred thousand blocks a message.
     */
    hexload(args) {
        if (!this.disk) {
            return;
        }
        let text = String(args.TEXT || "");
        if (text.charCodeAt(0) === 68 && text.charCodeAt(1) === 32) {
            text = text.slice(2);           // "D "
        }
        const at = Number(args.ADDR) >>> 0;
        const count = Math.min(text.length >> 1, this.disk.length - at);
        for (let i = 0; i < count; i++) {
            this.disk[at + i] = parseInt(text.substr(i * 2, 2), 16) || 0;
        }
        this.writes += count;
    }

    diskread8(args) {
        const at = Number(args.ADDR) >>> 0;
        return this.disk && at < this.disk.length ? this.disk[at] : 0;
    }

    stats() {
        return `${this.reads}/${this.writes}`;
    }
}

Scratch.extensions.register(new I386Mem());
