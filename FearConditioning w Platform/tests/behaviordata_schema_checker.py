# -*- coding: utf-8 -*-
"""
behaviordata_schema_checker.py
------------------------------
Validates BehaviorData folder structure, metadata, file naming, and CSV
column/schema compatibility with the current behavior pipeline.

No configuration needed here — all settings are pulled from runner.py.
Just run this file.
"""

from pathlib import Path
import re
from collections import Counter

import pandas as pd

# ── Pull all config from runner.py ───────────────────────────────────────────

# Ensure parent directory is on the sys.path, as this file is in a subfolder "tests"
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import (
    BEHAVIORDATA_DIRS,
    COLUMN_ALIASES,
    COLUMN_MATCH_MODE,
    CS_DETECTION_MODE,
    TONE_STATUS_COL_CSPLUS,
    TONE_STATUS_COL_CSMINUS,
)
import utils

REPORT_OUT = None  # e.g. r"Z:\...\schema_report.txt"

SCHEMA_CHECK_CFG = {
    "column_match_mode":       COLUMN_MATCH_MODE,
    "column_aliases":          COLUMN_ALIASES,
    "cs_detection_mode":       CS_DETECTION_MODE,
    "tone_status_col_csplus":  TONE_STATUS_COL_CSPLUS,
    "tone_status_col_csminus": TONE_STATUS_COL_CSMINUS,
}

REQUIRED_METADATA_COLUMNS    = ["behavior_id"]
RECOMMENDED_METADATA_COLUMNS = ["animal_id", "treatment_group", "sex", "litter_id", "cohort_id"]


# ── ANSI color helpers ────────────────────────────────────────────────────────

USE_COLOR = True


def _c(code, text):  return f"\033[{code}m{text}\033[0m" if USE_COLOR else text
def green(t):        return _c("32", t)
def yellow(t):       return _c("33", t)
def red(t):          return _c("31", t)
def bold(t):         return _c("1",  t)
def dim(t):          return _c("2",  t)


ICONS = {"ok": green("✓"), "warn": yellow("⚠"), "error": red("✗"), "info": dim("·")}

W = 80  # line width


# ── Formatting helpers ────────────────────────────────────────────────────────

def hr(char="─"):            return char * W
def section_header(title):   return f"\n{bold(title)}\n{hr()}"
def log(lines, msg):         print(msg); lines.append(_strip_ansi(msg))
def _strip_ansi(s):          return re.sub(r"\033\[[0-9;]*m", "", s)


def fmt_table(rows, headers, col_widths, col_fmts=None):
    """
    Render a fixed-width table.
    col_fmts: list of callables (one per column) to colorize cell text, or None.
    """
    sep = "  "
    lines = [sep.join(bold(h.ljust(w)) for h, w in zip(headers, col_widths)),
             sep.join("─" * w for w in col_widths)]
    for row in rows:
        cells = []
        for i, (cell, w) in enumerate(zip(row, col_widths)):
            text = str(cell)
            colored = col_fmts[i](text) if col_fmts and col_fmts[i] else text
            # pad using uncolored length so columns align
            pad = w - len(text)
            cells.append(colored + " " * max(pad, 0))
        lines.append(sep.join(cells))
    return "\n".join(lines)


def status_cell(val):
    """Color a pass/warn/fail cell value."""
    v = str(val)
    if v in ("pass", "✓"):  return green(v)
    if v in ("warn", "⚠"):  return yellow(v)
    if v in ("fail", "✗"):  return red(v)
    return v


# ── Core helpers (normalise / parse) ─────────────────────────────────────────

def norm_colname(c):
    c = str(c).strip().lower().replace("\u00a0", " ")
    c = re.sub(r"\s+", " ", c)
    c = c.replace("(", "").replace(")", "")
    c = c.replace("+", "plus").replace("-", "minus")
    c = c.replace("/", " ").replace("\\", " ")
    return c.replace(" ", "_")


def parse_folder_bits(folder_name):
    s = folder_name.strip()
    m = re.match(r"^(d\d{2})_(context[ab])_(.+)$", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2), m.group(3)
    m = re.match(r"^Day\s+(\d+)\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        day, rest = f"d{int(m.group(1)):02d}", m.group(2).strip()
        if "_" in rest:
            ctx, sess = rest.rsplit("_", 1)
            if sess.isdigit():
                return day, ctx, sess
        return day, rest, None
    return folder_name, None, None


def day_sort_key(name):
    m = re.match(r"^d(\d{2})_", name, flags=re.IGNORECASE)
    return (int(m.group(1)), name.lower()) if m else (999, name.lower())


def find_session_dirs(bd):
    dirs = [p for p in bd.iterdir()
            if p.is_dir() and re.search(r"^d\d{2}_", p.name, flags=re.IGNORECASE)]
    return sorted(dirs, key=lambda p: day_sort_key(p.name))


def read_csv_header(path):
    try:
        df = pd.read_csv(path, nrows=5, engine="python")
    except Exception:
        df = pd.read_csv(path, nrows=5, sep=None, engine="python")
    df.columns = [norm_colname(c) for c in df.columns]
    return df


def read_metadata(path):
    meta = pd.read_excel(path)
    meta.columns = [norm_colname(c) for c in meta.columns]
    return meta


def resolve_schema_column(df, role):
    if role == "time":     return utils.find_time_col(df, SCHEMA_CHECK_CFG)
    if role == "freezing": return utils.find_freeze_col(df, SCHEMA_CHECK_CFG)
    raise ValueError(f"Unknown role: {role}")


def trial_detection_status(df):
    mode   = str(SCHEMA_CHECK_CFG.get("cs_detection_mode", "auto")).strip().lower()
    errors = []
    if mode in {"tone_status", "auto"}:
        try:
            csplus, csminus = utils.find_tone_status_cols(
                df,
                SCHEMA_CHECK_CFG.get("tone_status_col_csplus"),
                SCHEMA_CHECK_CFG.get("tone_status_col_csminus"),
                SCHEMA_CHECK_CFG,
            )
            return True, f"tone-status ({csplus}, {csminus})"
        except ValueError as e:
            errors.append(str(e))
    if mode in {"ttl", "auto"}:
        try:
            ttl = utils.find_ttl_cols(df, SCHEMA_CHECK_CFG)
            if all(ttl):
                return True, f"TTL {ttl}"
            errors.append(f"incomplete TTL cols: {ttl}")
        except ValueError as e:
            errors.append(str(e))
    return False, "; ".join(errors) or "no compatible trial-detection columns"


def suggest_fix(issue_type):
    return {
        "missing_metadata":    "Add animals_metadata.xlsx to the BehaviorData folder.",
        "missing_behavior_id": "Add or rename the behavior_id column in metadata.",
        "missing_cohort_id":   "Add cohort_id to metadata, or the folder name will be used.",
        "behavior_id_mismatch":"Rename the CSV token or metadata behavior_id so they match.",
        "missing_time":        "Rename the time column or add an alias to COLUMN_ALIASES in runner.py.",
        "missing_freezing":    "Rename the freezing column or add an alias to COLUMN_ALIASES in runner.py.",
        "trial_detection":     "Check for tone-status or TTL columns; rename or set CS_DETECTION_MODE in runner.py.",
        "bad_day_folder":      "Rename the folder to d##_context{a|b}_<session> form.",
        "bad_filename_date":   "Rename the CSV to include an MM-DD-YY date token.",
    }.get(issue_type, "Review naming conventions or update runner.py.")


# ── Individual checks ─────────────────────────────────────────────────────────

def check_metadata(bd, lines):
    """Returns (meta_df | None, issues_list)."""
    issues = []
    meta_path = bd / "animals_metadata.xlsx"

    if not meta_path.exists():
        log(lines, f"  {ICONS['error']} animals_metadata.xlsx  {red('NOT FOUND')}")
        return None, [("missing_metadata", "Missing animals_metadata.xlsx")]

    try:
        meta = read_metadata(meta_path)
    except Exception as e:
        log(lines, f"  {ICONS['error']} animals_metadata.xlsx  {red(f'READ ERROR: {e}')}")
        return None, [("missing_metadata", str(e))]

    log(lines, f"  {ICONS['ok']} animals_metadata.xlsx  "
               f"{dim(str(len(meta)))} rows, "
               f"{dim(str(len(meta.columns)))} columns")

    for col in REQUIRED_METADATA_COLUMNS:
        if col not in meta.columns:
            log(lines, f"  {ICONS['error']} Required column missing: {red(col)}")
            issues.append(("missing_behavior_id" if col == "behavior_id" else "metadata",
                           f"Missing required column: {col}"))

    for col in RECOMMENDED_METADATA_COLUMNS:
        if col not in meta.columns:
            label = "missing_cohort_id" if col == "cohort_id" else "metadata"
            log(lines, f"  {ICONS['warn']} Recommended column missing: {yellow(col)}")
            issues.append((label, f"Missing recommended column: {col}"))

    if "behavior_id" in meta.columns:
        ids = meta["behavior_id"].astype(str).str.strip()
        n_blank = int((ids == "").sum())
        if n_blank:
            log(lines, f"  {ICONS['warn']} {yellow(str(n_blank))} blank behavior_id entries")
            issues.append(("missing_behavior_id", f"{n_blank} blank behavior_id entries"))
        dupes = ids.value_counts()
        dupes = dupes[dupes > 1].index.tolist()
        if dupes:
            log(lines, f"  {ICONS['warn']} Duplicate behavior_ids: {yellow(str(dupes))}")
            issues.append(("metadata", f"Duplicate behavior_ids: {dupes}"))

    if "cohort_id" not in meta.columns:
        log(lines, f"  {ICONS['info']} No cohort_id column — folder name will be used as cohort label")
        issues.append(("missing_cohort_id", "No cohort_id column"))

    return meta, issues


def check_day_folder(day_dir, lines):
    issues = []
    day, context, session = parse_folder_bits(day_dir.name)
    if day == day_dir.name and context is None:
        log(lines, f"  {ICONS['warn']} Folder name does not match expected pattern: {yellow(day_dir.name)}")
        issues.append(("bad_day_folder", f"Unexpected folder name: {day_dir.name}"))
    else:
        log(lines, f"  {ICONS['ok']} Parsed: day={bold(day)}  context={bold(str(context))}  session={dim(str(session))}")
    return issues


def check_csv(csv_path, meta, lines):
    """
    Returns a result dict with per-check pass/warn/fail values, plus issues list.
    """
    issues = []
    fname  = csv_path.name
    result = {
        "file":           fname,
        "date_parsed":    "✗",
        "behavior_id":    "✗",
        "time_col":       "✗",
        "freezing_col":   "✗",
        "trial_detect":   "✗",
        "meta_match":     "—",
        "notes":          [],
    }

    try:
        df = read_csv_header(csv_path)
    except Exception as e:
        issues.append(("metadata", str(e)))
        result["notes"].append(f"read error: {e}")
        return result, issues

    # Date token
    try:
        date_token, behavior_id = utils.parse_filename_bits(csv_path, meta)
    except ValueError as e:
        issues.append(("behavior_id_mismatch", str(e)))
        result["notes"].append(str(e))
        return result, issues

    if date_token:
        result["date_parsed"] = "✓"
    else:
        result["date_parsed"] = "⚠"
        issues.append(("bad_filename_date", f"No date in: {fname}"))

    if behavior_id:
        result["behavior_id"] = behavior_id
    else:
        result["behavior_id"] = "⚠"
        issues.append(("behavior_id_mismatch", f"No behavior_id parsed from: {fname}"))

    # Time column
    try:
        time_col = resolve_schema_column(df, "time")
        result["time_col"] = "✓"
    except ValueError as e:
        time_col = None
        result["time_col"] = "✗"
        issues.append(("missing_time", str(e)))

    # Freezing column
    try:
        resolve_schema_column(df, "freezing")
        result["freezing_col"] = "✓"
    except ValueError as e:
        result["freezing_col"] = "✗"
        issues.append(("missing_freezing", str(e)))

    # Trial detection
    trial_ok, trial_msg = trial_detection_status(df)
    if trial_ok:
        result["trial_detect"] = "✓"
        result["notes"].append(trial_msg)
    else:
        result["trial_detect"] = "⚠"
        issues.append(("trial_detection", trial_msg))

    # Metadata match
    if behavior_id and behavior_id not in ("⚠", "✗") and meta is not None and "behavior_id" in meta.columns:
        try:
            match = utils.find_metadata_for_behavior(meta, behavior_id)
            result["meta_match"] = "✓" if not match.empty else "⚠"
            if match.empty:
                issues.append(("behavior_id_mismatch",
                               f"behavior_id '{behavior_id}' not found in metadata"))
        except ValueError as e:
            result["meta_match"] = "✗"
            issues.append(("behavior_id_mismatch", str(e)))

    return result, issues


# ── Per-folder report ─────────────────────────────────────────────────────────

def check_folder(bd, lines):
    log(lines, "")
    log(lines, bold(f"{'═' * W}"))
    log(lines, bold(f"  {bd}"))
    log(lines, bold(f"{'═' * W}"))

    folder_issues = []

    if not bd.exists():
        log(lines, red(f"  FATAL: folder does not exist"))
        return {"folder": str(bd), "exists": False,
                "n_day_dirs": 0, "n_csv": 0, "csv_rows": [],
                "issues": [("missing_folder", "Folder does not exist")]}

    # ── Metadata ──────────────────────────────────────────────────────────────
    log(lines, section_header("  Metadata"))
    meta, meta_issues = check_metadata(bd, lines)
    folder_issues.extend(meta_issues)

    # ── Session folders ───────────────────────────────────────────────────────
    day_dirs = find_session_dirs(bd)
    if not day_dirs:
        log(lines, red("  No d##_ session folders found."))
        folder_issues.append(("bad_day_folder", "No session folders found"))
        return {"folder": str(bd), "exists": True,
                "n_day_dirs": 0, "n_csv": 0, "csv_rows": [],
                "issues": folder_issues}

    csv_rows   = []  # accumulate per-CSV result dicts for the table
    n_csv      = 0

    for day_dir in day_dirs:
        log(lines, section_header(f"  {day_dir.name}"))
        folder_issues.extend(check_day_folder(day_dir, lines))

        csvs = sorted(day_dir.glob("*.csv"))
        if not csvs:
            log(lines, f"  {ICONS['warn']} No CSV files found")
            folder_issues.append(("metadata", f"No CSVs in {day_dir.name}"))
            continue

        log(lines, f"  {ICONS['info']} {len(csvs)} CSV file(s) found\n")

        for csv_path in csvs:
            n_csv += 1
            result, csv_issues = check_csv(csv_path, meta, lines)
            result["session"] = day_dir.name
            csv_rows.append(result)
            folder_issues.extend(csv_issues)

        # Print per-session CSV table
        _print_csv_table(csv_rows[-len(csvs):], lines)

    return {
        "folder":      str(bd),
        "exists":      True,
        "n_day_dirs":  len(day_dirs),
        "n_csv":       n_csv,
        "csv_rows":    csv_rows,
        "issues":      folder_issues,
    }


def _print_csv_table(rows, lines):
    headers   = ["File", "Date", "Behavior ID", "Time", "Freezing", "Trials", "Metadata"]
    col_widths = [38, 5, 16, 5, 9, 7, 9]
    col_fmts   = [None, status_cell, None, status_cell, status_cell, status_cell, status_cell]

    table_rows = []
    for r in rows:
        fname = r["file"]
        fname = fname if len(fname) <= 38 else "…" + fname[-37:]
        table_rows.append([
            fname,
            r["date_parsed"],
            r["behavior_id"],
            r["time_col"],
            r["freezing_col"],
            r["trial_detect"],
            r["meta_match"],
        ])
    log(lines, fmt_table(table_rows, headers, col_widths, col_fmts))
    log(lines, "")


# ── Final summary table ───────────────────────────────────────────────────────

def print_summary(results, lines):
    log(lines, "")
    log(lines, bold(f"{'═' * W}"))
    log(lines, bold("  SUMMARY"))
    log(lines, bold(f"{'═' * W}"))

    # Per-folder summary table
    headers    = ["Folder", "Days", "CSVs", "Errors", "Warnings", "Status"]
    col_widths = [36, 5, 5, 7, 9, 8]
    col_fmts   = [None, None, None, None, None, status_cell]

    table_rows = []
    for r in results:
        if not r["exists"]:
            table_rows.append([Path(r["folder"]).name, "—", "—", "—", "—", red("✗ missing")])
            continue
        n_err  = sum(1 for _, m in r["issues"] if "[ERROR]" in m or "[FATAL]" in m
                     or ("error" in m.lower() and "warn" not in m.lower()))
        n_warn = sum(1 for _, m in r["issues"] if "warn" in m.lower() or "⚠" in m)
        status = green("✓ clean") if not r["issues"] else (
                 red("✗ errors") if n_err else yellow("⚠ warnings"))
        name = Path(r["folder"]).name
        name = name if len(name) <= 36 else "…" + name[-35:]
        table_rows.append([name, r["n_day_dirs"], r["n_csv"], n_err, n_warn, status])

    log(lines, fmt_table(table_rows, headers, col_widths, col_fmts))

    # Issue type breakdown
    all_issues = [(t, m) for r in results for t, m in r["issues"]]
    if not all_issues:
        log(lines, f"\n  {green('No issues found across all folders.')}")
        return

    counter   = Counter(t for t, _ in all_issues)
    n_errors  = sum(1 for _, m in all_issues if "error" in m.lower() or "fatal" in m.lower())
    n_warns   = sum(1 for _, m in all_issues if "warn" in m.lower())

    log(lines, f"\n  Total issues: {red(str(n_errors))} errors,  {yellow(str(n_warns))} warnings\n")
    log(lines, bold("  Issue breakdown & suggested fixes"))
    log(lines, "  " + hr("─"))

    fix_headers    = ["Issue type", "Count", "Suggested fix"]
    fix_col_widths = [24, 6, 44]
    fix_rows = [(itype, count, suggest_fix(itype))
                for itype, count in counter.most_common()]
    log(lines, "  " + fmt_table(fix_rows, fix_headers, fix_col_widths).replace("\n", "\n  "))

    log(lines, "")
    log(lines, bold("  General options"))
    log(lines, "  " + hr("─"))
    for tip in [
        "Rename folders/files to match the expected pattern.",
        "Add missing columns to animals_metadata.xlsx.",
        "Update COLUMN_ALIASES or CS_DETECTION_MODE in runner.py.",
    ]:
        log(lines, f"  {dim('→')} {tip}")

    log(lines, "")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    lines   = []
    results = [check_folder(Path(bd), lines) for bd in BEHAVIORDATA_DIRS]
    print_summary(results, lines)

    if REPORT_OUT:
        out = Path(REPORT_OUT)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n{green('✓')} Report written to: {out}")


if __name__ == "__main__":
    main()