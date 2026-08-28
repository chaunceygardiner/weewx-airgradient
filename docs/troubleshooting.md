---
title: Troubleshooting
layout: default
nav_order: 6
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
  available again.` is logged on recovery.
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

## The columns are empty for a stretch of time

WeeWX was not running then, and those periods are filled only if a proxy can
answer for them — see [Filling gaps after downtime](gaps.md).  With no
`[[ProxyN]]` configured nothing is filled, and the log says nothing about it.
With one configured, look for `Backfilled ...` or `No proxy data with which
to fill ...` at the time WeeWX restarted.

## Watching what the collector sees

Run the module directly against a monitor:

```
PYTHONPATH=<weewx-bin-dir> python bin/user/airgradient.py --test-collector --hostname <monitor> [--port <port>]
```

## Running the test suite

The tests are hermetic — no monitor or network required.  From a Python
environment with WeeWX installed:

```
PYTHONPATH=bin python -m pytest tests
```
