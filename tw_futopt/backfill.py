from __future__ import annotations

import argparse
import datetime as dt
import importlib.resources as resources
import json
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENV_OUTPUT_DIR = "TW_FUTOPT_DIR"
DEFAULT_LOG_DIR_NAME = "logs"
CONFIG_PACKAGE_DIR = "backfill_configs"


@dataclass(frozen=True)
class FileRule:
    source_template: str
    target_template: str

    @classmethod
    def from_config(cls, data: dict[str, Any]) -> "FileRule":
        return cls(
            source_template=require_string(data, "source_template"),
            target_template=require_string(data, "target_template"),
        )


@dataclass(frozen=True)
class BackfillConfig:
    name: str
    source_root: Path
    date_source: str
    date_glob: str
    date_format: str
    date_path_template: str
    files: tuple[FileRule, ...]

    @classmethod
    def from_file(cls, path: Path) -> "BackfillConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        files = tuple(FileRule.from_config(item) for item in require_list(data, "files"))
        if not files:
            raise ValueError("config must contain at least one file rule")

        date_source = require_string(data, "date_source")
        if date_source not in {"directory", "filename"}:
            raise ValueError("date_source must be 'directory' or 'filename'")

        return cls(
            name=require_string(data, "name"),
            source_root=Path(require_string(data, "source_root")).expanduser(),
            date_source=date_source,
            date_glob=require_string(data, "date_glob"),
            date_format=require_string(data, "date_format"),
            date_path_template=require_string(data, "date_path_template"),
            files=files,
        )


@dataclass
class Summary:
    copied: int = 0
    dry_run: int = 0
    skipped_existing: int = 0
    skipped_missing: int = 0
    skipped_invalid_source: int = 0
    errors: int = 0

    @property
    def ok(self) -> bool:
        return self.errors == 0


class TextLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action: str, **fields: object) -> None:
        timestamp = dt.datetime.now().isoformat(timespec="seconds")
        parts = [timestamp, action]
        parts.extend(f"{key}={value}" for key, value in fields.items())
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(" | ".join(parts) + "\n")


def require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"config field {key!r} must be a non-empty string")
    return value


def require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"config field {key!r} must be a list")
    return value


def output_dir_from_env() -> Path:
    raw = os.environ.get(ENV_OUTPUT_DIR)
    if not raw:
        raise RuntimeError(f"{ENV_OUTPUT_DIR} is not set")
    return Path(raw).expanduser()


def is_valid_nonempty_zip(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0 or not zipfile.is_zipfile(path):
        return False

    try:
        with zipfile.ZipFile(path) as archive:
            if not archive.infolist():
                return False
            return archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def parse_date(value: str, date_format: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(value, date_format).date()
    except ValueError:
        return None


def discover_dates(config: BackfillConfig) -> list[dt.date]:
    dates: set[dt.date] = set()

    if config.date_source == "directory":
        for path in config.source_root.glob(config.date_glob):
            if path.is_dir():
                parsed = parse_date(path.name, config.date_format)
                if parsed is not None:
                    dates.add(parsed)
    else:
        for path in config.source_root.glob(config.date_glob):
            if path.is_file():
                parsed = parse_date(path.name, config.date_format)
                if parsed is not None:
                    dates.add(parsed)

    return sorted(dates)


def render(template: str, trade_date: dt.date) -> str:
    return trade_date.strftime(template)


def copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=str(target.parent)
    )
    temp_path = Path(temp_name)
    os.close(fd)

    try:
        shutil.copy2(source, temp_path)
        if not is_valid_nonempty_zip(temp_path):
            raise ValueError("copied file is not a valid non-empty zip")
        temp_path.replace(target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def process_file(
    config: BackfillConfig,
    rule: FileRule,
    trade_date: dt.date,
    output_dir: Path,
    logger: TextLogger,
    dry_run: bool,
) -> str:
    source = config.source_root / render(config.date_path_template, trade_date) / render(
        rule.source_template, trade_date
    )
    target = output_dir / render(rule.target_template, trade_date)
    common = {
        "batch": config.name,
        "date": trade_date.isoformat(),
        "source": source,
        "target": target,
    }

    if target.exists():
        logger.write("SKIP_EXISTING", **common)
        return "skipped_existing"

    if not source.exists():
        logger.write("SKIP_MISSING_SOURCE", **common)
        return "skipped_missing"

    if not is_valid_nonempty_zip(source):
        logger.write("SKIP_INVALID_SOURCE", **common, bytes=source.stat().st_size)
        return "skipped_invalid_source"

    if dry_run:
        logger.write("DRY_RUN_COPY", **common, bytes=source.stat().st_size)
        return "dry_run"

    try:
        copy_atomic(source, target)
    except OSError as exc:
        logger.write("ERROR_COPY", **common, error=exc)
        return "error"
    except ValueError as exc:
        logger.write("ERROR_VALIDATE_COPIED_FILE", **common, error=exc)
        return "error"

    logger.write("COPIED", **common, bytes=target.stat().st_size)
    return "copied"


def run(
    config: BackfillConfig,
    output_dir: Path,
    logger: TextLogger,
    dry_run: bool = False,
    max_files: int | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
) -> Summary:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = Summary()
    processed = 0

    logger.write(
        "START",
        batch=config.name,
        source_root=config.source_root,
        output_dir=output_dir,
        dry_run=dry_run,
        max_files=max_files or "",
        start_date=start_date or "",
        end_date=end_date or "",
    )

    for trade_date in discover_dates(config):
        if start_date is not None and trade_date < start_date:
            continue
        if end_date is not None and trade_date > end_date:
            continue

        for rule in config.files:
            if max_files is not None and processed >= max_files:
                logger.write("STOP_MAX_FILES", batch=config.name, max_files=max_files)
                logger.write("SUMMARY", batch=config.name, **summary.__dict__)
                return summary

            result = process_file(config, rule, trade_date, output_dir, logger, dry_run)
            processed += 1
            if result == "copied":
                summary.copied += 1
            elif result == "dry_run":
                summary.dry_run += 1
            elif result == "skipped_existing":
                summary.skipped_existing += 1
            elif result == "skipped_missing":
                summary.skipped_missing += 1
            elif result == "skipped_invalid_source":
                summary.skipped_invalid_source += 1
            else:
                summary.errors += 1

    logger.write("SUMMARY", batch=config.name, **summary.__dict__)
    return summary


def default_log_path(config_name: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(DEFAULT_LOG_DIR_NAME) / f"backfill-{config_name}-{stamp}.log"


def packaged_config_dir() -> resources.abc.Traversable:
    return resources.files("tw_futopt").joinpath(CONFIG_PACKAGE_DIR)


def packaged_config_names() -> list[str]:
    return sorted(
        child.name
        for child in packaged_config_dir().iterdir()
        if child.is_file() and child.name.endswith(".json")
    )


def copy_packaged_configs(target_dir: Path, overwrite: bool = False) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in packaged_config_names():
        source = packaged_config_dir().joinpath(name)
        target = target_dir / name
        if target.exists() and not overwrite:
            continue
        target.write_bytes(source.read_bytes())
        copied.append(target)
    return copied


def parse_iso_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected yyyy-mm-dd"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tw_futopt.backfill",
        description="Copy valid historical TAIFEX daily zip files into TW_FUTOPT_DIR.",
    )
    parser.add_argument(
        "config", nargs="?", type=Path, help="path to a JSON backfill config"
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="plain text log path; defaults to logs/backfill-<batch>-<timestamp>.log",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and log planned copies without writing target files",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        help="stop after this many file rules; useful for smoke tests",
    )
    parser.add_argument(
        "--start-date",
        type=parse_iso_date,
        help="only process dates on or after yyyy-mm-dd",
    )
    parser.add_argument(
        "--end-date",
        type=parse_iso_date,
        help="only process dates on or before yyyy-mm-dd",
    )
    parser.add_argument(
        "--init-configs",
        type=Path,
        metavar="DIR",
        help="copy bundled template configs to DIR and exit",
    )
    parser.add_argument(
        "--overwrite-configs",
        action="store_true",
        help="replace existing files when used with --init-configs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.init_configs:
        try:
            copied = copy_packaged_configs(args.init_configs, args.overwrite_configs)
        except OSError as exc:
            print(f"error: failed to initialize configs: {exc}", file=sys.stderr)
            return 2
        print(f"config dir: {args.init_configs}")
        print(f"copied: {len(copied)}")
        return 0

    if args.config is None:
        parser.error(
            "config is required unless --init-configs is used"
        )

    try:
        config = BackfillConfig.from_file(args.config)
        output_dir = output_dir_from_env()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    log_path = args.log or default_log_path(config.name)
    logger = TextLogger(log_path)
    if args.start_date and args.end_date and args.start_date > args.end_date:
        print("error: --start-date must be on or before --end-date", file=sys.stderr)
        return 2

    summary = run(
        config,
        output_dir,
        logger,
        args.dry_run,
        args.max_files,
        args.start_date,
        args.end_date,
    )

    print(f"log: {log_path}")
    print(
        "summary: "
        f"copied={summary.copied}, "
        f"dry_run={summary.dry_run}, "
        f"skipped_existing={summary.skipped_existing}, "
        f"skipped_missing={summary.skipped_missing}, "
        f"skipped_invalid_source={summary.skipped_invalid_source}, "
        f"errors={summary.errors}"
    )
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
