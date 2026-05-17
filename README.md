# tw-futopt-daily

Download the recent TAIFEX futures and options daily zip files.

The downloader gets these files for each date:

- `Daily_YYYY_MM_DD.zip`
- `Daily_YYYY_MM_DD_B.zip`
- `Daily_YYYY_MM_DD_C.zip`
- `OptionsDaily_YYYY_MM_DD.zip`

Files are saved to the directory specified by the `TW_FUTOPT_DIR` environment
variable.

## Install

Install from GitHub:

```powershell
pip install git+https://github.com/caridina-ai/tw-futopt-daily.git
```

The `git+https://` prefix is required when installing directly from a Git
repository. Plain `pip install https://github.com/...` only works when the URL
points to a downloadable archive such as a `.zip` or `.tar.gz` file.

## Configure

Set `TW_FUTOPT_DIR` as a Windows machine environment variable. For example:

```powershell
[Environment]::SetEnvironmentVariable("TW_FUTOPT_DIR", "D:\tw_futopt", "Machine")
```

Open a new terminal after changing machine environment variables so the new
process can read the updated value.

Set Telegram notification credentials:

```powershell
[Environment]::SetEnvironmentVariable("TELEGRAM_API_TOKEN", "<bot-token>", "Machine")
[Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID", "<chat-id>", "Machine")
```

## Usage

Download the most recent 30 calendar days. Existing valid zip files are skipped:

```powershell
python -m tw_futopt
```

Force overwrite one date:

```powershell
python -m tw_futopt 2026-05-12
```

If TAIFEX returns a page instead of a zip file for a date, such as a weekend or
a not-yet-published trading day, the file is reported as `not_zip`.

On completion, the downloader sends a concise Telegram notification when
Telegram credentials are available, such as:

```text
tw_futopt done 4/20/120
```

The three counts are downloaded files, non-zip responses, and total planned
files. Existing valid zip files are still skipped, but skipped files are not
included in the Telegram count.

## Task Scheduler

Create a daily Windows Task Scheduler task. Recommended schedule:

- Trigger: daily
- Time: `22:30`
- Time zone: Taipei local time

- Program/script: `python`
- Add arguments: `-m tw_futopt`
- Run whether user is logged on or not: choose according to your machine policy

Because `TW_FUTOPT_DIR` is configured as a machine environment variable, the
scheduled task can read it directly.

The downloader looks back over the most recent 30 calendar days, so running a
little late is safer than running too early. If a file is not published yet, the
next day's run will try that date again.

## Notes

- The program writes downloads through temporary `.part` files and only replaces
  the target after the response is confirmed to be a valid zip file.
- Default mode treats missing TAIFEX files as normal and exits successfully as
  long as there are no network or filesystem failures.
- Single-date force mode exits with an error if any of that date's four files is
  missing or failed.

## Backfill Historical Files

Historical archives can be copied into `TW_FUTOPT_DIR` with the config-driven
backfill tool. It validates each source zip before copying, skips existing target
files, writes every file decision to a plain text log, and copies through a
temporary `.part` file before replacing the final target.

The package installs template backfill configs. Initialize editable copies into
your own data directory and use those paths in scheduled jobs:

```powershell
python -m tw_futopt.backfill --init-configs D:\tw_futopt\backfill_configs
```

Smoke test the two built-in configs without copying files:

```powershell
python -m tw_futopt.backfill D:\tw_futopt\backfill_configs\user_tick.json --dry-run --start-date 2017-07-28 --end-date 2017-07-28 --log logs\smoke-user-tick.log
python -m tw_futopt.backfill D:\tw_futopt\backfill_configs\futures.json --dry-run --start-date 2011-01-03 --end-date 2011-01-03 --log logs\smoke-futures.log
```

Run the full backfill. Run `user_tick.json` first because it contains
`Daily`, `Daily_B`, `Daily_C`, and `OptionsDaily`; then run `futures.json` as a
secondary source for `Daily` only.

```powershell
python -m tw_futopt.backfill D:\tw_futopt\backfill_configs\user_tick.json --log logs\backfill-user-tick.log
python -m tw_futopt.backfill D:\tw_futopt\backfill_configs\futures.json --log logs\backfill-futures.log
```

The built-in configs assume these source layouts:

- `T:\UserTick\yyyymmdd\yyyymmdd-Daily_yyyy_mm_dd.zip`
- `T:\UserTick\yyyymmdd\yyyymmdd-Daily_yyyy_mm_dd_B.zip`
- `T:\UserTick\yyyymmdd\yyyymmdd-Daily_yyyy_mm_dd_C.zip`
- `T:\UserTick\yyyymmdd\yyyymmdd-OptionsDaily_yyyy_mm_dd.zip`
- `E:\futures\yyyy\Daily_yyyy_mm_dd.zip`
