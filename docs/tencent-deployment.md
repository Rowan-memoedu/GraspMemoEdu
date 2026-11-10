# Tencent Cloud production deployment

GitHub Pages remains the frontend. The backend uses the existing Tencent Lighthouse Ubuntu instance at `https://124.221.144.155`; no Cloudflare Quick Tunnel or local PC uptime is required after cutover.

## Server layout

- `/opt/graspmemoedu/app`: application source; `/opt/graspmemoedu/venv`: Python runtime.
- `/opt/graspmemoedu/judge`: existing MathAnswerJudge module and prompt.
- `/var/lib/graspmemoedu`: private course content, SQLite database, guides and settings, owned by the dedicated `graspmemoedu` service account.
- `/etc/graspmemoedu/judge.env`: root-only website-specific judge credentials. Never use Codex outsourcing credentials.
- `graspmemoedu.service`: loopback backend on 127.0.0.1:18768, automatic startup; both backend and nginx use `Restart=always`, with a five-second delay after unexpected process exit (including clean exit).
- nginx on 443 preserves the public Host; 80 serves only ACME validation files. Administration endpoints stay unavailable publicly.
- The versioned proxy configuration is `deploy/nginx.conf`. Forward `/api/`, `/guide-assets/` and `/subject-guide-assets/` to the application. Do not expose the data directory with an nginx alias: the application validates referenced resources and subject permissions.
- `/etc/ssh/sshd_config.d/00-graspmemoedu-hardening.conf` disables SSH password and root login while preserving the tested `ubuntu` public-key login. Source: `deploy/ssh-hardening.conf`.
- `/etc/apt/apt.conf.d/51-graspmemoedu-security-updates` includes the Ubuntu security pocket in unattended upgrades. Source: `deploy/51-graspmemoedu-security-updates`.

## Operations

Local source, data, cache and artifact roots use `GraspMemoEdu`; the Python package and CLI are `graspmemoedu`. Website-specific environment variables use `GRASPMEMOEDU_`, including `GRASPMEMOEDU_JUDGE_API_KEY`, `GRASPMEMOEDU_JUDGE_BASE_URL` and `GRASPMEMOEDU_JUDGE_MODEL`. The independent MathAnswerJudge project's own environment interface remains separate. Renaming never changes course/Topic IDs, database schemas or learning records.

The frontend upgrades previous browser-storage keys on the same origin, preserving identities, language and drafts, with newer values taking precedence. Browser storage cannot cross the changed Pages domain: invitation-code users can sign in again to reach their existing server-side progress. Historical backup files remain original evidence and use the same restore format.

SSH uses the existing `ubuntu` account and its existing key. Do not copy credentials into documentation or Git.

```sh
sudo systemctl status graspmemoedu nginx
sudo journalctl -u graspmemoedu -n 50
sudo systemctl list-timers 'graspmemoedu-*'
sudo /usr/local/sbin/graspmemoedu-backup
cd /opt/graspmemoedu/app
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu account-list
```

Let's Encrypt IP certificates use the shortlived profile. `graspmemoedu-cert-renew.timer` checks every six hours and reloads nginx after renewal. `graspmemoedu-healthcheck.timer` checks the local HTTPS/backend chain every minute; it cannot detect an outage between an external student and the cloud firewall. `graspmemoedu-backup.timer` creates and restores a verification copy daily under `/var/backups/graspmemoedu`. The shared post-backup hook retains only the newest two complete archives, including while the owner's computer is offline; an older untransferred archive may therefore be discarded.

The private GitHub repository runs `.github/workflows/public-health.yml` twice an hour from outside Tencent. It checks public HTTPS health, the published Pages endpoint and the homepage. On failure it opens one private monitoring issue and marks the run failed; on recovery it closes the issue. GitHub notification delivery depends on the account's Actions/Issues notification settings and scheduled checks may run late. It does not restart the server.

Portable backup creation and restoration share a 3,000,000,000-byte uncompressed total limit, including the manifest. Individual entries remain capped at 100,000,000 bytes and the manifest at 4,000,000 bytes, with path, checksum, content-version and database validation retained. A successful archive creation is insufficient: the scheduled backup also restores into an isolated temporary directory before reporting success.

Paid judging has a durable daily quota in `/var/lib/graspmemoedu/judge-daily-quota.sqlite3`, shared by Lesson and Review and by all visitors. Defaults: 2000 accepted judging attempts across the site per day, no per-account cap, and 100 per guest identity, resetting at 00:00 Asia/Shanghai. Account usage is still counted in the global total and the private report. An error-result retry can require another judging call and therefore another unit; exact idempotent replays do not. Rejected or malformed answers do not consume a unit. The production settings are in `/etc/systemd/system/graspmemoedu.service.d/99-judge-quota.conf` (source: `deploy/99-judge-quota.conf`). `GRASPMEMOEDU_JUDGE_DAILY_GLOBAL` and `GRASPMEMOEDU_JUDGE_DAILY_GUEST` require positive integers; `GRASPMEMOEDU_JUDGE_DAILY_ACCOUNT=0` disables only the per-account cap. After changing them, run `systemctl daemon-reload`, then restart only `graspmemoedu.service`. Existing 12/minute per learner and eight-pending limits remain. This caps accepted calls, not provider-side currency spend or charges from calls already in flight.

The private, read-only report lists every account and guest (including zero usage), showing `learner_id`, display name and daily accepted judging count. Query it on the server; never publish these identities through the public website:

```sh
cd /opt/graspmemoedu/app
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu judge-usage-report
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu judge-usage-report --day 2026-09-25 --role guest
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu judge-usage-report --learner '<learner_id>'
```

The report does not create learning records or send judging requests. Counts begin when the daily quota database was introduced on 2026-09-24; earlier judging is not reconstructed. Site backups include the usage database when present; older archives without it remain restorable, but cannot produce a trustworthy usage report.

The existing `GraspMemoEdu-OffsiteBackup` Windows task now uses `D:/Projects/DeveloperGovernance/server-maintenance/offsite.py`, repeating every 15 minutes alongside its existing daily/logon triggers. It transfers pending GraspMemoEdu, OpenFocus, legacy Plan and PersonalWebsite snapshots with resumable chunks. GraspMemoEdu keeps `D:/AppData/GraspMemoEduOffsiteBackup`; other applications use restricted `D:/AppData/TencentCloudBackups`. SHA-256, archive/SQLite validation and atomic local commit must succeed before deleting the unchanged remote snapshot. Existing local archives are validated before retrying deletion. The old `scripts/pull_offsite_backup.py <destination>` command delegates to this implementation. Local historical backups are not pruned.

Server publish-and-verify commands use `sudo -n python3 /usr/local/lib/tencent-cloud-maintenance/host.py run-stage --path <staging-directory> -- <command>`. Success removes the exact unused staging directory immediately. A native systemd timer runs every 15 minutes to enforce retention and reclaim unused approved staging after 24 hours, protecting active processes, configuration references, symlinks, mounts, Git workspaces and public update repositories. The journal is limited to 200 MiB. Maintenance source and instructions are in `D:/Projects/DeveloperGovernance/server-maintenance/README.md`.

The old Windows maintenance startup entry is disabled so it cannot overwrite `deployment.json` with a temporary tunnel address. `cloud-authoritative.json` in the original data directory makes old start/maintenance entry points refuse to run; default `Manage-Learning.ps1` also refuses, requiring an explicit isolated `--data-dir`. The original local data is a rollback snapshot, not a second writable backend. Do not remove the marker to bypass migration safeguards. If rolling back after cloud writes have occurred, first export the cloud database and reconcile those writes; never simply start the old snapshot.

For planned server maintenance, stop `graspmemoedu-healthcheck.timer` before deliberately stopping the backend/nginx, then re-enable it when maintenance ends. Normal process termination is automatically recovered; a persistent configuration defect, cloud network outage, disk failure, expired cloud subscription or unavailable judge provider still requires diagnosis. No deployment can promise zero outages. Whole-server reboot recovery is configured through enabled units, but this migration did not reboot the machine to test it.

## Deployment acceptance

Run `python scripts/verify_deployed_guides.py https://124.221.144.155 --origin https://rowan-memoedu.github.io` after proxy/content changes. This checks every subject guide and all referenced resources through public HTTPS, including authenticated browser CORS preflights. A successful `/api/guide` JSON response alone is insufficient: the initial migration omitted asset proxy routes, causing both mathematics and physics images to return 404. The 2026-09-21 correction was applied with `nginx -t` and a graceful reload.

## Backup portability

Format 2 archives optionally declare `retired_topic_versions`. Historical database rows for topics absent from the current catalog are preserved without adding those topics back to the catalog or reopening them. Restore requires the declared retired topic/version pairs to exactly match database history. Ordinary active Topic version mismatches still block backup/restore. Topic v2 pure Introductions may retain a previous learning version until the next open; their exact version pairs are recorded and verified separately, as described in [Topic v2](topic-format-v2.md). Retired topics are not passed through active learning recovery, so their historical records remain unchanged. Original retired source files outside the active catalog remain in the old local data directory; this archive extension preserves their database history, not unpublished source packages.
