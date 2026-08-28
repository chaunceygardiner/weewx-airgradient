# weewx-airgradient

A WeeWX extension that reads an [AirGradient](https://www.airgradient.com/)
air quality monitor on the local network (or an
[airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
service) and inserts the readings — particulates, CO2, TVOC, NOx and more —
into every WeeWX loop packet.

Copyright (C) 2025-2026 by John A Kline (john@johnkline.com)

[User manual](https://chaunceygardiner.github.io/weewx-airgradient/) ·
[GitHub project](https://github.com/chaunceygardiner/weewx-airgradient)

**Requires:**
* WeeWX 4 or 5
* Python 3.9 or greater
* The [wview_extended](https://github.com/weewx/weewx/blob/master/src/schemas/wview_extended.py)
  schema (it contains the `pm1_0`, `pm2_5`, `pm10_0` and `co2` columns)
* The `python-dateutil` and `requests` Python packages
* An AirGradient monitor reachable on your local network
* Recommended: an
  [airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
  polling that monitor.  Filling gaps after downtime requires one;
  everything else works without it.

Not sure about the schema?  wview_extended is the default for new WeeWX 4
and 5 installs; only databases created under WeeWX 3 and carried forward
still use the old schema.  To check, look for `pm2_5` in your archive
table, e.g.:

```
echo '.schema archive' | sqlite3 /var/lib/weewx/weewx.sdb | grep pm2_5
```

## What it does

With the suggested `[LoopFields]` mapping (shown in Configuration below),
every loop packet is populated with:

| Loop field  | AirGradient field | Contents                                        |
|-------------|-------------------|-------------------------------------------------|
| `pm1_0`     | `pm01`            | PM1.0 concentration (µg/m³)                     |
| `pm2_5`     | `pm02Compensated` | PM2.5 concentration (µg/m³), compensated        |
| `pm10_0`    | `pm10`            | PM10.0 concentration (µg/m³)                    |
| `co2`       | `rco2`            | CO2 (ppm)                                       |
| `tvocIndex` | `tvocIndex`       | Sensirion TVOC index                            |
| `tvoc`      | `tvocRaw`         | TVOC raw value                                  |
| `noxIndex`  | `noxIndex`        | Sensirion NOx index                             |
| `nox`       | `noxRaw`          | NOx raw value                                   |

The `pm1_0`, `pm2_5`, `pm10_0` and `co2` fields are in the wview_extended
schema, so WeeWX accumulates them into archive records (and history graphs)
with no extra configuration.  The TVOC and NOx fields are not in the schema;
they are available in reports via `$current`, and a later section shows how
to add database columns for them if you want aggregates and graphs.

Two more observation types are available everywhere in reports and graphs —
without being stored in the database — via WeeWX
[XTypes](https://github.com/weewx/weewx/wiki/WeeWX-V4-user-defined-types):

| Field              | Contents                                                         |
|--------------------|------------------------------------------------------------------|
| `pm2_5_aqi`        | US EPA Air Quality Index computed from `pm2_5` (2024 definition) |
| `pm2_5_aqi_color`  | The RGB color of the AQI category, as a single integer           |

Readings are sanity checked: a reading is rejected if fields are
non-numeric or if the reading is stale.  If multiple monitors/proxies are
configured, they are tried in order until one produces a good reading.

Gaps are filled in, too.  When WeeWX is not running — a restart, a reboot, a
power cut — the station's logger keeps recording, and WeeWX archives those
records when it comes back.  They contain no air quality data, because this
extension was not there to supply any.  If an
[airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
is configured, the mapped fields are fetched from the proxy's own archive
history and filled in, so an outage no longer leaves a hole in the columns
and the graphs that draw them.  See
[Filling gaps after downtime](#filling-gaps-after-downtime).

### AQI categories

`pm2_5_aqi` conforms to the
[2024 EPA AQI definition](https://www.epa.gov/system/files/documents/2024-02/pm-naaqs-air-quality-index-fact-sheet.pdf);
`pm2_5_aqi_color` uses the EPA-defined RGB colors:

| Category                       | AQI       | 24-hr PM2.5 (µg/m³) | Color  | RGB           |
|--------------------------------|-----------|---------------------|--------|---------------|
| Good                           | 0 - 50    | 0.0 - 9.0           | Green  | (0, 228, 0)   |
| Moderate                       | 51 - 100  | 9.1 - 35.4          | Yellow | (255, 255, 0) |
| Unhealthy for Sensitive Groups | 101 - 150 | 35.5 - 55.4         | Orange | (255, 126, 0) |
| Unhealthy                      | 151 - 200 | 55.5 - 125.4        | Red    | (255, 0, 0)   |
| Very Unhealthy                 | 201 - 300 | 125.5 - 225.4       | Purple | (143, 63, 151)|
| Hazardous                      | 301 - 500 | 225.5 - 325.4       | Maroon | (126, 0, 35)  |

Concentrations above 325.4 µg/m³ map to AQI values above 500, continuing on
the same slope as AQI 301-500 (per the May 2024
[AirNow Technical Assistance Document](https://document.airnow.gov/technical-assistance-document-for-the-reporting-of-daily-air-quailty.pdf)).
The category and color remain Hazardous/Maroon.

The AQI is computed from `pm2_5` — with the suggested mapping, that is
AirGradient's compensated PM2.5 reading (`pm02Compensated`).  If you don't
want the AQI xtype (for instance, because another extension already
provides it), turn it off:

```
[AirGradient]
    enable_aqi = False
```

### Demo skin

A small demo report is installed at `<HTML_ROOT>/airgradient`:

![AirGradientReport](AirGradientReport.png)

### What's airgradient-proxy?

[airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
is a small service that polls the monitor for you and keeps its own archive
of the readings.  Running one is recommended, for three reasons:

* **It spares the monitor.**  Everything that queries an AirGradient
  competes for the same small processor.  The proxy queries it at one
  steady rate and answers everyone else.
* **It serves an average, not a spot reading.**  Each record it archives is
  an average of that whole archive period.
* **It fills the gaps.**  Because the proxy keeps archive records of its
  own, weewx-airgradient can go back and fill in the air quality data for
  the periods WeeWX was down for.  Nothing else can: a monitor queried
  directly keeps no history, so those records stay empty forever.

Two proxies on different machines can poll the same monitor for redundancy,
and weewx-airgradient will try each configured proxy in turn.  The install
is a script (`sudo ./install`) and has been tested on Debian and Raspberry
Pi OS; on other platforms it serves as a specification of the steps needed.

# Installation

1. Find your monitor on the network and verify you can reach it.

   Find the monitor's IP address (e.g., in your router's DHCP client list
   or the AirGradient dashboard), then browse to
   `http://<monitor-ip>/measures/current`.  You should see a page of JSON
   sensor data — that is exactly the endpoint this extension polls.  Since
   the extension needs a stable address, give the monitor a DHCP
   reservation in your router (or a hostname in local DNS) so its address
   doesn't change.

1. Install the prerequisite Python packages.

   For a WeeWX pip install, activate WeeWX's virtual environment first, then:

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
   archive interval, then configure it here as `[[Proxy1]]`.  See
   [What's airgradient-proxy?](#whats-airgradient-proxy) and
   [Filling gaps after downtime](#filling-gaps-after-downtime).

1. Edit the `[AirGradient]` section of weewx.conf (created by the install)
   to point at your monitor and fill in the `[[LoopFields]]` mapping (see
   Configuration below), then restart WeeWX.

1. To check the install, wait for a reporting cycle, then browse to the
   WeeWX site with `/airgradient` appended to the URL
   (e.g., `http://weewx-machine/weewx/airgradient`).  The graphs fill in
   over time.

## Configuration

```
[AirGradient]
    poll_secs = 15
    [[LoopFields]]
        pm01 = pm1_0
        pm02Compensated = pm2_5
        pm10 = pm10_0
        rco2 = co2
        tvocIndex = tvocIndex
        tvocRaw = tvoc
        noxIndex = noxIndex
        noxRaw = nox
    [[Sensor1]]
        enable = true
        hostname = airgradient
        port = 80
        timeout = 15
    [[Sensor2]]
        enable = false
        hostname = airgradient2
        port = 80
        timeout = 15
    [[Proxy1]]
        enable = false
        hostname = proxy1
        port = 8080
        timeout = 1
```

| Option      | Default                    | Meaning                                       |
|-------------|----------------------------|-----------------------------------------------|
| `poll_secs` | 15                         | How often to poll for a new reading (seconds) |
| `enable_aqi`| true                       | Whether to register the AQI xtype             |
| `enable`    | false                      | Whether this source is polled                 |
| `hostname`  |                            | Hostname or IP address of the monitor/proxy   |
| `port`      | 80 (sensor) / 8080 (proxy) | Port to connect on                            |
| `timeout`   | 1 (proxy) / 10 (sensor)    | HTTP timeout (seconds).  A proxy answers from its own database on the local network, so a second is ample; a monitor's own processor is slow, and the installer writes 15 for one. |

AirGradient monitors are specified with subsections `[[Sensor1]]`,
`[[Sensor2]]`, etc.; airgradient-proxy services with `[[Proxy1]]`,
`[[Proxy2]]`, etc.  There is no limit on the number of sensors and proxies,
but the numbering of each group must start at 1 and be consecutive (a gap
ends the scan).  On each polling round, proxies are interrogated first (low
numbers to high), then sensors; the first source that yields a sane, fresh
reading wins and no further sources are tried.

A reading is considered fresh for `max(120, 3 * poll_secs)` seconds; stale
readings are never inserted into loop packets.

### The [[LoopFields]] mapping

Each entry maps an AirGradient field (left side) to the loop-packet field
it should be written to (right side).  The installer creates the section
empty — copy in the suggested mapping shown above (or your own subset).
Without entries, no fields are written to loop packets; the extension logs
an error at startup to that effect.  The section is deliberately not
prefilled by the installer: on upgrade, weectl merges installer defaults
into your existing section, which would inject unwanted entries into a
customized mapping.

Any field the monitor reports can be mapped.  The full list:

| AirGradient field | Contents                                                         |
|-------------------|------------------------------------------------------------------|
| `serialno`        | Serial number of the monitor                                     |
| `wifi`            | WiFi signal strength                                             |
| `pm01`            | PM1.0 in µg/m³ (atmospheric environment)                         |
| `pm02`            | PM2.5 in µg/m³ (atmospheric environment)                         |
| `pm10`            | PM10 in µg/m³ (atmospheric environment)                          |
| `pm02Compensated` | PM2.5 in µg/m³ with correction applied (firmware 3.1.4 onwards)  |
| `pm01Standard`    | PM1.0 in µg/m³ (standard particle)                               |
| `pm02Standard`    | PM2.5 in µg/m³ (standard particle)                               |
| `pm10Standard`    | PM10 in µg/m³ (standard particle)                                |
| `rco2`            | CO2 in ppm                                                       |
| `pm003Count`      | Particle count 0.3µm per dL                                      |
| `pm005Count`      | Particle count 0.5µm per dL                                      |
| `pm01Count`       | Particle count 1.0µm per dL                                      |
| `pm02Count`       | Particle count 2.5µm per dL                                      |
| `pm50Count`       | Particle count 5.0µm per dL (indoor monitors only)               |
| `pm10Count`       | Particle count 10µm per dL (indoor monitors only)                |
| `atmp`            | Temperature in °C (converted to the loop packet's unit system)   |
| `atmpCompensated` | Temperature in °C with correction applied (converted, as above)  |
| `rhum`            | Relative humidity                                                |
| `rhumCompensated` | Relative humidity with correction applied                        |
| `tvocIndex`       | Sensirion VOC index                                              |
| `tvocRaw`         | VOC raw value                                                    |
| `noxIndex`        | Sensirion NOx index                                              |
| `noxRaw`          | NOx raw value                                                    |
| `boot`            | Counts every measurement cycle; low counts indicate restarts     |
| `bootCount`       | Same as boot (Home Assistant compatibility; deprecated)          |
| `ledMode`         | Current configuration of the LED mode                            |
| `firmware`        | Current firmware version                                         |
| `model`           | Current model name                                               |

All fields are optional: AirGradient models differ in which fields they
report, and missing fields are simply skipped.

The four non-numeric fields — `serialno`, `ledMode`, `firmware` and `model`
— can be mapped, but cannot be filled in for an archive period WeeWX missed:
there is no average of them to write.  See
[Filling gaps after downtime](#filling-gaps-after-downtime).

### Adding TVOC/NOx columns to the database (optional)

`tvoc`, `tvocIndex`, `nox` and `noxIndex` are not in the wview_extended
schema, so out of the box they are only available as current values.  To
get archive records, aggregates (`$day.nox.avg`) and graphs for them, add
the columns:

```
sudo systemctl stop weewx
source /home/weewx/weewx-venv/bin/activate
weectl database add-column tvoc --type=REAL
weectl database add-column tvocIndex --type=REAL
weectl database add-column nox --type=REAL
weectl database add-column noxIndex --type=REAL
sudo systemctl start weewx
```

# Filling gaps after downtime

If at least one `[[ProxyN]]` source is enabled, weewx-airgradient also fills
in air quality data for the archive periods WeeWX itself was not running for.
When WeeWX starts, the station's logger hands over the records it kept while
WeeWX was down; those records contain none of this extension's fields,
because nothing was there to supply them.  For each such record, the proxies
are asked — in configured order — for the archive records covering that
period, and the average is written into the record before WeeWX stores it.
Exactly the fields in `[[LoopFields]]` are filled, with exactly the values
the live path would have written: `pm2_5` is the compensated reading if that
is what the mapping asks for, and a temperature arrives in the record's own
unit system.  A backfilled `pm2_5` also restores `pm2_5_aqi` and
`pm2_5_aqi_color` for that period, since the AQI xtype computes them from
what is stored.

**Set airgradient-proxy's `archive-interval-secs` to match WeeWX's archive
interval.**  WeeWX logs the interval it is using at startup (`Using archive
interval of 300 seconds`), and weewx-airgradient logs the same number
(`archive_interval: 300`).  With the two matched, each proxy record lines up
exactly with one WeeWX period.  A proxy that archives more often is handled
— its records for the period are averaged — but a proxy that archives
*less* often than WeeWX has no record to offer for most periods, and those go
unfilled.

Periods WeeWX did see are never touched: whatever WeeWX averaged from the
loop packets stands.  That includes a period it saw only part of — one loop
packet's worth of data is what WeeWX would have stored for that period
anyway.

A proxy normally has the record for the period that has only just closed: its
polls are aligned to the clock, so one lands on the archive boundary and the
record is written a second or two later — before WeeWX archives that period
at all.  When no proxy has it — a proxy running with a `poll-freq-offset`
can still be a few seconds behind, and one that was down for the period has
nothing — the proxy's two minute average stands in, and then only if the two
minutes it covers reach into the period being filled.  Any period further
back that no proxy can answer for is left alone: an empty column is the
honest answer, and better than a value that describes some other stretch of
time.

With no proxy configured, none of this happens.  A monitor queried directly
keeps no history, so there is nothing to ask for, and the columns for those
periods stay empty.

Two log messages come from this, one per archive record:

```
INFO user.airgradient: Backfilled pm1_0, pm2_5, pm10_0, co2 into archive record 2026-08-26 18:40:00 PDT (1787794800).
INFO user.airgradient: No proxy data with which to fill pm1_0, pm2_5, pm10_0, co2 in archive record ...
```

The second is also how a proxy that is down announces itself, once per
archive period, for as long as it stays down.

# Using weewx-airgradient fields in reports

Current values:

```
$current.pm1_0
$current.pm2_5
$current.pm10_0
$current.co2
$current.tvoc
$current.tvocIndex
$current.nox
$current.noxIndex
$current.pm2_5_aqi
$current.pm2_5_aqi_color
```

Aggregates work for both the database-backed fields and the AQI xtypes
(supported AQI aggregates: `avg`, `min`, `max`, `first`, `last`, `count`):

```
$day.pm2_5.max
$week.co2.avg
$day.pm2_5_aqi.max
```

Both `pm2_5_aqi` and `pm2_5_aqi_color` can also be graphed, e.g. in
skin.conf's `[ImageGenerator]` section:

```
        [[[dayaqi]]]
            [[[[pm2_5_aqi]]]]
```

`pm2_5_aqi_color` is an [RGBint](https://www.shodor.org/stella2java/rgbint.html)
value, useful for displaying the AQI in the color of its category.  To unpack
it in a Cheetah template:

```
#set $color = int($current.pm2_5_aqi_color.raw)
#set $blue  =  $color & 255
#set $green = ($color >> 8) & 255
#set $red   = ($color >> 16) & 255
```

## How AQI values are computed (and stored)

AQI is always computed on demand from the stored `pm2_5` concentration —
there is no AQI column in the database, and none is needed: `$current`,
aggregates and graphs all resolve through the extension's AQI xtype.  For
real-time consumers (e.g., MQTT), `pm2_5_aqi` and `pm2_5_aqi_color` are
also present in every LOOP packet (unless `enable_aqi = false`).

There is no performance reason to store AQI (or its color) either, even
for long-term plots.  For an aggregated plot (e.g., a month of daily
maxima) the database aggregates the stored `pm2_5` exactly as it would
aggregate a stored AQI column, and the conversion to AQI and color — a
single interpolation and a category lookup — runs once per plotted
point, not once per database row; spans covering whole days are served
from the `pm2_5` daily-summary table without scanning the archive at
all.  Converting after aggregation is also the EPA-correct order of
operations: AQI is a non-linear transform of concentration, so the
average of per-record AQI values is not the AQI of the average
concentration (and an averaged RGB color can belong to no EPA category
at all).

To keep the on-demand computation authoritative, the extension registers
`extractor = noop` for both AQI fields so that WeeWX's accumulator does
not average them into archive records (averaging AQI values is
meaningless, since AQI is a non-linear transform of concentration).  An
`[Accumulator]` section in weewx.conf takes precedence if you
deliberately want different behavior.

### If you added an AQI column to your database

Some users have added a `pm2_5_aqi` (or `pm2_5_aqi_color`) column to their
database schema.  As of 2.0.1 the accumulator no longer fills such a
column, and any values stored in it *before* 2.0.1 are accumulator
averages that disagree with what the xtype computes (non-integer, and
averaged across a non-linear transform).  While present, those stored
values also override the xtype for `$current`.

**The cleanest fix is to remove the column.**  With WeeWX stopped (for a
pip install, activate WeeWX's virtual environment first):

WeeWX 5:

```
weectl database drop-columns pm2_5_aqi
```

WeeWX 4 (adjust the path if WeeWX is not installed in /home/weewx):

```
sudo /home/weewx/bin/wee_database --drop-columns=pm2_5_aqi
```

Name exactly the column(s) you added (repeat for `pm2_5_aqi_color` if you
added that too — naming a column that doesn't exist aborts the whole
command).  This also removes the matching daily-summary table.  Restart
WeeWX; no configuration changes are needed — `$current`, aggregates and
graphs all resolve through the xtype again.

**If something outside WeeWX reads the column directly** (e.g., Grafana),
keep it and have WeeWX compute it through the xtype, which stores
correctly EPA-rounded values:

```
[StdWXCalculate]
    [[Calculations]]
        pm2_5_aqi = prefer_hardware
        pm2_5_aqi_color = prefer_hardware
```

Then purge any values stored before 2.0.1 and backfill them through the
xtype:

1. Add the `[StdWXCalculate]` entries above to weewx.conf.

1. Stop WeeWX and back up the database.

1. NULL out the old values — for each AQI column you added, e.g. with
   SQLite (adapt for MySQL):

   ```
   sqlite3 /path/to/archive.sdb "UPDATE archive SET pm2_5_aqi = NULL;"
   ```

1. Backfill.  WeeWX 5: `weectl database calc-missing`; WeeWX 4:
   `wee_database --calc-missing`.  This recomputes each NULLed value from
   that record's stored `pm2_5` and recalculates the daily summaries.
   (It loads the extension to get the AQI xtype, so expect AirGradient's
   startup log lines, including a sensor fetch.  The AQI xtype must be
   available: either `enable_aqi` is on — the default — or weewx-purple
   is installed alongside and provides it.)

1. Restart WeeWX.

# Running alongside a PurpleAir extension

If another extension (e.g.,
[weewx-purple](https://github.com/chaunceygardiner/weewx-purple)) already
supplies `pm1_0`, `pm2_5`, `pm10_0` and the AQI, keep those and take only
AirGradient's extra sensors: turn off the AQI xtype and leave the pm fields
out of the mapping.

```
[AirGradient]
    enable_aqi = False
    [[LoopFields]]
        rco2 = co2
        tvocIndex = tvocIndex
        tvocRaw = tvoc
        noxIndex = noxIndex
        noxRaw = nox
```

# Troubleshooting

* `AirGradient extension is inoperable` in the log: no source has
  `enable = true` in `[AirGradient]`.
* `No [LoopFields] entries ...` in the log: the mapping is empty; copy in
  the suggested mapping (see above).
* `Found no fresh reading to insert.`: the monitor has stopped answering
  (or is answering with insane readings).  Logged once per outage;
  `Fresh reading available again.` is logged on recovery.
* `airgradient reading from <host> not sane, ...`: the reason and the
  offending reading are included in the message.
* `Backfilled ... into archive record <time>`: an archive period WeeWX was
  not running for has had its air quality data filled in from a proxy's
  archive history.  Expect one line per record after an outage.
* `No proxy data with which to fill ... in archive record <time>`: no
  configured proxy could answer for that period, so those columns were left
  empty.  Logged once per archive record, which is also how a proxy that is
  down makes itself heard for as long as it stays down.
* **The columns are empty for a stretch of time.**  WeeWX was not running
  then, and the periods were filled only if a proxy could answer for them —
  see [Filling gaps after downtime](#filling-gaps-after-downtime).  With no
  `[[ProxyN]]` configured nothing is filled, and the log says nothing about
  it.  With one configured, look for `Backfilled ...` or `No proxy data with
  which to fill ...` at the time WeeWX restarted.
* To watch what the collector sees, run the module directly against a
  monitor:

  ```
  PYTHONPATH=<weewx-bin-dir> python bin/user/airgradient.py --test-collector --hostname <monitor> [--port <port>]
  ```

# Running the test suite

The tests are hermetic (no monitor or network required).  From a Python
environment with WeeWX installed:

```
PYTHONPATH=bin python -m pytest tests
```

## Licensing

weewx-airgradient is licensed under the GNU Public License v3.
