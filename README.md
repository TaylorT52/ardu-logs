## ArduPilot demo
- reads `.BIN` logs into structured per-message tables
- previews the data in a cleaner, easier-to-read format
- produces coarse issue predictions with confidence scores
- attaches evidence rows and references to the tables that triggered each prediction

simple, but demo flow

## Workflow

```text
.BIN log
  -> structured tables by message type
  -> summary / preview / HTML report
  -> heuristic issue detector
  -> label + confidence + evidence rows
```

can run the reader from the repo root:

```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN
```

preview specific tables:

```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN --show-types ATT IMU GPS
```

gen an HTML report:

```bash
.venv/bin/python reader/binreader.py pxf/plane/4-12-2014/329.BIN --show-types ATT IMU GPS --html reader/329_report.html
```

detector is a small heuristic baseline that turns structured tables into a diagnosis signal.
For each log, it outputs:

- `predicted_issue`
- `confidence`
- `evidence_count`
- `top_evidence_table`
- `top_evidence_time`
- `top_evidence_reason`

It also writes per-log JSON with the exact evidence rows that triggered the prediction.
- `gps_issue`
- `failsafe_or_battery_issue`
- `altitude_control_issue`
- `sensor_or_ekf_issue`
- `no_obvious_issue`

### Example

run demo batch:

```bash
.venv/bin/python reader/issue_detector.py --demo
```

writes: 
- `reader/issue_demo/summary.csv`
- `reader/issue_demo/batch_stats.json`
- one JSON file per analyzed log in `reader/issue_demo/`

## Example demo output

The current demo batch produces outputs like:

```text
GPS_issues1.BIN                  -> gps_issue                  (0.95)
GPS_issues2.BIN                  -> gps_issue                  (0.99)
atl_hold_rapid_climbs_1.BIN      -> altitude_control_issue     (0.95)
MS5611_SPI.BIN                   -> sensor_or_ekf_issue        (0.74)
329.BIN                          -> failsafe_or_battery_issue  (0.90)
70.BIN                           -> no_obvious_issue           (0.35)
```

```json
{
  "log": "pxf/plane/4-12-2014/329.BIN",
  "predicted_issue": "failsafe_or_battery_issue",
  "confidence": 0.9,
  "evidence": [
    {
      "table": "MSG",
      "time": 1417705367.9190001,
      "reason": "Message text contains failsafe language: 'Failsafe - Short event on, '."
    },
    {
      "table": "MSG",
      "time": 1417705378.0010002,
      "reason": "Message text contains battery warning text: 'Low Battery 0.05V Used 0 mAh'."
    }
  ]
}
```

