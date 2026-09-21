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

## Operations

SSH uses the existing `ubuntu` account and its existing key. Do not copy credentials into documentation or Git.

```sh
sudo systemctl status graspmemoedu nginx
sudo journalctl -u graspmemoedu -n 50
sudo systemctl list-timers 'graspmemoedu-*'
sudo /usr/local/sbin/graspmemoedu-backup
cd /opt/graspmemoedu/app
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m math_learning --data-dir /var/lib/graspmemoedu account-list
```

Let's Encrypt IP certificates use the shortlived profile. `graspmemoedu-cert-renew.timer` checks every six hours and reloads nginx after renewal. `graspmemoedu-healthcheck.timer` checks the local HTTPS/backend chain every minute; it cannot detect an outage between an external student and the cloud firewall. `graspmemoedu-backup.timer` creates and restores a verification copy daily, retaining timestamped archives for seven days under `/var/backups/graspmemoedu`. These server backups share the server's failure domain; the migration snapshot is separately retained on the Windows machine.

The old Windows maintenance startup entry is disabled so it cannot overwrite `deployment.json` with a temporary tunnel address. `cloud-authoritative.json` in the original data directory makes old start/maintenance entry points refuse to run; default `Manage-Learning.ps1` also refuses, requiring an explicit isolated `--data-dir`. The original local data is a rollback snapshot, not a second writable backend. Do not remove the marker to bypass migration safeguards. If rolling back after cloud writes have occurred, first export the cloud database and reconcile those writes; never simply start the old snapshot.

For planned server maintenance, stop `graspmemoedu-healthcheck.timer` before deliberately stopping the backend/nginx, then re-enable it when maintenance ends. Normal process termination is automatically recovered; a persistent configuration defect, cloud network outage, disk failure, expired cloud subscription or unavailable judge provider still requires diagnosis. No deployment can promise zero outages. Whole-server reboot recovery is configured through enabled units, but this migration did not reboot the machine to test it.

## Deployment acceptance

Run `python scripts/verify_deployed_guides.py https://124.221.144.155 --origin https://amymiller76717-sudo.github.io` after proxy/content changes. This checks every subject guide and all referenced resources through public HTTPS, including authenticated browser CORS preflights. A successful `/api/guide` JSON response alone is insufficient: the initial migration omitted asset proxy routes, causing both mathematics and physics images to return 404. The 2026-09-21 correction was applied with `nginx -t` and a graceful reload.

## Backup portability

Format 2 archives optionally declare `retired_topic_versions`. Historical database rows for topics absent from the current catalog are preserved without adding those topics back to the catalog or reopening them. Restore requires the declared retired topic/version pairs to exactly match database history. An active topic whose saved version differs still blocks backup/restore. Retired topics are not passed through active learning recovery, so their historical records remain unchanged. Original retired source files outside the active catalog remain in the old local data directory; this archive extension preserves their database history, not unpublished source packages.
