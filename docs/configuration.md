---
title: Configuring weewx-airgradient
description: The [AirGradient] section of weewx.conf — monitors, proxies, polling, source order, freshness, and the [[LoopFields]] mapping.
---

# Configuring weewx-airgradient

[Home](index.md) ·
[Installation](installation.md) ·
[Filling gaps after downtime](gaps.md) ·
[Fields in reports](fields.md) ·
[Troubleshooting](troubleshooting.md) ·
[GitHub project](https://github.com/chaunceygardiner/weewx-airgradient)

---

The install creates an `[AirGradient]` section in weewx.conf, with comments
explaining each option.  Point it at your monitor and fill in the mapping:

```
[AirGradient]
    poll_secs = 15
    enable_aqi = true
    [[LoopFields]]
        pm01            = pm1_0
        pm02Compensated = pm2_5
        pm10            = pm10_0
        rco2            = co2
        tvocIndex       = tvocIndex
        tvocRaw         = tvoc
        noxIndex        = noxIndex
        noxRaw          = nox
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

| Option       | Default                    | Meaning                                       |
|--------------|----------------------------|-----------------------------------------------|
| `poll_secs`  | 15                         | How often to poll for a new reading (seconds) |
| `enable_aqi` | true                       | Whether to register the AQI xtype             |
| `enable`     | false                      | Whether this source is polled                 |
| `hostname`   |                            | Hostname or IP address of the monitor/proxy   |
| `port`       | 80 (sensor) / 8080 (proxy) | Port to connect on                            |
| `timeout`    | 1 (proxy) / 10 (sensor)    | HTTP timeout (seconds).  A proxy answers from its own database on the local network, so a second is ample; a monitor's own processor is slow, and the installer writes 15 for one. |

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
