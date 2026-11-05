/*
 * rv64mem — a typed-array memory for the rv64 machine.
 *
 * An .sb3 cannot carry JavaScript: the format is project.json plus assets, and
 * an extension is referenced by URL in `extensionURLs`. This file is that URL.
 * It is served from the same little server the project and the disk come from,
 * so the machine keeps its own extension without hosting anything:
 *
 *     http://localhost:8000/tools/rv64mem.js
 *
 * Port 8000 is not arbitrary. TurboWarp loads an extension without asking
 * permission only from `https://extensions.turbowarp.org/` and `localhost:8000`;
 * any other URL stops the project at "Downloading assets (2/2) …" behind a
 * permission modal, and if that modal is dismissed the editor dies with
 * "Permission to load extension denied". So the server must run on 8000:
 *
 *     python3 tools/serve_cors.py
 *
 * A gigabyte here is `new Uint8Array(1 << 30)` — 1 GiB of flat memory that the
 * guest can address with one block instead of walking a Scratch list. Linux
 * wants a gigabyte; a list of a billion numbers is ten gigabytes of heap, which
 * is why this exists and the list path stays only as the fallback.
 *
 * The blocks are deliberately machine-shaped (byte, word, fill) so the core can
 * call them from memory reads and writes without any bookkeeping.
 */
class Rv64Mem {
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
            id: 'rv64mem',
            name: 'RV64 Memory',
            color1: '#3b6ea5',
            color2: '#2d547d',
            blocks: [
                {
                    opcode: 'alloc',
                    blockType: Scratch.BlockType.REPORTER,
                    text: 'allocate [BYTES] bytes',
                    arguments: { BYTES: number(8388608) }
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
                    opcode: 'copyTo',
                    blockType: Scratch.BlockType.COMMAND,
                    text: 'copy [LEN] bytes at [ADDR] into list [LIST]'
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
     * Ask for memory and get what the browser would actually give. A 1 GiB
     * request can fail on a loaded machine, and throwing here would kill the
     * whole project: the ask is halved until a buffer fits, so the board can
     * read `size` back and tell the guest what it really has.
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

    copyTo(args) {
        const list = args.LIST;
        if (!list || typeof list.replace !== 'function') {
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

    stats() {
        return `${this.reads}/${this.writes}`;
    }
}

Scratch.extensions.register(new Rv64Mem());
