---
title: Home
layout: default
nav_order: 1
permalink: /
description: A WeeWX extension that reads an AirGradient monitor (or airgradient-proxy), inserts particulates, CO2, TVOC and NOx into every loop packet, serves AQI on demand as XTypes, and fills archive gaps from a proxy's history.
---

# weewx-airgradient

**AirGradient air quality for WeeWX** — particulates, CO2, TVOC and NOx in
every loop packet; AQI and its color computed on demand, never stored; and
the archive periods WeeWX was down for filled in from a proxy's history.

[View on GitHub](https://github.com/chaunceygardiner/weewx-airgradient){: .btn .btn-primary }

weewx-airgradient reads an [AirGradient](https://www.airgradient.com/) air
quality monitor on the local network (or an
[airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
service) and populates every WeeWX loop packet with whatever you map.  With
the suggested mapping that is:

| Loop field  | AirGradient field | Contents                                 |
|-------------|-------------------|------------------------------------------|
| `pm1_0`     | `pm01`            | PM1.0 concentration (µg/m³)              |
| `pm2_5`     | `pm02Compensated` | PM2.5 concentration (µg/m³), compensated |
| `pm10_0`    | `pm10`            | PM10 concentration (µg/m³)               |
| `co2`       | `rco2`            | CO2 (ppm)                                |
| `tvocIndex` | `tvocIndex`       | Sensirion TVOC index                     |
| `tvoc`      | `tvocRaw`         | TVOC raw value                           |
| `noxIndex`  | `noxIndex`        | Sensirion NOx index                      |
| `nox`       | `noxRaw`          | NOx raw value                            |

`pm1_0`, `pm2_5`, `pm10_0` and `co2` are in the wview_extended schema, so
WeeWX accumulates them into archive records — and into history graphs — with
no extra configuration.  The TVOC and NOx fields are not in the schema; they
are available in reports via `$current`, and
[Fields in reports](fields.md) shows how to add columns for them.

Two more observation types are available everywhere in reports and graphs
without being stored in the database, via WeeWX
[XTypes](https://github.com/weewx/weewx/wiki/WeeWX-V4-user-defined-types):

| Field             | Contents                                                         |
|-------------------|------------------------------------------------------------------|
| `pm2_5_aqi`       | US EPA Air Quality Index computed from `pm2_5` (2024 definition) |
| `pm2_5_aqi_color` | The RGB color of the AQI category, as a single integer            |

## Gaps are filled in

When WeeWX is not running — a restart, a reboot, a power cut — the station's
logger keeps recording, and WeeWX archives those records when it comes back.
They contain no air quality data, because this extension was not there to
supply any.  If an
[airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy)
is configured, those fields are fetched from the proxy's own archive history
and filled in, so an outage no longer leaves a hole in the columns and the
graphs that draw them.  See
[Filling gaps after downtime](gaps.md).

## The demo report

A small demo report is installed at `<HTML_ROOT>/airgradient`.  It is
translatable and ships German, French, Dutch and Spanish — see
[Translating the demo page](i18n.md).

![The demo page](https://raw.githubusercontent.com/chaunceygardiner/weewx-airgradient/master/AirGradientReport.png)

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
  without it.
