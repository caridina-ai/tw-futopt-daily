from __future__ import annotations

import argparse
import datetime as dt
import io
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from tw_futopt import cli


def zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sample.csv", "date,value\n2026-05-12,1\n")
    return buffer.getvalue()


def write_zip(path: Path) -> None:
    path.write_bytes(zip_bytes())


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def test_parse_date_accepts_iso_date() -> None:
    assert cli.parse_date("2026-05-12") == dt.date(2026, 5, 12)


def test_parse_date_rejects_invalid_date() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_date("2026/05/12")


def test_iter_default_dates_returns_30_days_including_anchor() -> None:
    anchor = dt.date(2026, 5, 13)

    dates = cli.iter_default_dates(anchor)

    assert len(dates) == 30
    assert dates[0] == dt.date(2026, 5, 13)
    assert dates[-1] == dt.date(2026, 4, 14)


def test_output_dir_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(cli.ENV_OUTPUT_DIR, str(tmp_path))

    assert cli.output_dir_from_env() == tmp_path


def test_output_dir_from_env_requires_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(cli.ENV_OUTPUT_DIR, raising=False)

    with pytest.raises(RuntimeError, match=cli.ENV_OUTPUT_DIR):
        cli.output_dir_from_env()


def test_fetch_url_writes_valid_zip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(zip_bytes()),
    )
    target = tmp_path / "Daily_2026_05_12.zip"

    cli.fetch_url("https://example.test/data.zip", target, timeout=1)

    assert zipfile.is_zipfile(target)
    assert not list(tmp_path.glob("*.part"))


def test_fetch_url_rejects_non_zip_and_removes_temp_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(b"<html>not found</html>"),
    )
    target = tmp_path / "Daily_2026_05_12.zip"

    with pytest.raises(cli.RemoteFileUnavailable):
        cli.fetch_url("https://example.test/data.zip", target, timeout=1)

    assert not target.exists()
    assert not list(tmp_path.glob("*.part"))


def test_download_one_skips_existing_valid_zip(tmp_path: Path) -> None:
    spec = cli.DOWNLOAD_SPECS[0]
    trade_date = dt.date(2026, 5, 12)
    write_zip(tmp_path / spec.filename(trade_date))

    result = cli.download_one(spec, trade_date, tmp_path, force=False, timeout=1)

    assert result == "skipped"


def test_download_one_force_overwrites_existing_zip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = cli.DOWNLOAD_SPECS[0]
    trade_date = dt.date(2026, 5, 12)
    target = tmp_path / spec.filename(trade_date)
    write_zip(target)
    calls: list[tuple[str, Path, float]] = []

    def fake_fetch_url(url: str, target: Path, timeout: float) -> None:
        calls.append((url, target, timeout))
        write_zip(target)

    monkeypatch.setattr(cli, "fetch_url", fake_fetch_url)

    result = cli.download_one(spec, trade_date, tmp_path, force=True, timeout=3)

    assert result == "downloaded"
    assert calls == [(spec.url(trade_date), target, 3)]


def test_download_one_treats_404_as_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = cli.DOWNLOAD_SPECS[0]
    trade_date = dt.date(2026, 5, 10)

    def raise_404(url: str, target: Path, timeout: float) -> None:
        raise HTTPError(url, 404, "not found", None, None)

    monkeypatch.setattr(cli, "fetch_url", raise_404)

    assert cli.download_one(spec, trade_date, tmp_path, False, 1) == "missing"


def test_download_one_treats_non_zip_response_as_not_zip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = cli.DOWNLOAD_SPECS[0]
    trade_date = dt.date(2026, 5, 10)

    def raise_unavailable(url: str, target: Path, timeout: float) -> None:
        raise cli.RemoteFileUnavailable("response is not a zip file")

    monkeypatch.setattr(cli, "fetch_url", raise_unavailable)

    assert cli.download_one(spec, trade_date, tmp_path, False, 1) == "not_zip"


def test_download_one_treats_network_error_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = cli.DOWNLOAD_SPECS[0]
    trade_date = dt.date(2026, 5, 12)

    def raise_network_error(url: str, target: Path, timeout: float) -> None:
        raise URLError("network down")

    monkeypatch.setattr(cli, "fetch_url", raise_network_error)

    assert cli.download_one(spec, trade_date, tmp_path, False, 1) == "failed"


def test_run_counts_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outcomes = iter(["downloaded", "skipped", "not_zip", "failed"])

    def fake_download_one(
        spec: cli.DownloadSpec,
        trade_date: dt.date,
        output_dir: Path,
        force: bool,
        timeout: float,
    ) -> str:
        return next(outcomes)

    monkeypatch.setattr(cli, "download_one", fake_download_one)

    summary = cli.run([dt.date(2026, 5, 12)], tmp_path, force=False, timeout=1)

    assert summary.planned == 4
    assert summary.downloaded == 1
    assert summary.skipped == 1
    assert summary.not_zip == 1
    assert summary.missing == 0
    assert summary.failed == 1
    assert not summary.ok


def test_telegram_message_reports_downloaded_not_zip_over_planned() -> None:
    summary = cli.Summary(planned=120, downloaded=4, skipped=76, not_zip=20)

    assert cli.telegram_message(summary, success=True) == "tw_futopt done 4/20/120"
    assert cli.telegram_message(summary, success=False) == "tw_futopt failed 4/20/120"


def test_main_returns_2_when_env_var_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(cli.ENV_OUTPUT_DIR, raising=False)

    assert cli.main([]) == 2


def test_main_returns_1_when_force_date_is_not_zip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(cli.ENV_OUTPUT_DIR, str(tmp_path))
    monkeypatch.setattr(cli, "run", lambda dates, output_dir, force, timeout: cli.Summary(not_zip=1))
    monkeypatch.setattr(cli, "notify_telegram", lambda summary, success: None)

    assert cli.main(["2026-05-10"]) == 1


def test_main_sends_telegram_done_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sent: list[tuple[cli.Summary, bool]] = []
    monkeypatch.setenv(cli.ENV_OUTPUT_DIR, str(tmp_path))
    monkeypatch.setattr(
        cli,
        "run",
        lambda dates, output_dir, force, timeout: cli.Summary(
            planned=4, downloaded=2, skipped=2
        ),
    )
    monkeypatch.setattr(
        cli, "notify_telegram", lambda summary, success: sent.append((summary, success))
    )

    assert cli.main(["2026-05-12"]) == 0
    assert sent == [(cli.Summary(planned=4, downloaded=2, skipped=2), True)]
