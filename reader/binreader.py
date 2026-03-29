from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import pandas as pd
from ardupilot_log_reader import Ardupilot

DEFAULT_EXCLUDE_TYPES = ["FMT", "FMTU", "UNIT", "MULT", "PARM", "MSG"]
DEFAULT_PREVIEW_PRIORITY = ["ATT", "IMU", "GPS", "BARO", "CTUN", "MODE", "AHR2", "EKF1", "EKF2"]


@dataclass
class ArduPilotBinTableReader:
    bin_path: Path
    include_types: list[str] = field(default_factory=lambda: ["*"])
    exclude_types: list[str] = field(default_factory=lambda: DEFAULT_EXCLUDE_TYPES.copy())
    _log: Ardupilot | None = field(default=None, init=False, repr=False)
    _tables: dict[str, pd.DataFrame] | None = field(default=None, init=False, repr=False)

    def load_log(self) -> Ardupilot:
        if not self.bin_path.exists():
            raise FileNotFoundError(f"BIN file not found: {self.bin_path}")

        if self._log is None:
            self._log = Ardupilot.parse(
                self.bin_path,
                types=self.include_types,
                nottypes=self.exclude_types or None,
            )

        return self._log

    def message_tables(self) -> dict[str, pd.DataFrame]:
        if self._tables is None:
            tables: dict[str, pd.DataFrame] = {}

            for message_type, frame in self.load_log().dfs.items():
                if frame.empty:
                    continue

                table = frame.copy()
                if "timestamp" in table.columns:
                    table = table.sort_values("timestamp", kind="stable").reset_index(drop=True)
                else:
                    table = table.reset_index(drop=True)

                tables[message_type] = table

            self._tables = tables

        return self._tables

    def available_types(self) -> list[str]:
        return sorted(self.message_tables())

    def message_table(self, message_type: str) -> pd.DataFrame:
        tables = self.message_tables()
        if message_type not in tables:
            available = ", ".join(self.available_types())
            raise KeyError(f"Unknown message type '{message_type}'. Available types: {available}")
        return tables[message_type]

    def summary_table(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []

        for message_type, frame in self.message_tables().items():
            visible_columns = [column for column in frame.columns if column != "timestamp"]
            sample_fields = ", ".join(visible_columns[:5])
            timestamp_start = frame["timestamp"].iloc[0] if "timestamp" in frame.columns else pd.NA
            timestamp_end = frame["timestamp"].iloc[-1] if "timestamp" in frame.columns else pd.NA

            rows.append(
                {
                    "message_type": message_type,
                    "rows": len(frame),
                    "columns": len(frame.columns),
                    "timestamp_start": timestamp_start,
                    "timestamp_end": timestamp_end,
                    "sample_fields": sample_fields,
                }
            )

        if not rows:
            return pd.DataFrame(
                columns=[
                    "message_type",
                    "rows",
                    "columns",
                    "timestamp_start",
                    "timestamp_end",
                    "sample_fields",
                ]
            )

        return (
            pd.DataFrame(rows)
            .sort_values(["rows", "message_type"], ascending=[False, True], kind="stable")
            .reset_index(drop=True)
        )

    def preview_types(self, limit: int = 3) -> list[str]:
        summary = self.summary_table()
        if summary.empty:
            return []

        available = summary["message_type"].tolist()
        preferred = [message_type for message_type in DEFAULT_PREVIEW_PRIORITY if message_type in available]
        remaining = [message_type for message_type in available if message_type not in preferred]
        return (preferred + remaining)[:limit]

    def combined_table(self) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []

        for message_type, frame in self.message_tables().items():
            typed_frame = frame.copy()
            typed_frame.insert(0, "message_type", message_type)
            frames.append(typed_frame)

        if not frames:
            return pd.DataFrame(columns=["message_type", "timestamp"])

        table = pd.concat(frames, ignore_index=True, sort=False)
        if "timestamp" in table.columns:
            table = table.sort_values("timestamp", kind="stable").reset_index(drop=True)
        return table

    def render_html_report(
        self,
        output_path: Path,
        preview_types: list[str],
        head_rows: int,
        summary_rows: int,
    ) -> Path:
        summary = display_frame(self.summary_table().head(summary_rows))
        sections: list[str] = []

        for message_type in preview_types:
            frame = self.message_table(message_type)
            preview = display_frame(frame.head(head_rows))
            sections.append(
                f"""
                <section class="card">
                  <div class="section-header">
                    <h2>{escape(message_type)}</h2>
                    <p>{len(frame):,} rows x {len(frame.columns)} columns</p>
                  </div>
                  <div class="table-wrap">
                    {preview.to_html(index=False, classes="data-table", float_format=lambda value: f"{value:.6f}")}
                  </div>
                </section>
                """
            )

        html = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ArduPilot Log Report</title>
    <style>
      :root {{
        --bg: #f5efe3;
        --panel: rgba(255, 252, 245, 0.92);
        --panel-strong: #fffdf8;
        --text: #1f2933;
        --muted: #52606d;
        --accent: #b8542a;
        --accent-soft: #f2d4c7;
        --line: rgba(31, 41, 51, 0.12);
      }}

      * {{
        box-sizing: border-box;
      }}

      body {{
        margin: 0;
        font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
        color: var(--text);
        background:
          radial-gradient(circle at top left, rgba(184, 84, 42, 0.18), transparent 28%),
          radial-gradient(circle at top right, rgba(56, 127, 112, 0.16), transparent 24%),
          linear-gradient(180deg, #f7f0e2 0%, var(--bg) 100%);
      }}

      .page {{
        max-width: 1240px;
        margin: 0 auto;
        padding: 40px 24px 72px;
      }}

      .hero {{
        background: linear-gradient(135deg, rgba(255, 253, 248, 0.95), rgba(248, 235, 221, 0.92));
        border: 1px solid var(--line);
        border-radius: 24px;
        padding: 28px;
        box-shadow: 0 18px 40px rgba(31, 41, 51, 0.08);
      }}

      h1, h2 {{
        margin: 0;
        font-weight: 700;
        letter-spacing: 0.01em;
      }}

      h1 {{
        font-size: clamp(2rem, 4vw, 3.3rem);
      }}

      h2 {{
        font-size: 1.45rem;
      }}

      .hero p,
      .section-header p,
      .meta-line {{
        color: var(--muted);
      }}

      .meta-line {{
        margin-top: 10px;
        font-size: 0.98rem;
      }}

      .grid {{
        display: grid;
        gap: 18px;
        margin-top: 22px;
      }}

      .card {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 22px;
        padding: 22px;
        box-shadow: 0 12px 30px rgba(31, 41, 51, 0.06);
        overflow: hidden;
      }}

      .section-header {{
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: baseline;
        margin-bottom: 14px;
      }}

      .data-table {{
        width: 100%;
        border-collapse: collapse;
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
        font-size: 0.88rem;
        background: var(--panel-strong);
        border-radius: 14px;
        overflow: hidden;
      }}

      .data-table th {{
        background: var(--accent-soft);
        color: #51210f;
        text-align: left;
        padding: 10px 12px;
        border-bottom: 1px solid var(--line);
        position: sticky;
        top: 0;
      }}

      .data-table td {{
        padding: 9px 12px;
        border-bottom: 1px solid rgba(31, 41, 51, 0.07);
        white-space: nowrap;
      }}

      .table-wrap {{
        overflow-x: auto;
      }}

      .pill-row {{
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        margin-top: 18px;
      }}

      .pill {{
        border: 1px solid rgba(184, 84, 42, 0.2);
        background: rgba(255, 253, 248, 0.72);
        color: #6d2f16;
        border-radius: 999px;
        padding: 8px 12px;
        font-size: 0.92rem;
      }}

      @media (max-width: 760px) {{
        .page {{
          padding: 24px 14px 48px;
        }}

        .hero,
        .card {{
          padding: 18px;
          border-radius: 18px;
        }}

        .section-header {{
          display: block;
        }}
      }}
    </style>
  </head>
  <body>
    <main class="page">
      <section class="hero">
        <h1>ArduPilot Log Report</h1>
        <p class="meta-line">{escape(str(self.bin_path))}</p>
        <div class="pill-row">
          <span class="pill">{len(self.message_tables())} message types</span>
          <span class="pill">summary rows shown: {summary_rows}</span>
          <span class="pill">preview rows shown: {head_rows}</span>
        </div>
      </section>

      <section class="grid">
        <section class="card">
          <div class="section-header">
            <h2>Message Summary</h2>
            <p>Dense per-type tables are usually more useful than one wide merged table.</p>
          </div>
          <div class="table-wrap">
            {summary.to_html(index=False, classes="data-table", float_format=lambda value: f"{value:.6f}")}
          </div>
        </section>
        {''.join(sections)}
      </section>
    </main>
  </body>
</html>
"""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        return output_path


def format_timestamp(value: object) -> object:
    if pd.isna(value):
        return value

    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return value

    if timestamp <= 0:
        return f"{timestamp:.3f}"

    return pd.to_datetime(timestamp, unit="s", utc=True).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()

    for column in ("timestamp", "timestamp_start", "timestamp_end"):
        if column in display.columns:
            display[column] = display[column].map(format_timestamp)

    return display


def print_frame(title: str, frame: pd.DataFrame) -> None:
    print(title)
    print(display_frame(frame).to_string(index=False, float_format=lambda value: f"{value:.6f}"))
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read an ArduPilot .bin log into clean pandas tables by message type.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("bin_file", type=Path, help="Path to the ArduPilot .bin or .BIN file.")
    parser.add_argument(
        "--types",
        nargs="+",
        default=["*"],
        help="Message types or glob patterns to include.",
    )
    parser.add_argument(
        "--exclude-types",
        nargs="*",
        default=DEFAULT_EXCLUDE_TYPES.copy(),
        help="Message types or glob patterns to exclude.",
    )
    parser.add_argument(
        "--show-types",
        nargs="*",
        default=[],
        help="Specific message types to preview, for example IMU ATT GPS.",
    )
    parser.add_argument(
        "--preview-types",
        type=int,
        default=3,
        help="How many message types to preview when --show-types is not provided.",
    )
    parser.add_argument(
        "--summary-rows",
        type=int,
        default=20,
        help="How many rows to show in the message summary.",
    )
    parser.add_argument(
        "--head",
        type=int,
        default=5,
        help="Number of rows to print from each preview table.",
    )
    parser.add_argument(
        "--wide",
        action="store_true",
        help="Also build the old wide combined table. This is convenient for export but usually sparse.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="Optional CSV output path. Saves the wide table with --wide, otherwise saves the summary table.",
    )
    parser.add_argument(
        "--html",
        type=Path,
        help="Optional HTML report path for a cleaner visual summary.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    reader = ArduPilotBinTableReader(
        bin_path=args.bin_file,
        include_types=args.types,
        exclude_types=args.exclude_types,
    )

    summary = reader.summary_table()
    preview_types = args.show_types or reader.preview_types(args.preview_types)

    print(f"Log file: {args.bin_file}")
    print(f"Message types found: {len(summary)}")
    print()
    print_frame("Message summary", summary.head(args.summary_rows))

    for message_type in preview_types:
        frame = reader.message_table(message_type)
        title = f"{message_type} preview ({len(frame):,} rows x {len(frame.columns)} columns)"
        print_frame(title, frame.head(args.head))

    if args.wide:
        wide_table = reader.combined_table()
        print(
            "Wide table preview "
            f"({len(wide_table):,} rows x {len(wide_table.columns)} columns, expect NaNs across message types)"
        )
        print(display_frame(wide_table.head(args.head)).to_string(index=False, float_format=lambda value: f"{value:.6f}"))
        print()
    else:
        print(
            "Tip: use --show-types IMU ATT GPS to inspect specific message tables, "
            "or add --wide if you really want one merged sparse table."
        )
        print()

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        if args.wide:
            reader.combined_table().to_csv(args.csv, index=False)
            print(f"Saved wide table to {args.csv}")
        else:
            summary.to_csv(args.csv, index=False)
            print(f"Saved summary table to {args.csv}")

    if args.html:
        report_path = reader.render_html_report(
            output_path=args.html,
            preview_types=preview_types,
            head_rows=args.head,
            summary_rows=args.summary_rows,
        )
        print(f"Saved HTML report to {report_path}")


if __name__ == "__main__":
    main()
