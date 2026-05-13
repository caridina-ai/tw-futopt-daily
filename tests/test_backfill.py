from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from tw_futopt import backfill


def zip_bytes(name: str = "sample.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, "date,value\n2026-05-12,1\n")
    return buffer.getvalue()


def write_zip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zip_bytes())


def write_empty_zip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w"):
        pass


def write_config(path: Path, data: dict[str, object]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_user_tick_config_copies_and_renames_files(tmp_path: Path) -> None:
    source_root = tmp_path / "UserTick"
    output_dir = tmp_path / "out"
    write_zip(source_root / "20260505" / "20260505-Daily_2026_05_05.zip")
    config_path = write_config(
        tmp_path / "user_tick.json",
        {
            "name": "user-tick-test",
            "source_root": str(source_root),
            "date_source": "directory",
            "date_glob": "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]",
            "date_format": "%Y%m%d",
            "date_path_template": "%Y%m%d",
            "files": [
                {
                    "source_template": "%Y%m%d-Daily_%Y_%m_%d.zip",
                    "target_template": "Daily_%Y_%m_%d.zip",
                }
            ],
        },
    )
    logger = backfill.TextLogger(tmp_path / "backfill.log")

    summary = backfill.run(
        backfill.BackfillConfig.from_file(config_path), output_dir, logger
    )

    assert summary.copied == 1
    assert zipfile.is_zipfile(output_dir / "Daily_2026_05_05.zip")
    assert "COPIED" in logger.path.read_text(encoding="utf-8")


def test_futures_config_discovers_dates_from_filenames(tmp_path: Path) -> None:
    source_root = tmp_path / "futures"
    output_dir = tmp_path / "out"
    write_zip(source_root / "2026" / "Daily_2026_05_05.zip")
    config_path = write_config(
        tmp_path / "futures.json",
        {
            "name": "futures-test",
            "source_root": str(source_root),
            "date_source": "filename",
            "date_glob": "[0-9][0-9][0-9][0-9]/Daily_[0-9][0-9][0-9][0-9]_[0-9][0-9]_[0-9][0-9].zip",
            "date_format": "Daily_%Y_%m_%d.zip",
            "date_path_template": "%Y",
            "files": [
                {
                    "source_template": "Daily_%Y_%m_%d.zip",
                    "target_template": "Daily_%Y_%m_%d.zip",
                }
            ],
        },
    )

    summary = backfill.run(
        backfill.BackfillConfig.from_file(config_path),
        output_dir,
        backfill.TextLogger(tmp_path / "backfill.log"),
    )

    assert summary.copied == 1
    assert (output_dir / "Daily_2026_05_05.zip").exists()


def test_existing_target_is_skipped_without_validating_source(tmp_path: Path) -> None:
    source_root = tmp_path / "UserTick"
    output_dir = tmp_path / "out"
    write_zip(output_dir / "Daily_2026_05_05.zip")
    write_empty_zip(source_root / "20260505" / "20260505-Daily_2026_05_05.zip")
    config = backfill.BackfillConfig(
        name="existing-test",
        source_root=source_root,
        date_source="directory",
        date_glob="20260505",
        date_format="%Y%m%d",
        date_path_template="%Y%m%d",
        files=(
            backfill.FileRule(
                "%Y%m%d-Daily_%Y_%m_%d.zip", "Daily_%Y_%m_%d.zip"
            ),
        ),
    )

    summary = backfill.run(config, output_dir, backfill.TextLogger(tmp_path / "log.txt"))

    assert summary.skipped_existing == 1


def test_invalid_source_zip_is_skipped_and_logged(tmp_path: Path) -> None:
    source_root = tmp_path / "UserTick"
    output_dir = tmp_path / "out"
    write_empty_zip(source_root / "20260505" / "20260505-Daily_2026_05_05.zip")
    log_path = tmp_path / "log.txt"
    config = backfill.BackfillConfig(
        name="invalid-test",
        source_root=source_root,
        date_source="directory",
        date_glob="20260505",
        date_format="%Y%m%d",
        date_path_template="%Y%m%d",
        files=(
            backfill.FileRule(
                "%Y%m%d-Daily_%Y_%m_%d.zip", "Daily_%Y_%m_%d.zip"
            ),
        ),
    )

    summary = backfill.run(config, output_dir, backfill.TextLogger(log_path))

    assert summary.skipped_invalid_source == 1
    assert "SKIP_INVALID_SOURCE" in log_path.read_text(encoding="utf-8")


def test_zip_validation_treats_testzip_os_error_as_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "Daily_2026_05_05.zip"
    write_zip(path)

    monkeypatch.setattr(
        backfill.zipfile.ZipFile,
        "testzip",
        lambda self: (_ for _ in ()).throw(OSError("bad central directory")),
    )

    assert not backfill.is_valid_nonempty_zip(path)


def test_dry_run_validates_source_but_does_not_copy(tmp_path: Path) -> None:
    source_root = tmp_path / "futures"
    output_dir = tmp_path / "out"
    write_zip(source_root / "2026" / "Daily_2026_05_05.zip")
    config = backfill.BackfillConfig(
        name="dry-run-test",
        source_root=source_root,
        date_source="filename",
        date_glob="2026/Daily_2026_05_05.zip",
        date_format="Daily_%Y_%m_%d.zip",
        date_path_template="%Y",
        files=(backfill.FileRule("Daily_%Y_%m_%d.zip", "Daily_%Y_%m_%d.zip"),),
    )

    summary = backfill.run(
        config, output_dir, backfill.TextLogger(tmp_path / "log.txt"), dry_run=True
    )

    assert summary.dry_run == 1
    assert not (output_dir / "Daily_2026_05_05.zip").exists()


def test_run_filters_date_range(tmp_path: Path) -> None:
    source_root = tmp_path / "futures"
    output_dir = tmp_path / "out"
    write_zip(source_root / "2026" / "Daily_2026_05_05.zip")
    write_zip(source_root / "2026" / "Daily_2026_05_06.zip")
    config = backfill.BackfillConfig(
        name="filter-test",
        source_root=source_root,
        date_source="filename",
        date_glob="2026/Daily_2026_05_*.zip",
        date_format="Daily_%Y_%m_%d.zip",
        date_path_template="%Y",
        files=(backfill.FileRule("Daily_%Y_%m_%d.zip", "Daily_%Y_%m_%d.zip"),),
    )

    summary = backfill.run(
        config,
        output_dir,
        backfill.TextLogger(tmp_path / "log.txt"),
        start_date=backfill.parse_iso_date("2026-05-06"),
        end_date=backfill.parse_iso_date("2026-05-06"),
    )

    assert summary.copied == 1
    assert not (output_dir / "Daily_2026_05_05.zip").exists()
    assert (output_dir / "Daily_2026_05_06.zip").exists()


def test_main_uses_tw_futopt_dir_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "futures"
    output_dir = tmp_path / "out"
    log_path = tmp_path / "run.log"
    write_zip(source_root / "2026" / "Daily_2026_05_05.zip")
    config_path = write_config(
        tmp_path / "futures.json",
        {
            "name": "main-test",
            "source_root": str(source_root),
            "date_source": "filename",
            "date_glob": "2026/Daily_2026_05_05.zip",
            "date_format": "Daily_%Y_%m_%d.zip",
            "date_path_template": "%Y",
            "files": [
                {
                    "source_template": "Daily_%Y_%m_%d.zip",
                    "target_template": "Daily_%Y_%m_%d.zip",
                }
            ],
        },
    )
    monkeypatch.setenv(backfill.ENV_OUTPUT_DIR, str(output_dir))

    assert backfill.main([str(config_path), "--log", str(log_path)]) == 0
    assert (output_dir / "Daily_2026_05_05.zip").exists()


def test_copy_packaged_configs_initializes_user_config_dir(tmp_path: Path) -> None:
    target_dir = tmp_path / "configs"

    copied = backfill.copy_packaged_configs(target_dir)

    copied_names = {path.name for path in copied}
    assert {"user_tick.json", "futures.json"} <= copied_names
    assert (target_dir / "user_tick.json").exists()
    assert (target_dir / "futures.json").exists()


def test_main_init_configs_does_not_require_config_path(tmp_path: Path) -> None:
    target_dir = tmp_path / "configs"

    assert backfill.main(["--init-configs", str(target_dir)]) == 0

    assert (target_dir / "user_tick.json").exists()
    assert (target_dir / "futures.json").exists()
