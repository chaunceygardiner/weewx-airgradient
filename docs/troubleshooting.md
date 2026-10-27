---
title: Troubleshooting
layout: default
nav_order: 7
description: Log messages, the manual collector harness, and running the hermetic test suite.
---

# Troubleshooting weewx-airgradient

[weewx-airgradient manual](https://chaunceygardiner.github.io/weewx-airgradient/) · [weewx-airgradient on GitHub](https://github.com/chaunceygardiner/weewx-airgradient) · [Report an issue](https://github.com/chaunceygardiner/weewx-airgradient/issues)

---

## Log messages

* `AirGradient extension is inoperable`: no source has `enable = true` in
  `[AirGradient]`.
* `No [LoopFields] entries ...`: the mapping is empty; copy in the suggested
  mapping — see [Configuration](configuration.md#the-loopfields-mapping).
* `Found no fresh reading to insert.`: the monitor has stopped answering, or
  is answering with insane readings.  Logged once per outage; `Fresh reading
  available again after 13 min.` is logged on recovery, with how long the
  outage lasted.
* `airgradient reading from <host> not sane, ...`: the reason and the
  offending reading are included in the message.
* `Backfilled ... into archive record <time>`: an archive period WeeWX was
  not running for has had its air quality data filled in from a proxy's
  archive history.  Expect one line per record after an outage.  See
  [Filling gaps after downtime](gaps.md).
* `No proxy data with which to fill ... in archive record <time>`: no
  configured proxy could answer for that period, so those columns were left
  empty.  Logged once per archive record, which is also how a proxy that is
  down makes itself heard for as long as it stays down.
* `airgradient archive record from <host> could not be parsed, ...`: the
  proxy answered, but one of the records it sent could not be used.  That
  record is skipped; the rest of the period is unaffected.
* `airgradient-proxy at <host>:<port> serves unfiltered CO2; upgrade it to
  2.2 or later.`: that proxy predates 2.2 and does not take the median of
  its CO2 readings, so a single-reading spike reaches your loop packets and
  archive records.  weewx-airgradient passes the value through rather than
  filter it this far downstream.  Logged once per proxy each time WeeWX
  starts.  Upgrade the proxy.  See
  [CO2: the median of the last 3 readings](configuration.md#co2-the-median-of-the-last-3-readings).

## CO2 is missing for a short while

The median needs 3 readings from a monitor polled directly, none more than
`max(120, 3 * poll_secs)` seconds plus half a poll old (127.5 seconds at the
default).  Until there are 3, CO2 is left out of loop packets and every
other field arrives as usual.  That is about two polls after WeeWX starts,
and again whenever the monitor's last readings have aged past the limit:
after an outage of more than about 112 seconds at the default, or after an
airgradient-proxy has been answering for that long.  A 2.2 proxy leaves CO2
out for 60 to 90 seconds after it restarts.  See
[CO2: the median of the last 3 readings](configuration.md#co2-the-median-of-the-last-3-readings).

## The columns are empty for a stretch of time

WeeWX was not running then, and those periods are filled only if a proxy can
answer for them — see [Filling gaps after downtime](gaps.md).  With no
`[[ProxyN]]` configured nothing is filled, and the log says nothing about it.
With one configured, look for `Backfilled ...` or `No proxy data with which
to fill ...` at the time WeeWX restarted.

## The demo page is still in English after setting `lang`

Check that `lang = de` sits under the report's own section (`[StdReport]`
`[[AirGradientReport]]`) or under `[[Defaults]]`, and that the skin ships
that language — English,
German, French, Dutch and Spanish do; anything else falls back to English,
silently and per string.  The plot titles live in the PNGs, so they change
only as the images regenerate, and WeeWX redraws a plot only once it is
older than its own aggregation interval: the day plots on the next report
cycle, the week plots within an hour, the month plots within three hours,
the year plot within a day.  See
[Translating the demo page](i18n.md).

## Watching what the collector sees

Run the module directly against a monitor:

```
PYTHONPATH=<weewx-bin-dir> python bin/user/airgradient.py --test-collector --hostname <monitor> [--port <port>]
```

It prints each reading as the monitor reports it, so its `rco2` is the raw
value and can differ from the CO2 in loop packets, which is the median of
the last 3 readings.

## Running the test suite

The tests are hermetic — no monitor or network required.  From a Python
environment with WeeWX installed:

```
PYTHONPATH=bin python -m pytest tests
```
