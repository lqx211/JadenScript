# Working on JadenScript

## Read and write projects through `jswc`, never by hand

`jswc` is the compiler *and* the project reader. Do not open `project.json` with
`zipfile` + `json.loads` to look at a built project — use the tool:

```python
from jswc.sb3_read import load_sb3

data = load_sb3("build/i386/i386.sb3").data        # targets, monitors, …
```

```bash
python3 -m jswc decompile build/x.sb3 -o build/x.jsw    # blocks → source
python3 -m jswc build userspace/x.jsw -o build/x.sb3 --target turbowarp
```

The same goes for writing: change the `.jsw` source, rebuild, and read the
result back with `jswc`. A hand-written `project.json` snippet is a bug report
against the compiler that nobody filed.

## Tests

`python3 -m unittest discover -s tests -t .` (or `make test`). `make i386`
regenerates the BIOS image and the disassembler tables before compiling, so a
change to `bios.asm`/`*.inc` needs it, not just `jswc build`.
