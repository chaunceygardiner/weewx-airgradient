---
title: Installing weewx-airgradient
description: Requirements and step-by-step installation of the weewx-airgradient extension.
---

# Installing weewx-airgradient

[Home](index.md) ·
[Configuration](configuration.md) ·
[Filling gaps after downtime](gaps.md) ·
[Fields in reports](fields.md) ·
[Troubleshooting](troubleshooting.md) ·
[GitHub project](https://github.com/chaunceygardiner/weewx-airgradient)

---

## Requirements

* WeeWX 4 or 5
* Python 3.9 or greater
* The [wview_extended](https://github.com/weewx/weewx/blob/master/src/schemas/wview_extended.py)
  schema (it contains the `pm1_0`, `pm2_5`, `pm10_0` and `co2` columns)
* The `python-dateutil` and `requests` Python packages
* An AirGradient monitor reachable on your local network
* Recommended: an
  [airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
  polling that monitor.  Gap filling requires one; everything else works
  without it — see [Filling gaps after downtime](gaps.md).

Not sure about the schema?  wview_extended is the default for new WeeWX 4
and 5 installs; only databases created under WeeWX 3 and carried forward
still use the old schema.  To check, look for `pm2_5` in your archive
table, e.g.:

```
echo '.schema archive' | sqlite3 /var/lib/weewx/weewx.sdb | grep pm2_5
```

## Steps

1. Find your monitor on the network and verify you can reach it.

   Find the monitor's IP address (e.g., in your router's DHCP client list or
   the AirGradient dashboard), then browse to
   `http://<monitor-ip>/measures/current`.  You should see a page of JSON
   sensor data — that is exactly the endpoint this extension polls.  Since
   the extension needs a stable address, give the monitor a DHCP reservation
   in your router (or a hostname in local DNS) so its address doesn't
   change.

1. Install the prerequisite Python packages.

   For a WeeWX pip install, activate WeeWX's virtual environment first,
   then:

   ```
   pip install python-dateutil requests
   ```

   For a Debian package install of WeeWX:

   ```
   apt install python3-dateutil python3-requests
   ```

1. Download the latest release, `weewx-airgradient.zip`, from the
   [GitHub repository](https://github.com/chaunceygardiner/weewx-airgradient).

1. Install the extension and restart WeeWX.

   WeeWX 5:

   ```
   weectl extension install weewx-airgradient.zip
   ```

   WeeWX 4 (adjust the path if WeeWX is not installed in /home/weewx):

   ```
   sudo /home/weewx/bin/wee_extension --install weewx-airgradient.zip
   ```

1. Consider installing
   [airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
   (optional, recommended).

   It polls the monitor on your behalf, serves period averages, and keeps an
   archive history — which is the only thing that can fill in the periods
   WeeWX was not running for.  Set its `archive-interval-secs` to WeeWX's
   archive interval, then configure it as `[[Proxy1]]`.

1. Edit the `[AirGradient]` section of weewx.conf (created by the install)
   to point at your monitor and fill in the `[[LoopFields]]` mapping — see
   [Configuration](configuration.md) — then restart WeeWX.

1. To check the install, wait for a reporting cycle, then browse to the
   WeeWX site with `/airgradient` appended to the URL (e.g.,
   `http://weewx-machine/weewx/airgradient`).  The graphs fill in over time.

## Upgrading

Upgrading replaces the bundled skin (`skins/airgradient/`) — if you
customized it, save a copy first.  An upgrade never rewrites your existing
`weewx.conf`, so any option added by a new release keeps its old value until
you change it yourself; the release notes call out when that matters.
