---
title: Filling gaps after downtime
description: How weewx-airgradient fills the archive records WeeWX was not running for, using an airgradient-proxy's own archive history.
---

# Filling gaps after downtime

[Home](index.md) ·
[Installation](installation.md) ·
[Configuration](configuration.md) ·
[Fields in reports](fields.md) ·
[Troubleshooting](troubleshooting.md) ·
[GitHub project](https://github.com/chaunceygardiner/weewx-airgradient)

---

When WeeWX starts, the station's logger hands over the records it kept while
WeeWX was down — the *catchup* records.  They contain none of this
extension's fields, because nothing was there to supply them, and until 3.0
that hole was permanent.

If at least one `[[ProxyN]]` source is enabled, weewx-airgradient now fills
those records in.  For each one, the proxies are asked — in configured order
— for the archive records covering that period, and the average is written
into the record before WeeWX stores it.

Exactly the fields in `[[LoopFields]]` are filled, with exactly the values
the live path would have written: `pm2_5` is the compensated reading if that
is what the mapping asks for, and a temperature arrives in the record's own
unit system.  A backfilled `pm2_5` also restores `pm2_5_aqi` and
`pm2_5_aqi_color` for that period, since the AQI xtype computes them from
what is stored.

Non-numeric fields are not filled.  `[[LoopFields]]` can map `serialno`,
`ledMode`, `firmware` and `model`, and there is no average of those to write.

## Match the proxy's archive interval to WeeWX's

**Set airgradient-proxy's `archive-interval-secs` to WeeWX's archive
interval.**  WeeWX logs the interval it is using at startup (`Using archive
interval of 300 seconds`), and weewx-airgradient logs the same number
(`archive_interval: 300`).  With the two matched, each proxy record lines up
exactly with one WeeWX period.

A proxy that archives more often is handled — its records for the period are
averaged — but a proxy that archives *less* often than WeeWX has no record to
offer for most periods, and those go unfilled.

## What is never touched

Periods WeeWX did see are never touched: whatever WeeWX averaged from the
loop packets stands.  That includes a period it saw only part of — one loop
packet's worth of data is what WeeWX would have stored for that period
anyway.

The extension knows which periods it was feeding readings into, and fills
only the others.  The archive record itself cannot be asked: under hardware
record generation WeeWX grafts the accumulator's values onto the record
*after* every data service has seen it, so at that moment every field looks
missing whether the accumulator holds anything or not.

## The period that just closed

A proxy normally already holds the record for the period that has only just
closed: its polls are aligned to the clock, so one lands on the archive
boundary and the record is written a second or two later — before WeeWX
archives that period at all.

When no proxy has it — a proxy running with a `poll-freq-offset` can still be
a few seconds behind, and one that was down for the period has nothing — the
proxy's two minute average stands in, and then only if the two minutes it
covers reach into the period being filled.

Any period further back that no proxy can answer for is left alone.  An empty
column is the honest answer, and better than a value that describes some
other stretch of time.

## With no proxy configured

None of this happens.  A monitor queried directly keeps no history, so there
is nothing to ask for, and the columns for those periods stay empty.  The
handler is not even installed: no fetches, no log messages, nothing.

## What you will see in the log

One line per archive record:

```
INFO user.airgradient: Backfilled pm1_0, pm2_5, pm10_0, co2 into archive record 2026-08-26 18:40:00 PDT (1787794800).
INFO user.airgradient: No proxy data with which to fill pm1_0, pm2_5, pm10_0, co2 in archive record ...
```

The second is also how a proxy that is down announces itself, once per
archive period, for as long as it stays down.  A proxy that cannot be reached
is left alone for one archive interval rather than asked again for every
record of a catchup, so a long outage with a dead proxy costs one timeout,
not one per record.
