from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


ENV_OUTPUT_DIR = "TW_FUTOPT_DIR"
ENV_TELEGRAM_API_TOKEN = "TELEGRAM_API_TOKEN"
ENV_TELEGRAM_CHAT_ID = "TELEGRAM_CHAT_ID"
DEFAULT_DAYS = 30
TELEGRAM_TIMEOUT = 20.0
USER_AGENT = "tw-futopt/0.1 (+https://www.taifex.com.tw/)"


class RemoteFileUnavailable(Exception):
    """Raised when TAIFEX returns a page instead of a downloadable zip."""


@dataclass(frozen=True)
class DownloadSpec:
    name_template: str
    url_template: str

    def filename(self, trade_date: dt.date) -> str:
        return trade_date.strftime(self.name_template)

    def url(self, trade_date: dt.date) -> str:
        return trade_date.strftime(self.url_template)


DOWNLOAD_SPECS = (
    DownloadSpec(
        "Daily_%Y_%m_%d.zip",
        "https://www.taifex.com.tw/DailyDownload/DailyDownloadCSV/Daily_%Y_%m_%d.zip",
    ),
    DownloadSpec(
        "Daily_%Y_%m_%d_B.zip",
        "https://www.taifex.com.tw/DailyDownload/DailyDownloadCSV_B/Daily_%Y_%m_%d_B.zip",
    ),
    DownloadSpec(
        "Daily_%Y_%m_%d_C.zip",
        "https://www.taifex.com.tw/DailyDownload/DailyDownloadCSV_C/Daily_%Y_%m_%d_C.zip",
    ),
    DownloadSpec(
        "OptionsDaily_%Y_%m_%d.zip",
        "https://www.taifex.com.tw/DailyDownload/OptionsDailyDownloadCSV/OptionsDaily_%Y_%m_%d.zip",
    ),
)


@dataclass
class Summary:
    planned: int = 0
    downloaded: int = 0
    skipped: int = 0
    not_zip: int = 0
    missing: int = 0
    failed: int = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def completed(self) -> int:
        return self.downloaded + self.skipped


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected yyyy-mm-dd"
        ) from exc


def output_dir_from_env() -> Path:
    raw = os.environ.get(ENV_OUTPUT_DIR)
    if not raw:
        raise RuntimeError(f"{ENV_OUTPUT_DIR} is not set")
    return Path(raw).expanduser()


def iter_default_dates(today: dt.date | None = None) -> list[dt.date]:
    anchor = today or dt.date.today()
    return [anchor - dt.timedelta(days=offset) for offset in range(DEFAULT_DAYS)]


def is_zip_file(path: Path) -> bool:
    return zipfile.is_zipfile(path)


def fetch_url(url: str, target: Path, timeout: float) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=str(target.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as temp_file:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                shutil.copyfileobj(response, temp_file)

        if not is_zip_file(temp_path):
            raise RemoteFileUnavailable("response is not a zip file")

        temp_path.replace(target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def download_one(
    spec: DownloadSpec,
    trade_date: dt.date,
    output_dir: Path,
    force: bool,
    timeout: float,
) -> str:
    filename = spec.filename(trade_date)
    target = output_dir / filename
    if target.exists() and not force and is_zip_file(target):
        print(f"skip       {filename}")
        return "skipped"

    if target.exists() and not force:
        print(f"replace    {filename} (existing file is not a valid zip)")

    try:
        fetch_url(spec.url(trade_date), target, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"missing    {filename} (404)")
            return "missing"
        print(f"failed     {filename} ({exc})", file=sys.stderr)
        return "failed"
    except RemoteFileUnavailable as exc:
        print(f"not_zip    {filename} ({exc})")
        return "not_zip"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"failed     {filename} ({exc})", file=sys.stderr)
        return "failed"

    print(f"downloaded {filename}")
    return "downloaded"


def run(dates: list[dt.date], output_dir: Path, force: bool, timeout: float) -> Summary:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = Summary(planned=len(dates) * len(DOWNLOAD_SPECS))

    for trade_date in dates:
        print(f"[{trade_date.isoformat()}]")
        for spec in DOWNLOAD_SPECS:
            result = download_one(spec, trade_date, output_dir, force, timeout)
            if result == "downloaded":
                summary.downloaded += 1
            elif result == "skipped":
                summary.skipped += 1
            elif result == "not_zip":
                summary.not_zip += 1
            elif result == "missing":
                summary.missing += 1
            else:
                summary.failed += 1

    return summary


def env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value

    if sys.platform != "win32":
        return None

    try:
        import winreg
    except ImportError:
        return None

    registry_locations = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for root, subkey in registry_locations:
        try:
            with winreg.OpenKey(root, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if raw:
            return str(raw)
    return None


def telegram_credentials() -> tuple[str, str] | None:
    token = env_value(ENV_TELEGRAM_API_TOKEN)
    chat_id = env_value(ENV_TELEGRAM_CHAT_ID)
    if not token or not chat_id:
        return None
    return token, chat_id


def describe_telegram_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"URL error: {exc.reason}"
    return exc.__class__.__name__


def telegram_message(summary: Summary, success: bool) -> str:
    status = "done" if success else "failed"
    return f"tw_futopt {status} {summary.downloaded}/{summary.not_zip}/{summary.planned}"


def send_telegram_message(message: str, timeout: float = TELEGRAM_TIMEOUT) -> None:
    credentials = telegram_credentials()
    if credentials is None:
        raise RuntimeError(
            f"{ENV_TELEGRAM_API_TOKEN} or {ENV_TELEGRAM_CHAT_ID} is not set"
        )

    token, chat_id = credentials
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def notify_telegram(summary: Summary, success: bool) -> None:
    message = telegram_message(summary, success)
    try:
        send_telegram_message(message)
    except Exception as exc:
        print(
            f"warning: telegram notification failed: {describe_telegram_error(exc)}",
            file=sys.stderr,
        )
        return
    print(f"telegram: {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tw_futopt",
        description="Download recent TAIFEX futures/options daily zip files.",
    )
    parser.add_argument(
        "date",
        nargs="?",
        type=parse_date,
        help="force overwrite files for one date, formatted yyyy-mm-dd",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="download timeout in seconds (default: 60)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        output_dir = output_dir_from_env()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    force = args.date is not None
    dates = [args.date] if force else iter_default_dates()

    print(f"output dir: {output_dir}")
    print("mode: force overwrite" if force else f"mode: default {DEFAULT_DAYS} days")

    summary = run(dates, output_dir, force, args.timeout)
    print(
        "summary: "
        f"planned={summary.planned}, "
        f"downloaded={summary.downloaded}, "
        f"skipped={summary.skipped}, "
        f"not_zip={summary.not_zip}, "
        f"missing={summary.missing}, "
        f"failed={summary.failed}"
    )

    exit_code = 0
    if force and (summary.failed > 0 or summary.missing > 0 or summary.not_zip > 0):
        exit_code = 1
    elif summary.failed > 0:
        exit_code = 1

    notify_telegram(summary, exit_code == 0)
    return exit_code
