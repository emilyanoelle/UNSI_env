# -*- coding: utf-8 -*-
"""
behaviordata_schema_checker.py
------------------------------
Simple validator for BehaviorData folder structure, metadata, file naming,
and CSV column/schema compatibility with the current behavior pipeline.

Set your directories below, then run this file.
"""

from pathlib import Path
import re
from collections import Counter, defaultdict

import pandas as pd

import utils


# ── Data directories ─────────────────────────────────────────────────────────
#
# List every BehaviorData folder you want to include. Each folder should
# contain an animals_metadata.xlsx file. If cohort_id is absent, the folder
# name is used as the cohort label automatically.

BEHAVIORDATA_DIRS = [
    r"Z:\NIMH DIRP NSI\Projects\PFC Ketamine\Behavior\Fear Conditioning\Early Life Stress Cohort 1\BehaviorData",
    r"Z:\NIMH DIRP NSI\Projects\PFC Ketamine\Behavior\Fear Conditioning\Early Life Stress Cohort 2\BehaviorData",
]

# Top-level report output. Set to None to only print to console.
REPORT_OUT = None  # e.g. r"Z:\...\BehaviorData_schema_report.txt"


# ── Expected schema for the current pipeline ─────────────────────────────────
#
# The analysis code uses:
# - behavior_id to match CSV files to metadata
# - cohort_id to group outputs
# - animal_id, treatment_group, sex, litter_id for downstream labeling and plots
#
# If some fields are missing, the checker reports the impact and suggests a fix.

REQUIRED_METADATA_COLUMNS = ["behavior_id"]
RECOMMENDED_METADATA_COLUMNS = [
    "animal_id",
    "treatment_group",
    "sex",
    "litter_id",
    "cohort_id",
]

REQUIRED_CSV_HINTS = {
    "time": ["time"],
    "freezing": ["freez"],
}

# Trial detection can use either tone-status columns or TTL columns.
# The checker does not require them for every file, but it reports whether
# the file appears compatible with the configured pipeline logic.
TONE_STATUS_HINTS = [
    "cs plus tone status",
    "cs minus tone status",
]

TTL_HINTS = [
    "cs plus on",
    "cs plus off",
    "cs minus on",
    "cs minus off",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def norm_colname(c: str) -> str:
    c = str(c).strip().lower()
    c = c.replace("\u00a0", " ")
    c = re.sub(r"\s+", " ", c)
    c = c.replace("(", "").replace(")", "")
    c = c.replace("+", "plus").replace("-", "minus")
    c = c.replace("/", " ").replace("\\", " ")
    c = c.replace(" ", "_")
    return c


def _normalize_key(v) -> str:
    if pd.isna(v):
        return ""
    return str(v).strip()


def parse_filename_bits(csv_path: Path):
    """
    Match the pipeline's filename logic:
    - look for MM-DD-YY in the file name
    - behavior_id is the first token after the date
    """
    stem = csv_path.stem
    m = re.search(r"(\d{2}-\d{2}-\d{2})", stem)
    if not m:
        return None, None
    test_date = m.group(1)
    rest = stem[m.end():].lstrip("_ -")
    if not rest:
        return test_date, None
    behavior_id = re.split(r"[_\s\(\)\[\]\{\}]+", rest, maxsplit=1)[0].strip()
    return test_date, behavior_id or None


def parse_folder_bits(folder_name: str):
    """
    Match the pipeline's folder parsing logic:
    - preferred form: d##_contextX_session
    - fallback: Day N ...
    """
    s = folder_name.strip()

    m = re.match(r"^(d\d{2})_(context[ab])_(.+)$", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2), m.group(3)

    m = re.match(r"^Day\s+(\d+)\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        day = f"d{int(m.group(1)):02d}"
        rest = m.group(2).strip()
        if "_" in rest:
            maybe_ctx, maybe_sess = rest.rsplit("_", 1)
            if maybe_sess.isdigit():
                return day, maybe_ctx, maybe_sess
        return day, rest, None

    return folder_name, None, None


def day_sort_key(folder_name: str):
    m = re.match(r"^d(\d{2})_", folder_name, flags=re.IGNORECASE)
    if m:
        return (int(m.group(1)), folder_name.lower())
    return (999, folder_name.lower())


def find_session_dirs(behaviordata: Path):
    dirs = [
        p for p in behaviordata.iterdir()
        if p.is_dir() and re.search(r"^d\d{2}_", p.name, flags=re.IGNORECASE)
    ]
    return sorted(dirs, key=lambda p: day_sort_key(p.name))


def find_first_column(df: pd.DataFrame, hints):
    for col in df.columns:
        if any(h in col for h in hints):
            return col
    return None


def find_tone_status_cols(cols):
    cols = list(cols)

    def match_one(pattern):
        norm = norm_colname(pattern)
        for c in cols:
            if norm in c:
                return c
        tokens = norm.split("_")
        for c in cols:
            if all(t in c for t in tokens):
                return c
        return None

    return match_one(TONE_STATUS_HINTS[0]), match_one(TONE_STATUS_HINTS[1])


def find_ttl_cols(cols):
    cols = list(cols)

    def like(words):
        pat = re.compile(r".*".join(re.escape(w) for w in words))
        matches = [c for c in cols if pat.search(c)]
        return matches[0] if matches else None

    csplus_on   = like(["cs", "plus", "on"])   or like(["csplus", "on"])
    csplus_off  = like(["cs", "plus", "off"])  or like(["csplus", "off"])
    csminus_on  = like(["cs", "minus", "on"])  or like(["csminus", "on"])
    csminus_off = like(["cs", "minus", "off"]) or like(["csminus", "off"])
    return csplus_on, csplus_off, csminus_on, csminus_off


def read_csv_header(csv_path: Path):
    try:
        df = pd.read_csv(csv_path, nrows=5, engine="python")
    except Exception:
        df = pd.read_csv(csv_path, nrows=5, sep=None, engine="python")
    df.columns = [norm_colname(c) for c in df.columns]
    return df


def read_metadata(meta_path: Path):
    meta = pd.read_excel(meta_path)
    meta.columns = [norm_colname(c) for c in meta.columns]
    return meta


def log_line(lines, msg):
    print(msg)
    lines.append(msg)


def suggest_for_issue(issue_type, details):
    if issue_type == "missing_metadata":
        return "Add animals_metadata.xlsx to the folder, or duplicate the file into a branch where the pipeline can read it."
    if issue_type == "missing_behavior_id":
        return "Rename the metadata column to behavior_id, or update the code mapping in a git branch and pull that change."
    if issue_type == "missing_cohort_id":
        return "Either add cohort_id to metadata or let the folder name define the cohort label."
    if issue_type == "behavior_id_mismatch":
        return "Rename the CSV file token or the metadata behavior_id so they match exactly."
    if issue_type == "missing_time":
        return "Rename the time column, or update the parser in a branch and pull the change."
    if issue_type == "missing_freezing":
        return "Rename the freezing column, or update the parser in a branch and pull the change."
    if issue_type == "trial_detection":
        return "Check whether the export uses tone-status or TTL columns; rename them or switch the pipeline setting."
    if issue_type == "bad_day_folder":
        return "Rename the folder to the expected d##_... form, or update the folder parser in code."
    if issue_type == "bad_filename_date":
        return "Rename the CSV so it includes an MM-DD-YY date token."
    return "Review the naming convention or adjust the parser in code on a separate branch."


# ── Checks ───────────────────────────────────────────────────────────────────

def check_metadata_folder(bd: Path, report_lines):
    meta_path = bd / "animals_metadata.xlsx"
    result = {
        "has_metadata": False,
        "meta": None,
        "issues": [],
    }

    if not meta_path.exists():
        msg = "[ERROR] Missing animals_metadata.xlsx"
        result["issues"].append(("missing_metadata", msg))
        log_line(report_lines, msg)
        return result

    try:
        meta = read_metadata(meta_path)
    except Exception as e:
        msg = f"[ERROR] Could not read metadata: {e}"
        result["issues"].append(("missing_metadata", msg))
        log_line(report_lines, msg)
        return result

    result["has_metadata"] = True
    result["meta"] = meta

    log_line(report_lines, f"[ok] Loaded metadata: {meta_path.name}")

    for col in REQUIRED_METADATA_COLUMNS:
        if col not in meta.columns:
            msg = f"[ERROR] Missing required metadata column: {col}"
            result["issues"].append(("missing_behavior_id", msg) if col == "behavior_id" else ("metadata", msg))
            log_line(report_lines, msg)

    for col in RECOMMENDED_METADATA_COLUMNS:
        if col not in meta.columns:
            msg = f"[WARN] Missing recommended metadata column: {col}"
            result["issues"].append(("missing_cohort_id", msg) if col == "cohort_id" else ("metadata", msg))
            log_line(report_lines, msg)

    if "behavior_id" in meta.columns:
        n_blank = int((meta["behavior_id"].astype(str).str.strip() == "").sum())
        if n_blank > 0:
            msg = f"[WARN] {n_blank} blank behavior_id entries in metadata"
            result["issues"].append(("missing_behavior_id", msg))
            log_line(report_lines, msg)

        dupes = meta["behavior_id"].astype(str).str.strip().value_counts()
        dupes = dupes[dupes > 1]
        if not dupes.empty:
            msg = f"[WARN] Duplicate behavior_id values in metadata: {dupes.index.tolist()}"
            result["issues"].append(("metadata", msg))
            log_line(report_lines, msg)

    if "cohort_id" not in meta.columns:
        msg = f"[INFO] No cohort_id column found; folder name will be used as cohort label for {bd.name}"
        result["issues"].append(("missing_cohort_id", msg))
        log_line(report_lines, msg)

    return result


def check_day_folder(day_dir: Path, report_lines):
    issues = []
    day, context, session = parse_folder_bits(day_dir.name)

    if day == day_dir.name and context is None:
        msg = f"[WARN] Folder name does not match expected pattern: {day_dir.name}"
        issues.append(("bad_day_folder", msg))
        log_line(report_lines, msg)
    else:
        log_line(report_lines, f"[ok] Day folder parsed as: day={day}, context={context}, session={session}")

    return issues


def check_csv(csv_path: Path, meta: pd.DataFrame, report_lines):
    issues = []
    fname = csv_path.name

    try:
        df = read_csv_header(csv_path)
    except Exception as e:
        msg = f"[ERROR] Could not read CSV: {e}"
        issues.append(("metadata", msg))
        log_line(report_lines, msg)
        return issues

    date_token, behavior_id = utils.parse_filename_bits(csv_path, meta)

    if not date_token:
        msg = f"[WARN] Could not parse test date from filename: {fname}"
        issues.append(("bad_filename_date", msg))
        log_line(report_lines, msg)
    else:
        log_line(report_lines, f"[ok] Parsed file date: {date_token}")

    if not behavior_id:
        msg = f"[WARN] Could not parse behavior_id from filename: {fname}"
        issues.append(("behavior_id_mismatch", msg))
        log_line(report_lines, msg)
    else:
        log_line(report_lines, f"[ok] Parsed behavior_id token: {behavior_id}")

    time_col = find_first_column(df, REQUIRED_CSV_HINTS["time"])
    freeze_col = find_first_column(df, REQUIRED_CSV_HINTS["freezing"])

    if not time_col:
        msg = f"[ERROR] Missing time-like column in {fname}"
        issues.append(("missing_time", msg))
        log_line(report_lines, msg)
    else:
        log_line(report_lines, f"[ok] Time column candidate: {time_col}")

    if not freeze_col:
        msg = f"[ERROR] Missing freezing-like column in {fname}"
        issues.append(("missing_freezing", msg))
        log_line(report_lines, msg)
    else:
        log_line(report_lines, f"[ok] Freezing column candidate: {freeze_col}")

    tone_csplus, tone_csminus = find_tone_status_cols(df.columns)
    ttl_cols = find_ttl_cols(df.columns)

    if tone_csplus and tone_csminus:
        log_line(report_lines, f"[ok] Tone-status trial columns found: {tone_csplus}, {tone_csminus}")
    elif all(ttl_cols):
        log_line(report_lines, f"[ok] TTL trial columns found: {ttl_cols}")
    else:
        msg = (
            f"[WARN] Trial detection columns are incomplete in {fname}. "
            f"Neither tone-status nor TTL-style CS columns were fully detected."
        )
        issues.append(("trial_detection", msg))
        log_line(report_lines, msg)

    if behavior_id and meta is not None and "behavior_id" in meta.columns:
        if utils.find_metadata_for_behavior(meta, behavior_id).empty:
            msg = f"[WARN] behavior_id '{behavior_id}' from filename does not appear in metadata"
            issues.append(("behavior_id_mismatch", msg))
            log_line(report_lines, msg)

    return issues


def summarize_folder(bd: Path, report_lines):
    log_line(report_lines, "")
    log_line(report_lines, "=" * 78)
    log_line(report_lines, f"Checking BehaviorData folder: {bd}")
    log_line(report_lines, "=" * 78)

    if not bd.exists():
        msg = f"[FATAL] Folder does not exist: {bd}"
        log_line(report_lines, msg)
        return {
            "folder": str(bd),
            "exists": False,
            "n_day_dirs": 0,
            "n_csv": 0,
            "issues": [("missing_folder", msg)],
        }

    folder_result = {
        "folder": str(bd),
        "exists": True,
        "n_day_dirs": 0,
        "n_csv": 0,
        "issues": [],
    }

    meta_result = check_metadata_folder(bd, report_lines)
    meta = meta_result["meta"]
    folder_result["issues"].extend(meta_result["issues"])

    day_dirs = find_session_dirs(bd)
    folder_result["n_day_dirs"] = len(day_dirs)

    if not day_dirs:
        msg = "[ERROR] No day folders found. Expected folders like d01_contexta_..."
        folder_result["issues"].append(("bad_day_folder", msg))
        log_line(report_lines, msg)
        return folder_result

    for day_dir in day_dirs:
        folder_result["issues"].extend(check_day_folder(day_dir, report_lines))

        csvs = sorted(day_dir.glob("*.csv"))
        if not csvs:
            msg = f"[WARN] No CSV files found in {day_dir.name}"
            folder_result["issues"].append(("metadata", msg))
            log_line(report_lines, msg)
            continue

        log_line(report_lines, f"[ok] {day_dir.name}: {len(csvs)} CSV file(s)")

        for csv_path in csvs:
            folder_result["n_csv"] += 1
            folder_result["issues"].extend(check_csv(csv_path, meta, report_lines))

    return folder_result


def print_final_summary(results, report_lines):
    log_line(report_lines, "")
    log_line(report_lines, "=" * 78)
    log_line(report_lines, "SUMMARY")
    log_line(report_lines, "=" * 78)

    total_folders = len(results)
    total_day_dirs = sum(r["n_day_dirs"] for r in results)
    total_csv = sum(r["n_csv"] for r in results)

    issue_counter = Counter(issue_type for r in results for issue_type, _ in r["issues"])
    n_errors = sum(1 for r in results for _, msg in r["issues"] if "[ERROR]" in msg or "[FATAL]" in msg)
    n_warns = sum(1 for r in results for _, msg in r["issues"] if "[WARN]" in msg)

    log_line(report_lines, f"Folders checked: {total_folders}")
    log_line(report_lines, f"Day folders found: {total_day_dirs}")
    log_line(report_lines, f"CSV files checked: {total_csv}")
    log_line(report_lines, f"Errors: {n_errors}")
    log_line(report_lines, f"Warnings: {n_warns}")

    if issue_counter:
        log_line(report_lines, "")
        log_line(report_lines, "Most common issue types:")
        for issue_type, count in issue_counter.most_common():
            log_line(report_lines, f"  - {issue_type}: {count}")

    log_line(report_lines, "")
    log_line(report_lines, "Suggested fixes:")
    top_issue_types = [k for k, _ in issue_counter.most_common(5)]
    if not top_issue_types:
        log_line(report_lines, "  - No issues detected.")
    else:
        for issue_type in top_issue_types:
            suggestion = suggest_for_issue(issue_type, None)
            log_line(report_lines, f"  - {suggestion}")

    log_line(report_lines, "")
    log_line(report_lines, "Practical fix options:")
    log_line(report_lines, "  - Rename folders/files to match the current parser.")
    log_line(report_lines, "  - Or update the parser in a separate git branch and pull that change.")
    log_line(report_lines, "  - Or duplicate the affected files into a branch-specific working copy and adjust only that copy.")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    report_lines = []

    results = []
    for bd_str in BEHAVIORDATA_DIRS:
        bd = Path(bd_str)
        results.append(summarize_folder(bd, report_lines))

    print_final_summary(results, report_lines)

    if REPORT_OUT:
        out_path = Path(REPORT_OUT)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(report_lines), encoding="utf-8")
        print(f"\n[ok] Report written to: {out_path}")


if __name__ == "__main__":
    main()
