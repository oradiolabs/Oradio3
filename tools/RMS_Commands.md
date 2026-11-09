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
- Runs as its own systemd unit, so it is not stopped when the install script stops the Oradio
  service.
- The Oradio reboots one or more times during the install.

```
sudo systemd-run --unit=oradio-install --collect --on-active=60s --uid=pi --gid=pi /usr/bin/bash -c 'f=/etc/initramfs-tools/update-initramfs.conf; if [ -f $f ]; then sudo sed -i s/^update_initramfs=.*/update_initramfs=no/ $f; grep -q ^update_initramfs= $f || echo update_initramfs=no | sudo tee -a $f >/dev/null; fi; sudo DEBIAN_FRONTEND=noninteractive dpkg --force-confdef --force-confold --configure -a; curl -fsSL --retry 3 --retry-delay 5 --max-time 120 -o /tmp/oradio-install.sh https://oradiolabs.nl/Oradio3/install && bash -n /tmp/oradio-install.sh && exec bash /tmp/oradio-install.sh <TAG>'
```

### What the command does, in order

| Step | What happens | If it fails |
| --- | --- | --- |
| 1 | systemd starts a separate unit, `oradio-install`, and returns at once. The Oradio finishes handling the heartbeat. | No unit, nothing happens. The Oradio is untouched. |
| 2 | The unit waits 60 seconds. | - |
| 3 | Initramfs updates are switched off. | Continues with step 4. |
| 4 | An interrupted package installation is finished. | Continues with step 5; the install script reports the package error. |
| 5 | The install script is downloaded. | Stops. The Oradio is untouched. |
| 6 | Its syntax is checked. | Stops. The Oradio is untouched. |
| 7 | The install script runs and updates the Oradio. | The install script reports it in `install.log`. |

Steps 3 and 4 are separated by `;`, so they always run and never stop the update. Steps 5, 6 and 7
are joined by `&&`, so each runs only if the previous one succeeded.

### What each part does

| Part | What it does |
| --- | --- |
| `sudo systemd-run` | Starts the command as a separate systemd unit, independent of oradio.service. The heartbeat command is run by Oradio itself. Without a separate unit, the script would kill itself the moment it stops Oradio. |
| `--unit=oradio-install` | Fixed name for the unit. This lets you follow it (`journalctl -u oradio-install`), and systemd refuses a second start while the first still exists, for example when RMS sends the command twice. |
| `--collect` | Removes the unit when it has finished, also after a failure, so a later update can use the same name again. |
| `--on-active=60s` | Starts only after 60 seconds, so the time synchronisation can finish first. See [Why wait 60 seconds](#why-wait-60-seconds). |
| `--uid=pi --gid=pi` | Runs as user pi, not as root. The script expects this and calls sudo itself where needed. |
| `/usr/bin/bash -c '...'` | Runs all steps between the single quotes as one command line. |
| `f=/etc/initramfs-tools/update-initramfs.conf` | Stores the path of the initramfs configuration file in `$f`, so it is written only once. |
| `if [ -f $f ]; then ... fi` | Only touches the file if it exists. Without the initramfs-tools package there is no initramfs to update. |
| `sudo sed -i s/^update_initramfs=.*/update_initramfs=no/ $f` | Changes an existing `update_initramfs=` line to `update_initramfs=no`, whatever it was set to. |
| `grep -q ^update_initramfs= $f \|\| echo update_initramfs=no \| sudo tee -a $f >/dev/null` | If the file has no `update_initramfs=` line at all, appends one. `sudo tee -a` is needed because a plain `>>` would be done by user pi, who may not write the file. |
| `sudo DEBIAN_FRONTEND=noninteractive dpkg --configure -a` | Finishes the configuration of packages an earlier run left half-done. Does nothing if there are none. `DEBIAN_FRONTEND=noninteractive` makes sure no package waits for an answer. |
| `--force-confdef --force-confold` | When a package brings a new version of a configuration file that was changed on the Oradio: keep the Oradio's version. So the `update_initramfs=no` set above is not undone. |
| `curl -fsSL` | Downloads the script. `-f` fails on an HTTP error such as 404 instead of saving the error page. `-sS` is silent but still shows errors. `-L` follows redirects. |
| `--retry 3 --retry-delay 5` | Retries up to 3 times with a 5-second pause, to cope with a flaky Wi-Fi connection. |
| `--max-time 120` | Aborts the download after 2 minutes, so it cannot hang forever. |
| `-o /tmp/oradio-install.sh` | Saves the script to this file. |
| `&&` | Continues only if the previous step succeeded: no check if the download fails, and no run if the check fails. |
| `bash -n /tmp/oradio-install.sh` | Checks the syntax without running anything. |
| `exec bash /tmp/oradio-install.sh` | Runs the script. With `exec`, the script replaces the intermediate shell, so the script's exit code becomes the unit's exit code directly. |
| `<TAG>` (optional) | A release tag (e.g. `v2.0.1`). Installs that release instead of the latest release. A branch name (e.g. `main`) also works. |

### Why /tmp/oradio-install.sh appears three times

The path appears three times, but the script runs only once. Each time, something different happens
to it:

| Step | Command | What happens |
| --- | --- | --- |
| 1 | `curl ... -o /tmp/oradio-install.sh` | Download and save. |
| 2 | `bash -n /tmp/oradio-install.sh` | Check only, do not run: bash reads the whole script and checks that the syntax is valid, but does not run a single line. |
| 3 | `exec bash /tmp/oradio-install.sh` | Actually run the script. |

Why check first? A download that breaks off halfway leaves a truncated script. Bash runs a script
line by line, so a truncated script would simply start and stop somewhere in the middle, for example
right after ~/Oradio3 has been deleted. `curl -f` catches HTTP errors, but not every dropped
connection. `bash -n` usually detects a truncated script, because a `fi`, `done` or `}` is then
missing. The command then stops before anything has changed.

### Why initramfs updates are switched off

The Oradio boots without an initramfs: `config.txt` sets `auto_initramfs=0`, so the bootloader
never loads one. Debian still rebuilds it whenever a package with an initramfs hook changes
(e2fsprogs, udev, kmod, ...), and the install script installs and upgrades packages. On an Oradio
that rebuild can fail with `mkinitramfs: failed to determine device for /`. The package
installation then stops, initramfs-tools is left half-configured, and the update aborts.

With `update_initramfs=no` that rebuild is skipped, so it cannot fail. Nothing is lost, because
the initramfs is never used.

The step is in the command because releases up to and including v2.0.1 do not switch the rebuild
off themselves. Later releases do, in `tools/pkg-helper.sh`, and so does the install script
(`tools/install`) that the command downloads, before it hands over to the release to install. Once
that install script is published on oradiolabs.nl, it covers every update whatever the release,
and the initramfs and dpkg steps in this command are no longer needed.

### Why dpkg --configure -a

When a package installation is interrupted, by the initramfs problem above, a power cut or an
earlier failed update, dpkg remembers which packages are half-configured. Every later package
installation then fails with `dpkg was interrupted` or with the original error, until that is
finished. `dpkg --configure -a` finishes it before the install script starts.

It needs no network and does nothing if there is nothing to finish. It runs after the initramfs
step on purpose: a package left half-configured by the initramfs problem can only be finished once
the rebuild is switched off.

### Why wait 60 seconds

The Pi has no clock that keeps running while it is off. At start-up it sets its clock to the last
time it saved, so after the Oradio has been off for a while its clock is behind until it has
reached a time server on the internet. That takes some tens of seconds after the WiFi connection.

Before that, downloads from GitHub fail. A secure connection checks that the server's certificate
is valid on today's date. When the Oradio's clock is behind, a certificate issued after that date
counts as not yet valid. The install script then stops with `Aborting: Could not fetch tags from
GitHub`, and the Oradio is left untouched.

The heartbeat that fetches the command is sent as soon as the WiFi connects, so a power-cycled
Oradio receives the command while its clock may still be behind. The 60 seconds give the clock
time to be set first.

On an Oradio with SSH access, this shows whether the clock was set in time:

```
journalctl -b -o short-monotonic --no-pager -u systemd-timesyncd -u oradio-install | grep -E 'Initial clock synchronization|Started|update started'
```

The times are seconds since start-up. `Initial clock synchronization` must come before
`Started oradio-install.service`.

### Why there are no double quotes in the command

Up to and including v1.0.6 the Oradio reads the command out of the RMS response as raw text, and
double quotes and backslashes do not survive that (see the top of this page). The command is
written without them, so it works on every version.

That is why `$f` and the `sed` and `grep` patterns have no quotes around them. Without quotes they
work the same here: the path and the patterns contain no spaces, and the `sed` pattern matches no
file name.

If you have SSH or console access, progress can be tracked with:
- `journalctl -u oradio-install -f` until the first reboot;
- `journalctl -u oradio-install-continue -f` after an intermediate reboot, while the install
  continues;
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
  - A path cannot contain a space: the paths are inside the single quotes of `bash -c`, where
    they cannot be quoted themselves.
  - Paths are case-sensitive. A path that does not exist on SharePoint aborts the whole run
    before anything is synced; the log lists the paths that are available.
- Runs as its own systemd unit, so it is not stopped when the sync script stops the Oradio service.
- The Oradio does not reboot: the sync script restarts the services it stopped when it is done.

Note: from v2.0.0 the full command, password included, is written to logging/oradio.log, which
is uploaded to RMS with incident and crash reports.

```
sudo systemd-run --unit=oradio-usb-sync --collect --on-active=60s --uid=pi --gid=pi --setenv=HOME=/home/pi --setenv=ORADIO_SYNC_PW='PASSWORD' /usr/bin/bash -c 'f=/etc/initramfs-tools/update-initramfs.conf; if [ -f $f ]; then sudo sed -i s/^update_initramfs=.*/update_initramfs=no/ $f; grep -q ^update_initramfs= $f || echo update_initramfs=no | sudo tee -a $f >/dev/null; fi; sudo DEBIAN_FRONTEND=noninteractive dpkg --force-confdef --force-confold --configure -a; exec bash /home/pi/Oradio3/tools/USB_sync.sh --yes [--all | WHAT-TO-SYNC]'
```

### What the command does, in order

| Step | What happens | If it fails |
| --- | --- | --- |
| 1 | systemd starts a separate unit, `oradio-usb-sync`, and returns at once. The Oradio finishes handling the heartbeat. | No unit, nothing happens. The Oradio is untouched. |
| 2 | The unit waits 60 seconds. | - |
| 3 | Initramfs updates are switched off. | Continues with step 4. |
| 4 | An interrupted package installation is finished. | Continues with step 5; the sync script reports the package error. |
| 5 | The sync script checks the USB, installs the tools it needs, and syncs. | The sync script reports it in its log (see below), and starts again what it stopped. |

Steps 3 and 4 are the same as in the update command, for the same reasons: see
[Why initramfs updates are switched off](#why-initramfs-updates-are-switched-off) and
[Why dpkg --configure -a](#why-dpkg---configure--a). Here the packages are the ones the sync
script installs or upgrades before it syncs: dosfstools, e2fsprogs, psmisc and rclone. An
e2fsprogs upgrade rebuilds the initramfs. Up to and including v2.0.1 the sync script does not
switch that rebuild off itself. A sync does not run the install script, so unlike in the update
command these steps stay needed for those releases.

### Why wait 60 seconds here as well

For the same reason as the update: see [Why wait 60 seconds](#why-wait-60-seconds). Before the
clock is set, rclone cannot reach SharePoint: the connection is secure, and the certificate is
checked against the date. The sync then fails. The package lists the sync script fetches carry a
date too, and are refused as not yet valid; the script then carries on with the lists it has.

To check whether the clock was set in time, use the command in that section with
`-u oradio-usb-sync` in place of `-u oradio-install`.

### What each part does

| Part | What it does |
| --- | --- |
| `sudo systemd-run` | Starts the command as a separate systemd unit, independent of oradio.service. The heartbeat command is run by Oradio itself. Without a separate unit, the sync would kill itself the moment it stops Oradio. |
| `--unit=oradio-usb-sync` | Fixed name for the unit. This lets you follow it (`journalctl -u oradio-usb-sync`), and systemd refuses a second start while the first still exists, for example when RMS sends the command twice. |
| `--collect` | Removes the unit when it has finished, also after a failure, so a later sync can use the same name again. |
| `--on-active=60s` | Starts only after 60 seconds, so the time synchronisation can finish first. See [Why wait 60 seconds here as well](#why-wait-60-seconds-here-as-well). |
| `--uid=pi --gid=pi` | Runs as user pi, not as root. The script expects this and calls sudo itself where needed. |
| `--setenv=HOME=/home/pi` | Sets the home directory of user pi for the script. |
| `--setenv=ORADIO_SYNC_PW='PASSWORD'` | Passes the SharePoint password to the script, so it does not ask for it. |
| `/usr/bin/bash -c '...'` | Runs all steps between the single quotes as one command line. |
| `f=...; if [ -f $f ]; then ... fi` | Switches initramfs updates off, exactly as in the update command; see its table above. |
| `sudo DEBIAN_FRONTEND=noninteractive dpkg --force-confdef --force-confold --configure -a` | Finishes an interrupted package installation, exactly as in the update command. |
| `exec bash /home/pi/Oradio3/tools/USB_sync.sh` | Runs the sync script of the installed Oradio version. With `exec`, the script replaces the intermediate shell, so its exit code becomes the unit's exit code. |
| `--yes` | Answers all questions with their default, as nobody is there to answer them. |
| `--all` | Syncs the complete SharePoint tree and deletes everything on the USB that is not on SharePoint. |
| `WHAT-TO-SYNC` | One or more paths, relative to the SharePoint sync root, to sync instead of the complete tree. |

If you have SSH or console access, progress can be tracked with:
- `journalctl -u oradio-usb-sync -f` while the sync runs;
- `tail -f ~/Oradio3/logging/usb.log`: this file is kept after the sync (the journal is not
  after a reboot). The sync's lines start with `USB_sync:`, between the mount and unmount lines
  of the USB. Up to and including v2.0.1 the sync writes to `usb_sync.log` instead.

On RMS:
- Check new SYS_INFO and HEARTBEAT messages and Command is empty: sent to Oradio
- After the sync the Oradio service is started again, which sends a new SYS_INFO message.

If Oradio is playing, playback stops when the sync script stops the Oradio service. The Oradio
stays silent while the USB is checked and synced, which can take long for a large sync. User
hears the Oradio startup when the sync is done. **Essential user does NOTHING**, and does not
remove the USB, until after that startup.
RMS operator can inform user the sync was successful after seeing the SYS_INFO message.

---
