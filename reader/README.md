ArduPilot logs contain many message types such as `ATT`, `IMU`, `GPS`, `BARO`, and `CTUN`.

Small reader
- prints a summary of the message types found in the log
- shows a small `head()` preview for a few useful message types
- can generate an HTML report for a cleaner visual view

From the repo root:

```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN
```

filter by specific message type:

```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN --show-types ATT IMU GPS
```

Default output:
- message summary table
- per-type previews for a few high-value message types like `ATT`, `IMU`, and `GPS`

Optional output:
- `--html PATH` writes a styled HTML report
- `--csv PATH` writes a CSV
- `--wide` builds the old merged table
- `--wide` produces one exportable table (tends to be sparse).

summary CSV:
```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN --csv reader/summary.csv
```

wide CSV anyway:
```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN --wide --csv reader/wide.csv
```

GPS-style data:
```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN --show-types GPS MODE
```
