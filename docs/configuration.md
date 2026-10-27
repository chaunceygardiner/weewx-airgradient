---
title: Configuration
layout: default
nav_order: 3
description: The [AirGradient] section of weewx.conf — monitors, proxies, polling, source order, freshness, and the [[LoopFields]] mapping.
---

# Configuring weewx-airgradient

[weewx-airgradient manual](https://chaunceygardiner.github.io/weewx-airgradient/) · [weewx-airgradient on GitHub](https://github.com/chaunceygardiner/weewx-airgradient) · [Report an issue](https://github.com/chaunceygardiner/weewx-airgradient/issues)

---

The install creates an `[AirGradient]` section in weewx.conf, with comments
explaining each option.  Point it at your monitor and fill in the mapping:

```
[AirGradient]
    # How often to poll the sensor/proxy, in seconds.
    #poll_secs = 15
    [[LoopFields]]
        # The install creates this section EMPTY -- these are suggestions
        # to paste in, not what you get.
        pm01            = pm1_0
        pm02Compensated = pm2_5
        pm10            = pm10_0
        rco2            = co2
        # These four have no column in the wview_extended schema.  Mapped
        # as they stand they are current values only -- no archive records,
        # aggregates or graphs.  See "Adding TVOC/NOx columns".
        tvocIndex       = tvocIndex
        tvocRaw         = tvoc
        noxIndex        = noxIndex
        noxRaw          = nox
    [[Sensor1]]
        enable = true
        # The port the monitor's own web server listens on
        #port = 80
        # http timeout (seconds)
        #timeout = 15
        # PLACEHOLDER -- replace with the host name or IP address of the
        # first monitor
        hostname = airgradient
    [[Sensor2]]
        enable = false
        #port = 80
        #timeout = 15
        hostname = airgradient2
    [[Proxy1]]
        enable = false
        #port = 8080
        #timeout = 1
        hostname = proxy1
```

| Option       | Default                    | Meaning                                       |
|--------------|----------------------------|-----------------------------------------------|
| `poll_secs`  | 15                         | How often to poll for a new reading (seconds) |
| `enable_aqi` | true                       | Whether to register the AQI xtype             |
| `enable`     | false                      | Whether this source is polled                 |
| `hostname`   |                            | Hostname or IP address of the monitor/proxy   |
| `port`       | 80 (sensor) / 8080 (proxy) | Port to connect on                            |
| `timeout`    | 1 (proxy) / 15 (sensor)    | HTTP timeout (seconds).  A proxy answers from its own database on the local network, so a second is ample; a monitor's own processor is slow and easily overwhelmed, so it gets more room. |

## Live options and commented-out ones

The options the install writes commented out are the ones weewx-airgradient
supplies for itself.  Leave one commented and the extension's own value
governs, including a better one a later release might bring; uncomment it to
pin this station to the value shown.

`hostname` is written live because there is nothing to fall back on — it is
the one you have to replace with your own, and its comment says PLACEHOLDER
so that the one line which breaks the extension if you ignore it says so
first.  `enable` is written live for a different reason: `Sensor1` ships
enabled so that a fresh install works with no proxy, and that is not what an
absent `enable` means.  Leave `enable` out of a section and that source is
simply off.

`enable_aqi` is not written into the section at all; set it to `false` (at
the `[AirGradient]` level, beside `poll_secs`) if another extension already
supplies `pm2_5_aqi`.

## Sources

AirGradient monitors are specified with subsections `[[Sensor1]]`,
`[[Sensor2]]`, etc.; airgradient-proxy services with `[[Proxy1]]`,
`[[Proxy2]]`, etc.  There is no limit on the number of monitors and
proxies, but the numbering of each group must start at 1 and be
consecutive — a gap ends the scan.

On each polling round, proxies are interrogated first (low numbers to
high), then sensors; the first source that yields a sane, fresh reading
wins and no further sources are tried.

A reading is considered fresh for `max(120, 3 * poll_secs)` seconds; stale
readings are never inserted into loop packets.

## CO2: the median of the last 3 readings

An AirGradient's CO2 now and then drops or jumps for a single reading, by
anything from a few ppm to a few hundred, and that one reading can become
the day's low or high.  So the CO2 written into loop packets is the median of
the last 3 readings rather than the latest one: a single bad reading is
out-voted by the two around it, whichever way it went.

* From a monitor polled directly (`[[SensorN]]`), weewx-airgradient takes the
  median.  All the `[[SensorN]]` sources share the same 3 readings, so a
  failover from one monitor to the next shows one poll late rather than
  leaving CO2 empty.
* Through an airgradient-proxy, the proxy takes the median and
  weewx-airgradient passes its CO2 through.  Only airgradient-proxy 2.2 and
  later do this.  An older proxy's CO2 arrives unfiltered, and
  weewx-airgradient logs once that the proxy should be upgraded — see
  [Troubleshooting](troubleshooting.md#log-messages).

The cost is lag: a real change in CO2 arrives about one `poll_secs` late (15
seconds at the default).  A real change that lasts a single reading, such as
a brief plume drifting past an outdoor monitor, is removed along with the
faults.

The median removes a fault only if the fault shows up in a single poll.  One
or two bad samples from the monitor's CO2 sensor spoil 12 to 16 seconds of
the CO2 it reports, so at the default `poll_secs` of 15 a fault almost
always lands in one poll.  Poll faster and it increasingly lands in two or more, and the
median lets it through: at `poll_secs = 10`, between a fifth and three fifths
of faults get through, and at 6 or less, every one.  Leave `poll_secs` at 15
or more for a monitor polled directly.

CO2 is also left out of loop packets until there are 3 readings to take the
median of.  A reading counts if it came from a monitor polled directly and
is no more than `max(120, 3 * poll_secs)` seconds plus half a poll old
(127.5 seconds at the default).  So CO2 is missing for about two polls after
WeeWX starts, and again whenever the monitor's last readings have aged past
that: after an outage of more than about 112 seconds at the default, or
after an airgradient-proxy has been answering for that long.  Every other
field arrives as usual meanwhile.  The median is always on; there is no
option.

## The [[LoopFields]] mapping

Each entry maps an AirGradient field (left side) to the loop-packet field
it should be written to (right side).  The installer creates the section
empty — copy in the suggested mapping shown above, or your own subset.
Without entries, no fields are written to loop packets, and the extension
logs an error at startup to that effect.

The section is deliberately not prefilled by the installer: on upgrade,
weectl merges installer defaults into your existing section, which would
inject unwanted entries into a customized mapping.

[Fields in reports](fields.md) lists every AirGradient field you can map.

`pm1_0`, `pm2_5`, `pm10_0` and `co2` are already columns in the
wview_extended schema, so mapping to them needs no database work.  `tvoc`,
`tvocIndex`, `nox` and `noxIndex` are not: mapped as they stand they are
current values only — `$current.tvoc` works, but there are no archive
records, no aggregates and no graphs, and nothing is logged to say so.  See
[Adding TVOC/NOx columns to the database](fields.md#adding-tvocnox-columns-to-the-database-optional).

Temperature fields (`atmp`, `atmpCompensated`) are reported in Celsius and
converted to the unit system of the packet they are written into.  All
fields are optional: AirGradient models differ in which ones they report,
and a field the monitor does not report is simply skipped.

## Running alongside a PurpleAir extension

If another extension (e.g.,
[weewx-purple](https://chaunceygardiner.github.io/weewx-purple/)) already
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

Two extensions must not map the same loop-packet field.  Whichever runs
later overwrites the other, and the gap filling described in
[Filling gaps after downtime](gaps.md) assumes this extension is the only
writer of the fields it maps.
