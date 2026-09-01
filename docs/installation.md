---
title: Installation
layout: default
nav_order: 2
description: Requirements and step-by-step installation of the weewx-airgradient extension.
---

# Installing weewx-airgradient

[weewx-airgradient manual](https://chaunceygardiner.github.io/weewx-airgradient/) · [weewx-airgradient on GitHub](https://github.com/chaunceygardiner/weewx-airgradient) · [Report an issue](https://github.com/chaunceygardiner/weewx-airgradient/issues)

---

## Requirements

* WeeWX 4.6 or later (4.6 through 4.10, or any WeeWX 5)
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

   WeeWX 5, pip install (`weectl` lives in the virtual environment, so
   activate it first; yours may sit elsewhere, `~/weewx-venv` is the usual
   place):

   ```
   source ~/weewx-venv/bin/activate
   weectl extension install weewx-airgradient.zip
   ```

   WeeWX 5, Debian or Red Hat package install (`weectl` is already on the
   path).  No `sudo`: that install put your account in the `weewx` group,
   which owns the files -- if you installed WeeWX in this same login
   session, log out and back in first so the group membership takes
   effect.

   ```
   weectl extension install weewx-airgradient.zip
   ```

   WeeWX 4 (on a setup.py install use the full path, e.g.
   `/home/weewx/bin/wee_extension`; a package install has it on the path):

   ```
   sudo wee_extension --install weewx-airgradient.zip
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

Upgrading replaces the bundled skin (`skins/airgradient/`) — including its
`lang/` files — so if you customized it, save a copy first.  Label and text
overrides written into `weewx.conf` instead survive an upgrade; see
[Translating the demo page](i18n.md).  An upgrade never rewrites your
existing `weewx.conf`, so any option added by a new release keeps its old
value until you change it yourself; the release notes call out when that
matters.
