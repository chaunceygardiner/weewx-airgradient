---
title: weewx-airgradient fields in reports
description: Every AirGradient field you can map, using them in WeeWX reports — tags, aggregates, graphs — and how AQI is computed and (not) stored.
---

# weewx-airgradient fields in reports

[Home](index.md) ·
[Installation](installation.md) ·
[Configuration](configuration.md) ·
[Filling gaps after downtime](gaps.md) ·
[Troubleshooting](troubleshooting.md) ·
[GitHub project](https://github.com/chaunceygardiner/weewx-airgradient)

---

## Fields you can map

Any field the monitor reports can be mapped in
[`[[LoopFields]]`](configuration.md#the-loopfields-mapping).  The full list:

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

Temperature fields (`atmp`, `atmpCompensated`) are reported in Celsius and
converted to the unit system of the packet or record they are written into.
The four non-numeric fields — `serialno`, `ledMode`, `firmware` and `model`
— can be mapped, but cannot be filled in for a period WeeWX missed; see
[Filling gaps after downtime](gaps.md).

## Adding TVOC/NOx columns to the database (optional)

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

## Using the fields in reports

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
