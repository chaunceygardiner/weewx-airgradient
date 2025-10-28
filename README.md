 weewx-airgradient
*Open source plugin for WeeWX software.

## Description

A WeeWX plugin that gets its AirGradient sensor readings either directly
from the AirGradient sensor or from a
[airgradient-proxy](https://github.com/chaunceygardiner/weewx-airgradient) service.

Copyright (C)2025 by John A Kline (john@johnkline.com)

**This plugin requires Python 3.9, WeeWX 5 and the
[wview_extended](https://github.com/weewx/weewx/blob/master/src/schemas/wview_extended.py)
schema.  The extension should install and run on WeeWX 4, but is not supported by the author.**

weewx-airgradient requires the
[wview_extended](https://github.com/weewx/weewx/blob/master/src/schemas/wview_extended.py)
in WeeWX 4 or 5 that contains `pm1_0`, `pm2_5` `pm10_0` and `o2` columns.  With the weewx-airgradient
extension, Loop records will be populated with `pm1_0`, `pm2_5`, `pm10_0` and `o2` fields that
correspond to AirGradient's `pm01`, `pm02` or `pm02Compensated`, `pm10` and `rco2` fields.

In addition to the above fields, weewx-airgradient inserts `tvocIndex`, `tvocRaw`, `noxIndex` and `noxRaw'.
These fields are accessible in the current loop record with the `.current` syntax.  If one wishes to
have these fields saved in archive records, one will need to add these columns to the schema.

Note: The AQI index values conform to the [2024 EPA definition](https://www.epa.gov/system/files/documents/2024-02/pm-naaqs-air-quality-index-fact-sheet.pdf)

If the AirGradient is configured to use a correction (available in the pm02Compensated field, it will be the value inserted into the loop rcords.  If not, pm02
will be the value inserted.

In addition to `pm1_0`, `pm2_5` `pm10_0` and `no2`, `tvocIndex`, `tvocRaw`, `noxIndex` and `noxRaw` are available (even if you havent added those columns to the database.  (Althouh, if not added, they are only available as `.current` values.

In addition, AQI variables are also available (even though they are not in the
database) via WeeWX 4's (or 5's) [XTypes](https://github.com/weewx/weewx/wiki/WeeWX-V4-user-defined-types).
pm2_5_aqi is automatically computed from pm2_5 and can be used in reports
(`$current.pm2_5_aqi`) and in graphs `[[[[pm2_5_aqi]]]`.  Also available is
is the [RGBint](https://www.shodor.org/stella2java/rgbint.html) value
`pm2_5_aqi_color` (useful for displaying AQI in the appropriate color
(e.g., green for AQIs <= 50).

A skin is provided to show a sample report:
![AirGradientReport](AirGradientReport.jpg)

### What's an airgradient-proxy?

airgradient-proxy is optional when using weewx-airgradient.  airgradient-proxy
returns an average over the archive period when queried.  Use of airgradient-proxy
is not recommended (and strongly discouraged for all but the most Unix/Linux
savvy).  The install is rather crude and has only been tested on Debian.
If in doubt, skip airgradient-proxy and query the AirGradient devices directly.

See `weewx-airgradient` and `airgradient-proxy` in action on the following pages:
* [Weatherboard&trade; Report](https://www.paloaltoweather.com/weatherboard/)
* [LiveSeasons Report](https://www.paloaltoweather.com/index.html).

# Installation Instructions

If you don't meet the following requirements, don't install this extension.
  * WeeWX 4 or 5
  * Using WeeWX 4's new wview_extended schema.
  * Python 3.7 or greater

## WeeWX 5 Installation Instructions

1. If pip install,
   Activate the virtual environment (actual syntax varies by type of WeeWX install):
   `/home/weewx/weewx-venv/bin/activate`
   Install the dateutil package.
   `pip install python-dateutil`
   Install the requests package.
   `pip install requests`

1. If package install:
   Install python3's dateutil package.  On debian, that can be accomplished with:
   `apt install python3-dateutil`
   Install python3's requests package.  On debian, that can be accomplished with:
   `apt install python3-requests`

1. Download the lastest release, weewx-airgradient.zip, from the
   [GitHub Repository](https://github.com/chaunceygardiner/weewx-airgradient).

1. Install the airgradient extension.

   `weectl extension install weewx-airgradient.zip`

1. Edit the `AirGradient` section of weewx.conf (which was created by the install
   above).  AirGradient sensors are specified with section names of `Sensor1`,
   `Sensor2`, `Sensor3`, etc.  Proxies are specified as `Proxy1`, `Proxy2`,
   `Proxy3`, etc.  There is no limit on how many sensors and proxies can
   be configured; but the numbering must be sonsecutive.  The order in which
   sensors/proxies are interrogated is first the proxies, low numbers to high;
   then the sensors, low numbers to high.  Once a proxy or sensor replies,
   no further proxies/sensors are interrogated for the current polling round.

   ```
   [AirGradient]
       poll_secs = 15
       [[Sensor]]
           enable = true
           hostname = airgradient
           port = 80
           timeout = 10
       [[Sensor2]]
           enable = false
           hostname = airgradient2
           port = 80
           timeout = 10
       [[Proxy1]]
           enable = false
           hostname = proxy
           port = 8080
           timeout = 10
           starup_timeout = 60
       [[Proxy2]]
           enable = false
           hostname = proxy2
           port = 8080
           timeout = 10
           starup_timeout = 60
       [[Proxy3]]
           enable = false
           hostname = proxy3
           port = 8080
           timeout = 10
           starup_timeout = 60
       [[Proxy4]]
           enable = false
           hostname = proxy4
           port = 8080
           timeout = 10
           starup_timeout = 60
   ```

1. Optionally, add the `tvocIndex`, `tvocRaw`, `noxIndex` and `noxRaw` columns to the weewx database.

   `sudo systemctl stop weewx`
   `/home/weewx/weewx-venv/bin/activate`
   `weectl database add-column tvocIndex --type=REAL`
   `weectl database add-column tvocRaw --type=REAL`
   `weectl database add-column noxIndex --type=REAL`
   `weectl database add-column noxRaw --type=REAL`
   `sudo systemctl start weewx`

1. If you are Unix/Linux savy, and are willing to work with a crude
   installation procedure, install
   [airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy).

1. Restart WeeWX

1. To check for a successful install, wait for a reporting cycle, then
   navigate in a browser to the WeeWX site and add /airgradient to the end
   of the URL (e.g., http://weewx-machine/weewx/airgradient).
   The PM2.5 and AQI graphs will fill in over time.

## WeeWX 4 Installation Instructions (installing in WeeWX 4 is not supported by the author)

1. Install python3's dateutil package.  On debian, that can be accomplished with:

   `apt install python3-dateutil`

1. Install python3's requests package.  On debian, that can be accomplished with:

   `apt install python3-requests`

1. Download the lastest release, weewx-airgradient.zip, from the
   [GitHub Repository](https://github.com/chaunceygardiner/weewx-airgradient).

1. Run the following command.

   `sudo /home/weewx/bin/wee_extension --install weewx-airgradient-3.3.zip`

   Note: this command assumes weewx is installed in /home/weewx.  If it's installed
   elsewhere, adjust the path of wee_extension accordingly.

1. Optionally, add the `tvocIndex`, `tvocRaw`, `noxIndex` and `noxRaw` columns to the
   weewx database as type REAL.  Details on how to do this for WeeWX version 4 are
   currently not provided in the README.

1. Edit the `AirGradient` section of weewx.conf (which was created by the install
   above).  AirGradient sensors are specified with section names of `Sensor1`,
   `Sensor2`, `Sensor3`, etc.  Proxies are specified as `Proxy1`, `Proxy2`,
   `Proxy3`, etc.  There is no limit on how many sensors and proxies can
   be configured; but the numbering must be sonsecutive.  The order in which
   sensors/proxies are interrogated is first the proxies, low numbers to high;
   then the sensors, low numbers to high.  Once a proxy or sensor replies,
   no further proxies/sensors are interrogated for the current polling round.

   ```
   [AirGradient]
       poll_secs = 15
       [[Sensor]]
           enable = true
           hostname = airgradient
           port = 80
           timeout = 10
       [[Sensor2]]
           enable = false
           hostname = airgradient2
           port = 80
           timeout = 10
       [[Proxy1]]
           enable = false
           hostname = proxy
           port = 8080
           timeout = 10
           starup_timeout = 60
       [[Proxy2]]
           enable = false
           hostname = proxy2
           port = 8080
           timeout = 10
           starup_timeout = 60
       [[Proxy3]]
           enable = false
           hostname = proxy3
           port = 8080
           timeout = 10
           starup_timeout = 60
       [[Proxy4]]
           enable = false
           hostname = proxy4
           port = 8080
           timeout = 10
           starup_timeout = 60
   ```

1. If you are Unix/Linux savvy, and are willing to work with a crude
   installation procedure, install
   [airgradient-proxy](https://github.com/chaunceygardiner/airgradient-proxy).

1. Restart WeeWX

1. To check for a successful install, wait for a reporting cycle, then
   navigate in a browser to the WeeWX site and add /airgradient to the end
   of the URL (e.g., http://weewx-machine/weewx/airgradient).
   The PM2.5 and AQI graphs will fill in over time.

# How to access weewx-airgradient fields in reports.

Detailed instructions are pending, below is a quick and dirty set of instructions.
At present, one will need to browse the code for more detail.

Note: Although the examples below show the use of $current, aggregates are also
supported (e.g., the high PM2.5 for the week can be presented with `$week.pm2_5.max`.

To show the PM2.5 reading, use the following:
```
$current.pm2_5
```

To show the Air Quality Index:
```
$current.pm2_5_aqi
```

To get the RGBINT color of the current Air Quality Index:
```
#set $color = int($current.pm2_5_aqi_color.raw)
#set $blue  =  $color & 255
#set $green = ($color >> 8) & 255
#set $red   = ($color >> 16) & 255
```

To show the PM1.0 reading, use the following:
```
$current.pm1_0
```

To show the PM10.0 reading, use the following:
```
$current.pm10_0
```

## Licensing

weewx-airgradient is licensed under the GNU Public License v3.
