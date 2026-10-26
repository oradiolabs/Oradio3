# RMS commands

Copy-paste into RMS Oradio Command form field.

Commands run in the Oradio service context as pi:pi, with passwordless sudo, so no user or
privilege checks are needed. A command's stdout and stderr are recorded on the device: after
v1.0.6 in logging/rms.log, up to and including v1.0.6 in the application log (there the output
of a successful command is logged at debug level only; a failing command's exit code, stdout and
stderr at error level). From v2.0.0 the command text itself is also written to the application
log, logging/oradio.log; before that only at debug level.

Avoid double quotes and backslashes. Up to and including v1.0.6 the command is matched out of
the response as raw text, and those two characters do not survive that; single quotes are fine.

---

## Update an Oradio, any version

- Configure Oradio with wifi network connection to the internet.
- Verify Oradio is online; Copy command on RMS for given Oradio serial; power-cycle Oradio.
  The power cycle triggers a HEARTBEAT, and the Oradio fetches the command with it.
  - Replace `<TAG>` with the release tag (or branch) to install, e.g. `v2.0.1`.
  - To install the LATEST RELEASE, delete `<TAG>` and leave no argument in its place.
  - A tag or branch that does not exist aborts the install and leaves the current version untouched.
- Runs as its own systemd unit, so it is not stopped when the install script stops the Oradio service.
- The Oradio reboots one or more times during the install.

```
sudo systemd-run --unit=oradio-install --collect --on-active=10s --uid=pi --gid=pi /usr/bin/bash -c 'curl -fsSL --retry 3 --retry-delay 5 --max-time 120 -o /tmp/oradio-install.sh https://oradiolabs.nl/Oradio3/install && bash -n /tmp/oradio-install.sh && exec bash /tmp/oradio-install.sh <TAG>'
```

### What each part does

| Part | What it does |
| --- | --- |
| `sudo systemd-run` | Starts the command as a separate systemd unit, independent of oradio.service. The heartbeat command is run by Oradio itself. Without a separate unit, the script would kill itself the moment it stops Oradio. |
| `--unit=oradio-install` | Fixed name for the unit. This lets you follow it (`journalctl -u oradio-install`), and systemd refuses a second start while the first still exists, for example when RMS sends the command twice. |
| `--collect` | Removes the unit when it has finished, also after a failure, so a later update can use the same name again. |
| `--on-active=10s` | Starts only after 10 seconds, so the heartbeat handling in Oradio can finish first. |
| `--uid=pi --gid=pi` | Runs as user pi, not as root. The script expects this and calls sudo itself where needed. |
| `/usr/bin/bash -c '...'` | Runs the three steps below as a single command line. |
| `curl -fsSL` | Downloads the script. `-f` fails on an HTTP error such as 404 instead of saving the error page. `-sS` is silent but still shows errors. `-L` follows redirects. |
| `--retry 3 --retry-delay 5` | Retries up to 3 times with a 5-second pause, to cope with a flaky Wi-Fi connection. |
| `--max-time 120` | Aborts the download after 2 minutes, so it cannot hang forever. |
| `-o /tmp/oradio-install.sh` | Saves the script to this file. |
| `&&` | Continues only if the previous step succeeded: no check if the download fails, and no run if the check fails. |
| `bash -n /tmp/oradio-install.sh` | Checks the syntax without running anything. |
| `exec bash /tmp/oradio-install.sh` | Runs the script. With `exec`, the script replaces the intermediate shell, so the script's exit code becomes the unit's exit code directly. |
| `<TAG>` (optional) | A release tag (e.g. `v2.0.1`). Installs that release instead of the latest release. A branch name (e.g. `main`) also works. |

### Why /tmp/oradio-install.sh appears three times

The path appears three times, but the script runs only once. Each time, something different happens to it:

| Step | Command | What happens |
| --- | --- | --- |
| 1 | `curl ... -o /tmp/oradio-install.sh` | Download and save. |
| 2 | `bash -n /tmp/oradio-install.sh` | Check only, do not run: bash reads the whole script and checks that the syntax is valid, but does not run a single line. |
| 3 | `exec bash /tmp/oradio-install.sh` | Actually run the script. |

Why check first? A download that breaks off halfway leaves a truncated script. Bash runs a script line by line, so a truncated script would simply start and stop somewhere in the middle, for example right after ~/Oradio3 has been deleted. `curl -f` catches HTTP errors, but not every dropped connection. `bash -n` usually detects a truncated script, because a `fi`, `done` or `}` is then missing. The command then stops before anything has changed.

If you have SSH or console access, progress can be tracked with:
- `journalctl -u oradio-install -f` until the first reboot;
- `journalctl -u oradio-install-continue -f` after an intermediate reboot, while the install continues;
- `tail -f ~/Oradio3/logging/install.log` once the new version is in place: this file covers
  the whole install and update, and is kept after the final reboot (the journal is not).
  It starts with a banner saying whether it was a first installation or an update, and from
  which version.

On RMS:
- Check new SYS_INFO and HEARTBEAT messages and Command is empty: sent to Oradio
- A CRASH message is not expected: the install script stops the Oradio service cleanly and keeps it
  from starting at an intermediate reboot. If one arrives, check install.log in its attached logs.
- Check SYS_INFO message for sw_version

If Oradio is playing, playback continues while the new version is downloaded (it may stutter),
then stops when the install script stops the Oradio service. The Oradio stays silent until the
final reboot, also across an intermediate reboot. User hears the Oradio startup ONCE, after the
final reboot. **Essential user does NOTHING** until after that startup.
RMS operator can inform user update was successful after seeing the SYS_INFO message.

---

## Sync Oradio SharePoint with USB, v1.0.7 and later

- Configure Oradio with wifi network connection to the internet, and insert the USB.
- Verify Oradio is online; Copy command on RMS for given Oradio serial; power-cycle Oradio.
  The power cycle triggers a HEARTBEAT, and the Oradio fetches the command with it.
  - Replace `PASSWORD` with the password for accessing the Oradio SharePoint. Keep the single
    quotes. The password itself must not contain a single quote, double quote or backslash.
  - Replace `[--all | WHAT-TO-SYNC]`, brackets included, with either `--all` to sync the complete
    USB, or with one or more paths to sync, separated by spaces.
  - With `--all` the USB becomes a mirror of SharePoint: everything on the USB that is not on
    SharePoint is deleted, so existing playlists are removed and existing presets are reset.
  - With one or more paths only those paths are synced, e.g. `Muziek`, or `Muziek/Evergreens`,
    or `Muziek Speellijsten` (two paths). The rest of the USB is left untouched.
  - Paths are case-sensitive. A path that does not exist on SharePoint aborts the whole run
    before anything is synced; the log lists the paths that are available.
- Runs as its own systemd unit, so it is not stopped when the sync script stops the Oradio service.
- The Oradio does not reboot: the sync script restarts the services it stopped when it is done.

Note: from v2.0.0 the full command, password included, is written to logging/oradio.log, which
is uploaded to RMS with incident and crash reports.

```
sudo systemd-run --unit=oradio-usb-sync --collect --on-active=10s --uid=pi --gid=pi --setenv=HOME=/home/pi --setenv=ORADIO_SYNC_PW='PASSWORD' /usr/bin/bash /home/pi/Oradio3/tools/USB_sync.sh --yes [--all | WHAT-TO-SYNC]
```

### What each part does

| Part | What it does |
| --- | --- |
| `sudo systemd-run` | Starts the command as a separate systemd unit, independent of oradio.service. The heartbeat command is run by Oradio itself. Without a separate unit, the sync would kill itself the moment it stops Oradio. |
| `--unit=oradio-usb-sync` | Fixed name for the unit. This lets you follow it (`journalctl -u oradio-usb-sync`), and systemd refuses a second start while the first still exists, for example when RMS sends the command twice. |
| `--collect` | Removes the unit when it has finished, also after a failure, so a later sync can use the same name again. |
| `--on-active=10s` | Starts only after 10 seconds, so the heartbeat handling in Oradio can finish first. |
| `--uid=pi --gid=pi` | Runs as user pi, not as root. The script expects this and calls sudo itself where needed. |
| `--setenv=HOME=/home/pi` | Sets the home directory of user pi for the script. |
| `--setenv=ORADIO_SYNC_PW='PASSWORD'` | Passes the SharePoint password to the script, so it does not ask for it. |
| `/usr/bin/bash /home/pi/Oradio3/tools/USB_sync.sh` | Runs the sync script of the installed Oradio version. |
| `--yes` | Answers all questions with their default, as nobody is there to answer them. |
| `--all` | Syncs the complete SharePoint tree and deletes everything on the USB that is not on SharePoint. |
| `WHAT-TO-SYNC` | One or more paths, relative to the SharePoint sync root, to sync instead of the complete tree. |

If you have SSH or console access, progress can be tracked with:
- `journalctl -u oradio-usb-sync -f` while the sync runs;
- `tail -f ~/Oradio3/logging/usb_sync.log`: this file is kept after the sync (the journal is not
  after a reboot).

On RMS:
- Check new SYS_INFO and HEARTBEAT messages and Command is empty: sent to Oradio
- After the sync the Oradio service is started again, which sends a new SYS_INFO message.

If Oradio is playing, playback stops when the sync script stops the Oradio service. The Oradio
stays silent while the USB is checked and synced, which can take long for a large sync. User
hears the Oradio startup when the sync is done. **Essential user does NOTHING**, and does not
remove the USB, until after that startup.
RMS operator can inform user the sync was successful after seeing the SYS_INFO message.

---
