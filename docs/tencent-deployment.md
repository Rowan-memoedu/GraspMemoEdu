# Tencent Cloud production deployment

GitHub Pages remains the frontend. The backend uses the existing Tencent Lighthouse Ubuntu instance at `https://124.221.144.155`; no Cloudflare Quick Tunnel or local PC uptime is required after cutover.

## Server layout

- `/opt/graspmemoedu/app`: application source; `/opt/graspmemoedu/venv`: Python runtime.
- `/opt/graspmemoedu/judge`: existing MathAnswerJudge module and prompt.
- `/var/lib/graspmemoedu`: private course content, SQLite database, guides and settings, owned by the dedicated `graspmemoedu` service account.
- `/etc/graspmemoedu/judge.env`: root-only website-specific judge credentials. Never use Codex outsourcing credentials.
- `graspmemoedu.service`: loopback backend on 127.0.0.1:18768, automatic restart and startup.
- nginx on 443 preserves the public Host; 80 serves only ACME validation files. Administration endpoints stay unavailable publicly.

## Operations

SSH uses the existing `ubuntu` account and its existing key. Do not copy credentials into documentation or Git.

```sh
sudo systemctl status graspmemoedu nginx
sudo journalctl -u graspmemoedu -n 50
sudo systemctl list-timers 'graspmemoedu-*'
sudo /usr/local/sbin/graspmemoedu-backup
```

Let's Encrypt IP certificates use the shortlived profile. `graspmemoedu-cert-renew.timer` checks every six hours and reloads nginx after renewal. `graspmemoedu-healthcheck.timer` checks the local HTTPS/backend chain every minute; it cannot detect an outage between an external student and the cloud firewall. `graspmemoedu-backup.timer` creates and restores a verification copy daily, retaining timestamped archives for seven days under `/var/backups/graspmemoedu`. These server backups share the server's failure domain; the migration snapshot is separately retained on the Windows machine.

The old Windows maintenance startup entry is disabled during cutover so it cannot overwrite `deployment.json` with a temporary tunnel address. Do not run `Start-PublicLearning.ps1` or reinstall local maintenance against production. The original local data is a rollback snapshot, not a second writable backend. If rolling back after cloud writes have occurred, first export the cloud database and reconcile those writes; never simply start the old snapshot.

## Backup portability

Format 2 archives optionally declare `retired_topic_versions`. Historical database rows for topics absent from the current catalog are preserved without adding those topics back to the catalog or reopening them. Restore requires the declared retired topic/version pairs to exactly match database history. An active topic whose saved version differs still blocks backup/restore. Retired topics are not passed through active learning recovery, so their historical records remain unchanged. Original retired source files outside the active catalog remain in the old local data directory; this archive extension preserves their database history, not unpublished source packages.
