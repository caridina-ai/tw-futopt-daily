from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from tw_futopt import cli


@pytest.mark.integration
def test_downloads_recent_complete_taifex_trading_day(tmp_path: Path) -> None:
    today = dt.date.today()
    skipped_dates: list[str] = []

    for offset in range(1, 11):
        trade_date = today - dt.timedelta(days=offset)
        day_dir = tmp_path / trade_date.isoformat()
        downloaded: list[Path] = []

        for spec in cli.DOWNLOAD_SPECS:
            target = day_dir / spec.filename(trade_date)
            try:
                cli.fetch_url(spec.url(trade_date), target, timeout=30)
            except cli.RemoteFileUnavailable:
                skipped_dates.append(trade_date.isoformat())
                break
            except HTTPError as exc:
                if exc.code == 404:
                    skipped_dates.append(trade_date.isoformat())
                    break
                raise
            except URLError as exc:
                pytest.fail(f"network error while downloading {target.name}: {exc}")

            downloaded.append(target)

        if len(downloaded) == len(cli.DOWNLOAD_SPECS):
            assert all(path.exists() for path in downloaded)
            assert all(path.stat().st_size > 200 for path in downloaded)
            assert all(zipfile.is_zipfile(path) for path in downloaded)
            return

    pytest.fail(
        "No complete TAIFEX trading day found in the previous 10 calendar days; "
        f"skipped dates: {', '.join(skipped_dates)}"
    )
