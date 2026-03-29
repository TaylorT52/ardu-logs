from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from ardupilot_log_reader import Ardupilot

DEFAULT_INCLUDE_TYPES = [
    "ERR",
    "EV",
    "GPS",
    "BAT",
    "CURR",
    "CTUN",
    "RCIN",
    "RCOU",
    "MSG",
    "MODE",
    "ESC*",
    "STAT",
    "IMU",
    "ATT",
    "EKF*",
    "NKF*",
    "XKF*",
    "PM",
]
DEFAULT_EXCLUDE_TYPES = ["FMT", "FMTU", "UNIT", "MULT", "PARM"]
DEFAULT_DEMO_LOGS = [
    "pxf/copter/6-11-2014/GPS_issues1.BIN",
    "pxf/copter/6-11-2014/GPS_issues2.BIN",
    "pxf/copter/alt_hold_issues/atl_hold_rapid_climbs_1.BIN",
    "pxf/copter/test/MS5611_SPI.BIN",
    "pxf/plane/4-12-2014/329.BIN",
    "pxf/copter/27-9-2014/70.BIN",
]
ISSUE_TYPES = [
    "gps_issue",
    "failsafe_or_battery_issue",
    "altitude_control_issue",
    "sensor_or_ekf_issue",
    "no_obvious_issue",
]


@dataclass
class Evidence:
    table: str
    time: float | None
    reason: str
    score: float
    row: dict[str, Any]


@dataclass
class DetectionResult:
    log: str
    predicted_issue: str
    confidence: float
    evidence: list[Evidence]
    scores: dict[str, float]

    def top_evidence(self) -> Evidence | None:
        if not self.evidence:
            return None
        return max(self.evidence, key=lambda item: item.score)

    def summary_record(self) -> dict[str, Any]:
        top = self.top_evidence()
        return {
            "log_name": self.log,
            "predicted_issue": self.predicted_issue,
            "confidence": round(self.confidence, 2),
            "evidence_count": len(self.evidence),
            "top_evidence_table": top.table if top else "none",
            "top_evidence_time": top.time if top else None,
            "top_evidence_reason": top.reason if top else "no strong evidence",
        }

    def json_record(self) -> dict[str, Any]:
        return {
            "log": self.log,
            "predicted_issue": self.predicted_issue,
            "confidence": round(self.confidence, 3),
            "scores": {key: round(value, 3) for key, value in self.scores.items()},
            "evidence": [asdict(item) for item in self.evidence],
        }


class LogIssueDetector:
    def __init__(
        self,
        include_types: list[str] | None = None,
        exclude_types: list[str] | None = None,
    ) -> None:
        self.include_types = include_types or DEFAULT_INCLUDE_TYPES.copy()
        self.exclude_types = exclude_types or DEFAULT_EXCLUDE_TYPES.copy()

    def load_tables(self, log_path: Path) -> dict[str, pd.DataFrame]:
        parsed = Ardupilot.parse(
            log_path,
            types=self.include_types,
            nottypes=self.exclude_types,
        )

        tables: dict[str, pd.DataFrame] = {}
        for name, frame in parsed.dfs.items():
            if frame.empty:
                continue
            table = frame.copy()
            if "timestamp" in table.columns:
                table = table.sort_values("timestamp", kind="stable").reset_index(drop=True)
            else:
                table = table.reset_index(drop=True)
            tables[name] = table
        return tables

    def detect(self, log_path: Path) -> DetectionResult:
        tables = self.load_tables(log_path)
        scores = {issue: 0.0 for issue in ISSUE_TYPES if issue != "no_obvious_issue"}
        evidence_by_issue = {issue: [] for issue in scores}

        self._score_gps_issue(tables, scores, evidence_by_issue["gps_issue"])
        self._score_failsafe_or_battery_issue(tables, scores, evidence_by_issue["failsafe_or_battery_issue"])
        self._score_altitude_control_issue(tables, scores, evidence_by_issue["altitude_control_issue"])
        self._score_sensor_or_ekf_issue(tables, scores, evidence_by_issue["sensor_or_ekf_issue"])

        predicted_issue = max(scores, key=scores.get) if scores else "no_obvious_issue"
        best_score = scores.get(predicted_issue, 0.0)

        if best_score < 0.45:
            predicted_issue = "no_obvious_issue"
            confidence = 0.35
            scores["no_obvious_issue"] = confidence
            evidence = []
        else:
            confidence = min(0.99, best_score)
            scores["no_obvious_issue"] = max(0.0, 0.4 - confidence / 2)
            evidence = evidence_by_issue[predicted_issue]

        evidence = sorted(evidence, key=lambda item: item.score, reverse=True)

        return DetectionResult(
            log=str(log_path),
            predicted_issue=predicted_issue,
            confidence=confidence,
            evidence=evidence[:5],
            scores=scores,
        )

    def _score_gps_issue(
        self,
        tables: dict[str, pd.DataFrame],
        scores: dict[str, float],
        evidence: list[Evidence],
    ) -> None:
        if "ERR" in tables:
            err = tables["ERR"]
            gps_err = err[err["Subsys"].isin([7, 11, 12])]
            if not gps_err.empty:
                row = gps_err.iloc[0]
                count = len(gps_err)
                score = min(0.75, 0.55 + 0.05 * max(0, count - 1))
                scores["gps_issue"] += score
                evidence.append(
                    self._make_evidence(
                        "ERR",
                        row,
                        f"ERR rows with subsystem in [7, 11, 12] appeared {count} time(s), which often lines up with GPS/EKF loss.",
                        score,
                    )
                )

        if "GPS" in tables:
            gps = tables["GPS"]
            if "Status" in gps.columns:
                stable = gps.iloc[5:] if len(gps) > 5 else gps
                degraded = stable[stable["Status"] <= 1]
                if not degraded.empty:
                    row = degraded.iloc[0]
                    score = 0.18
                    scores["gps_issue"] += score
                    evidence.append(
                        self._make_evidence(
                            "GPS",
                            row,
                            "GPS status dropped to 1 or below after startup.",
                            score,
                        )
                    )

            if {"HDop", "NSats"}.issubset(gps.columns):
                poor_fix = gps[(gps["HDop"] >= 2.5) | (gps["NSats"] <= 5)]
                if not poor_fix.empty:
                    row = poor_fix.iloc[0]
                    score = 0.12
                    scores["gps_issue"] += score
                    evidence.append(
                        self._make_evidence(
                            "GPS",
                            row,
                            f"Poor GPS quality observed with HDop={row['HDop']} and NSats={row['NSats']}.",
                            score,
                        )
                    )

        if "EV" in tables:
            ev = tables["EV"]
            gps_events = ev[ev["Id"].isin([25])]
            if not gps_events.empty:
                row = gps_events.iloc[0]
                score = 0.12
                scores["gps_issue"] += score
                evidence.append(
                    self._make_evidence(
                        "EV",
                        row,
                        f"EV id {int(row['Id'])} appeared, which is useful as a GPS-loss-style event cue in this dataset.",
                        score,
                    )
                )

    def _score_failsafe_or_battery_issue(
        self,
        tables: dict[str, pd.DataFrame],
        scores: dict[str, float],
        evidence: list[Evidence],
    ) -> None:
        if "MSG" in tables:
            msg = tables["MSG"].copy()
            lowered = msg["Message"].astype(str).str.lower()

            failsafe = msg[lowered.str.contains("failsafe|fs off", regex=True)]
            if not failsafe.empty:
                row = failsafe.iloc[0]
                score = 0.48
                scores["failsafe_or_battery_issue"] += score
                evidence.append(
                    self._make_evidence(
                        "MSG",
                        row,
                        f"Message text contains failsafe language: '{row['Message']}'.",
                        score,
                    )
                )

            battery = msg[lowered.str.contains("battery", regex=False)]
            if not battery.empty:
                row = battery.iloc[0]
                score = 0.42
                scores["failsafe_or_battery_issue"] += score
                evidence.append(
                    self._make_evidence(
                        "MSG",
                        row,
                        f"Message text contains battery warning text: '{row['Message']}'.",
                        score,
                    )
                )

        if "ERR" in tables:
            err = tables["ERR"]
            failsafe_err = err[err["Subsys"] == 10]
            if not failsafe_err.empty:
                row = failsafe_err.iloc[0]
                score = 0.2
                scores["failsafe_or_battery_issue"] += score
                evidence.append(
                    self._make_evidence(
                        "ERR",
                        row,
                        f"ERR subsystem 10 appeared with code {int(row['ECode'])}.",
                        score,
                    )
                )

    def _score_altitude_control_issue(
        self,
        tables: dict[str, pd.DataFrame],
        scores: dict[str, float],
        evidence: list[Evidence],
    ) -> None:
        if "CTUN" not in tables:
            return

        ctun = tables["CTUN"]
        if not {"Alt", "DAlt", "CRt", "DCRt"}.issubset(ctun.columns):
            return

        alt_error = (ctun["Alt"] - ctun["DAlt"]).abs()
        climb_error = (ctun["CRt"] - ctun["DCRt"]).abs()
        alt_p95 = float(alt_error.quantile(0.95))
        climb_p95 = float(climb_error.quantile(0.95))

        if alt_p95 >= 10:
            row = ctun.iloc[int(alt_error.idxmax())]
            score = 0.55
            scores["altitude_control_issue"] += score
            evidence.append(
                self._make_evidence(
                    "CTUN",
                    row,
                    f"Altitude tracking error was large (95th percentile {alt_p95:.2f} m, max {float(alt_error.max()):.2f} m).",
                    score,
                )
            )

        if climb_p95 >= 500:
            row = ctun.iloc[int(climb_error.idxmax())]
            score = 0.3
            scores["altitude_control_issue"] += score
            evidence.append(
                self._make_evidence(
                    "CTUN",
                    row,
                    f"Climb-rate tracking error was large (95th percentile {climb_p95:.1f}, max {float(climb_error.max()):.1f}).",
                    score,
                )
            )

        if float(alt_error.max()) >= 20:
            scores["altitude_control_issue"] += 0.1

    def _score_sensor_or_ekf_issue(
        self,
        tables: dict[str, pd.DataFrame],
        scores: dict[str, float],
        evidence: list[Evidence],
    ) -> None:
        if "ERR" not in tables:
            return

        err = tables["ERR"]

        subsystem_16 = err[err["Subsys"] == 16]
        if not subsystem_16.empty:
            row = subsystem_16.iloc[0]
            count = len(subsystem_16)
            score = min(0.78, 0.5 + 0.06 * max(0, count - 1))
            scores["sensor_or_ekf_issue"] += score
            evidence.append(
                self._make_evidence(
                    "ERR",
                    row,
                    f"ERR subsystem 16 appeared {count} time(s), suggesting repeated sensor/EKF instability in this log.",
                    score,
                )
            )

        subsystem_6 = err[err["Subsys"] == 6]
        if not subsystem_6.empty:
            row = subsystem_6.iloc[0]
            score = 0.3
            scores["sensor_or_ekf_issue"] += score
            evidence.append(
                self._make_evidence(
                    "ERR",
                    row,
                    f"ERR subsystem 6 appeared with code {int(row['ECode'])}.",
                    score,
                )
            )

    def _make_evidence(
        self,
        table_name: str,
        row: pd.Series,
        reason: str,
        score: float,
    ) -> Evidence:
        payload = {
            key: self._normalize_value(value)
            for key, value in row.to_dict().items()
            if not pd.isna(value)
        }
        time = payload.get("timestamp")
        if isinstance(time, (int, float)):
            time_value: float | None = float(time)
        else:
            time_value = None
        return Evidence(
            table=table_name,
            time=time_value,
            reason=reason,
            score=round(score, 3),
            row=payload,
        )

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, (pd.Timestamp,)):
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return value
        return value


def sanitize_name(log_path: str) -> str:
    return (
        log_path.replace("\\", "_")
        .replace("/", "__")
        .replace(".BIN", "")
        .replace(".bin", "")
        .replace(" ", "_")
    )


def build_batch_stats(results: list[DetectionResult]) -> dict[str, Any]:
    err_counts: dict[str, int] = {}
    ev_counts: dict[str, int] = {}

    for result in results:
        for item in result.evidence:
            if item.table == "ERR" and "Subsys" in item.row:
                key = str(item.row["Subsys"])
                err_counts[key] = err_counts.get(key, 0) + 1
            if item.table == "EV" and "Id" in item.row:
                key = str(item.row["Id"])
                ev_counts[key] = ev_counts.get(key, 0) + 1

    summary = pd.DataFrame([result.summary_record() for result in results])
    issue_counts = summary["predicted_issue"].value_counts().to_dict() if not summary.empty else {}
    avg_evidence_count = float(summary["evidence_count"].mean()) if not summary.empty else 0.0

    return {
        "log_count": len(results),
        "issue_counts": issue_counts,
        "most_frequent_err_subsystems": dict(sorted(err_counts.items(), key=lambda item: item[1], reverse=True)[:5]),
        "most_frequent_ev_ids": dict(sorted(ev_counts.items(), key=lambda item: item[1], reverse=True)[:5]),
        "average_evidence_count": round(avg_evidence_count, 3),
    }


def resolve_logs(args: argparse.Namespace) -> list[Path]:
    paths = [Path(path) for path in args.log_paths]
    if args.demo:
        paths.extend(Path(path) for path in DEFAULT_DEMO_LOGS)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)

    if not deduped:
        raise SystemExit("Provide one or more log paths, or use --demo.")

    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a small heuristic issue detector over ArduPilot BIN logs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("log_paths", nargs="*", help="Paths to .BIN logs to analyze.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the curated 6-log demo batch from this repo.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reader/issue_demo"),
        help="Directory for the summary CSV, stats JSON, and per-log JSON files.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    log_paths = resolve_logs(args)

    detector = LogIssueDetector()
    results = [detector.detect(path) for path in log_paths]

    args.output_dir.mkdir(parents=True, exist_ok=True)

    expected_json_names = {sanitize_name(result.log) + ".json" for result in results}
    for existing_json in args.output_dir.glob("*.json"):
        if existing_json.name == "batch_stats.json":
            continue
        if existing_json.name not in expected_json_names:
            existing_json.unlink()

    summary = pd.DataFrame([result.summary_record() for result in results])
    summary_path = args.output_dir / "summary.csv"
    summary.to_csv(summary_path, index=False)

    for result in results:
        output_name = sanitize_name(result.log) + ".json"
        output_path = args.output_dir / output_name
        output_path.write_text(json.dumps(result.json_record(), indent=2), encoding="utf-8")

    stats = build_batch_stats(results)
    stats_path = args.output_dir / "batch_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    print(summary.to_string(index=False))
    print()
    print(f"Saved summary CSV to {summary_path}")
    print(f"Saved batch stats to {stats_path}")
    print(f"Saved {len(results)} per-log JSON files to {args.output_dir}")


if __name__ == "__main__":
    main()
