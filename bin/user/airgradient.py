# Copyright 2025 by John A Kline <john@johnkline.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""
WeeWX module that records AirGradient air quality sensor readings.

AirGradient's local API is documented here:
https://github.com/airgradienthq/arduino/blob/master/docs/local-server.md
"""

import datetime
import json
import logging
import math
import requests
import sys
import threading
import time

from dateutil import tz
from dateutil.parser import parse
from dateutil.parser import ParserError

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Type

import weeutil.weeutil
import weewx
import weewx.units
import weewx.xtypes

from weewx.units import ValueTuple
from weeutil.weeutil import timestamp_to_string
from weeutil.weeutil import to_bool
from weeutil.weeutil import to_int
from weewx.engine import StdService

log = logging.getLogger(__name__)

WEEWX_AIRGRADIENT_VERSION = "1.0"

if sys.version_info[0] < 3 or (sys.version_info[0] == 3 and sys.version_info[1] < 9):
    raise weewx.UnsupportedFeature(
        "weewx-airgradient requires Python 3.9 or later, found %s.%s" % (sys.version_info[0], sys.version_info[1]))

if weewx.__version__ < "4":
    raise weewx.UnsupportedFeature(
        "weewx-airgradient requires WeeWX 4, found %s" % weewx.__version__)

# Set up observation types not in weewx.units

weewx.units.USUnits['air_quality_index']       = 'aqi'
weewx.units.MetricUnits['air_quality_index']   = 'aqi'
weewx.units.MetricWXUnits['air_quality_index'] = 'aqi'

weewx.units.USUnits['tvoc_index']       = 'tvoc_index'
weewx.units.MetricUnits['tvoc_index']   = 'tvoc_index'
weewx.units.MetricWXUnits['tvoc_index'] = 'tvoc_index'

weewx.units.USUnits['nox_index']       = 'nox_index'
weewx.units.MetricUnits['nox_index']   = 'nox_index'
weewx.units.MetricWXUnits['nox_index'] = 'nox_index'

weewx.units.USUnits['air_quality_color']       = 'aqi_color'
weewx.units.MetricUnits['air_quality_color']   = 'aqi_color'
weewx.units.MetricWXUnits['air_quality_color'] = 'aqi_color'

weewx.units.default_unit_label_dict['aqi']  = ' AQI'
weewx.units.default_unit_label_dict['aqi_color'] = ' RGB'
weewx.units.default_unit_label_dict['tvoc_index']  = ' TVOC Index'
weewx.units.default_unit_label_dict['nox_index']  = ' NOx Index'

weewx.units.default_unit_format_dict['aqi']  = '%d'
weewx.units.default_unit_format_dict['aqi_color'] = '%d'
weewx.units.default_unit_format_dict['tvoc_index'] = '%d'
weewx.units.default_unit_format_dict['nox_index'] = '%d'

weewx.units.obs_group_dict['pm2_5_aqi'] = 'air_quality_index'
weewx.units.obs_group_dict['pm2_5_aqi_color'] = 'air_quality_color'
weewx.units.obs_group_dict['tvocIndex'] = 'tvoc_index'
weewx.units.obs_group_dict['tvoc'] = 'group_concentration'
weewx.units.obs_group_dict['noxIndex'] = 'nox_index'
weewx.units.obs_group_dict['nox'] = 'group_concentration'

class Source:
    def __init__(self, config_dict, name, is_proxy):
        self.is_proxy = is_proxy
        # Raise KeyEror if name not in dictionary.
        source_dict = config_dict[name]
        self.enable = to_bool(source_dict.get('enable', False))
        self.hostname = source_dict.get('hostname', '')
        if is_proxy:
            self.port = to_int(source_dict.get('port', 8000))
        else:
            self.port = to_int(source_dict.get('port', 80))
        self.timeout  = to_int(source_dict.get('timeout', 10))

@dataclass
class Reading:
    measurementTime : datetime.datetime # time of reading
    serialno        : str             # Serial Number of the monitor
    wifi            : Optional[float] # WiFi signal strength
    pm01            : Optional[float] # PM1.0 in ug/m3 (atmospheric environment)
    pm02            : Optional[float] # PM2.5 in ug/m3 (atmospheric environment)
    pm10            : Optional[float] # PM10 in ug/m3 (atmospheric environment)
    pm02Compensated : Optional[float] # PM2.5 in ug/m3 with correction applied (from fw version 3.1.4 onwards)
    pm01Standard    : Optional[float] # PM1.0 in ug/m3 (standard particle)
    pm02Standard    : Optional[float] # PM2.5 in ug/m3 (standard particle)
    pm10Standard    : Optional[float] # PM10 in ug/m3 (standard particle)
    rco2            : Optional[float] # CO2 in ppm
    pm003Count      : Optional[float] # Particle count 0.3um per dL
    pm005Count      : Optional[float] # Particle count 0.5um per dL
    pm01Count       : Optional[float] # Particle count 1.0um per dL
    pm02Count       : Optional[float] # Particle count 2.5um per dL
    pm50Count       : Optional[float] # Particle count 5.0um per dL (only for indoor monitor)
    pm10Count       : Optional[float] # Particle count 10um per dL (only for indoor monitor)
    atmp            : Optional[float] # Temperature in Degrees Celsius
    atmpCompensated : Optional[float] # Temperature in Degrees Celsius with correction applied
    rhum            : Optional[float] # Relative Humidity
    rhumCompensated : Optional[float] # Relative Humidity with correction applied
    tvocIndex       : Optional[float] # Senisiron VOC Index
    tvocRaw         : Optional[float] # VOC raw value
    noxIndex        : Optional[float] # Senisirion NOx Index
    noxRaw          : Optional[float] # NOx raw value
    boot            : Optional[int  ] # Counts every measurement cycle. Low boot counts indicate restarts.
    bootCount       : Optional[int  ] # Same as boot property. Required for Home Assistant compatability. (deprecated soon!)
    ledMode         : Optional[str  ] # Current configuration of the LED mode
    firmware        : Optional[str  ] # Current firmware version
    model           : Optional[str  ] # Current model name

@dataclass
class Configuration:
    lock          : threading.Lock
    reading       : Optional[Reading] # Controlled by lock
    archive_delay : int               # Immutable
    poll_secs     : int               # Immutable
    fresh_secs    : int               # Immutable
    loop_fields   : Dict[str, str]    # Immutable
    sources       : List[Source]      # Immutable
    enable_aqi    : bool              # Immutable

def datetime_from_reading(dt_str):
    # 2025-10-25T17:45:00.000Z
    dt_str = dt_str.replace('Z', 'UTC')
    tzinfos = {'CST': tz.gettz("UTC")}
    return parse(dt_str, tzinfos=tzinfos)

def utc_now():
    return datetime.datetime.now(tz=tz.gettz("UTC"))

def get_reading(cfg: Configuration):
    for source in cfg.sources:
        if source.enable:
            reading = collect_data(source.hostname,
                                  source.port,
                                  source.timeout,
                                  source.is_proxy)
            if reading is not None:
                log.debug('get_reading: source: %s' % reading)
                age_of_reading = time.time() - reading.measurementTime.timestamp()
                # Ignore old readings.  We can't reading of fresh_secs or close to
                # it because the reading will age before the next time
                # a reading is polled.  Reduce fresh_secs - poll_secs by
                # 5s (as a buffer).
                if abs(age_of_reading) > (cfg.fresh_secs - cfg.poll_secs - 5.0):
                    log.info('Ignoring reading from %s:%d--age: %d seconds.' % (
                        source.hostname, source.port, age_of_reading))
                    continue
                log.debug('get_reading: reading: %s' % reading)
                return reading
    log.error('Could not get reading from any source.')
    return None

def check_type(j: Dict[str, Any], types: List[Type], names: List[str]) -> Tuple[bool, str]:
    try:
        for name in names:
            try:
                if name in j:
                    x = j[name]
                    if x is not None:
                        match_found = False
                        for t in types:
                            if isinstance(x, t):
                                match_found = True
                                break
                        if not match_found:
                            return False, '%s is not an instance of any of the following type(s): %r: %s' % (name, types, j[name])
            except KeyError:
                # All columns are optional
                pass
        return True, ''
    except Exception as e:
        return False, 'check_type: exception: %s' % e

def is_sane(j: Dict[str, Any]) -> Tuple[bool, Optional[str]]:

    ok, reason = check_type(j, [str], ['measurementTime', 'serialno','ledMode','firmware', 'model'])
    if not ok:
        return False, reason

    if 'measurementTime' in j:
        try:
            ok, reason = check_type(j, [str], ['measurementTime'])
            if not ok:
                return False, reason
            _ = datetime_from_reading(j['measurementTime'])
        except ParserError:
            return False, 'measurementTime could not be converted to a dateTime: %s' % j['measurementTime']

    ok, reason = check_type(j, [str], ['serialno','ledMode','firmware', 'model'])
    if not ok:
        return False, reason
 
    ok, reason = check_type(j, [int], ['boot', 'bootCount'])
    if not ok:
        return False, reason

    ok, reason = check_type(j, [float,int], [ 'wifi', 'pm01','pm02', 'pm10', 'pm02Compensated',
             'pm01Standard', 'pm02Standard', 'pm10Standard', 'rco2', 'pm003Count',
             'pm005Count', 'pm01Count', 'pm02Count', 'pm50Count', 'pm10Count',
             'atmp', 'atmpCompensated', 'rhum', 'rhumCompensated', 'tvocIndex',
             'tvocRaw', 'noxIndex', 'noxRaw'])
    if not ok:
        return False, reason

    return True, None

def parse_response(hostname: str, response: requests.Response) -> Optional[Reading]:
    try:
        # convert to json
        j: Dict[str, Any] = response.json()

        sane, reason = is_sane(j)
        if not sane:
            log.info('airgradient reading from %s not sane, %s: %s' % (hostname, reason, j))
            return None

        # if json contains 'measurementTime', the reading is from an airgradientproxy.
        # if missing, the reading is directly from an AirGradient sensor.  For the
        # latter case, the measurementTime is now.
        if 'measurementTime' in j:
            measurementTime = datetime_from_reading(j['measurementTime'])
        else:
            measurementTime = utc_now()

        return Reading(
            measurementTime = measurementTime,
            serialno        = j['serialno'],
            wifi            = float(j['wifi']) if 'wifi' in j else None,
            pm01            = float(j['pm01']) if 'pm01' in j else None,
            pm02            = float(j['pm02']) if 'pm02' in j else None,
            pm10            = float(j['pm10']) if 'pm10' in j else None,
            pm02Compensated = float(j['pm02Compensated']) if 'pm02Compensated' in j else None,
            pm01Standard    = float(j['pm01Standard']) if 'pm01Standard' in j else None,
            pm02Standard    = float(j['pm02Standard']) if 'pm02Standard' in j else None,
            pm10Standard    = float(j['pm10Standard']) if 'pm10Standard' in j else None,
            rco2            = float(j['rco2']) if 'rco2' in j else None,
            pm003Count      = float(j['pm003Count']) if 'pm003Count' in j else None,
            pm005Count      = float(j['pm005Count']) if 'pm005Count' in j else None,
            pm01Count       = float(j['pm01Count']) if 'pm01Count' in j else None,
            pm02Count       = float(j['pm02Count']) if 'pm02Count' in j else None,
            pm50Count       = float(j['pm50Count']) if 'pm50Count' in j else None,
            pm10Count       = float(j['pm10Count']) if 'pm10Count' in j else None,
            atmp            = float(j['atmp']) if 'atmp' in j else None,
            atmpCompensated = float(j['atmpCompensated']) if 'atmpCompensated' in j else None,
            rhum            = float(j['rhum']) if 'rhum' in j else None,
            rhumCompensated = float(j['rhumCompensated']) if 'rhumCompensated' in j else None,
            tvocIndex       = float(j['tvocIndex']) if 'tvocIndex' in j else None,
            tvocRaw         = float(j['tvocRaw']) if 'tvocRaw' in j else None,
            noxIndex        = float(j['noxIndex']) if 'noxIndex' in j else None,
            noxRaw          = float(j['noxRaw']) if 'noxRaw' in j else None,
            boot            = j['boot'] if 'boot' in j else None,
            bootCount       = j['bootCount'] if 'bootCount' in j else None,
            ledMode         = j['ledMode'] if 'ledMode' in j else None,
            firmware        = j['firmware'] if 'firmware' in j else None,
            model           = j['model'] if 'model' in j else None)
    except Exception as e:
        log.info('parse_response: %r raised exception %r' % (response.text, e))
        raise e

def collect_data(hostname, port, timeout, proxy = False) -> Optional[Reading]:

    reading: Optional[Reading] = None
    url = 'http://%s:%s/measures/current' % (hostname, port)

    try:
        # fetch data
        log.debug('collect_data: fetching from url: %s, timeout: %d' % (url, timeout))
        r = requests.get(url=url, timeout=timeout)
        r.raise_for_status()
        log.debug('collect_data: %s returned %r' % (hostname, r))
        if r:
            reading = parse_response(hostname, r)
    except Exception as e:
        log.info('collect_data: Attempt to fetch from: %s failed: %s.' % (hostname, e))
        reading = None

    return reading


class AirGradient(StdService):
    """Collect AirGradient Air air quality measurements."""

    def __init__(self, engine, config_dict):
        super(AirGradient, self).__init__(engine, config_dict)
        log.info("Service version is %s." % WEEWX_AIRGRADIENT_VERSION)

        self.engine = engine
        self.config_dict = config_dict.get('AirGradient', {})

        poll_secs  = to_int(self.config_dict.get('poll_secs', 15))
        fresh_secs = max(120, 3 * poll_secs)

        enable_aqi  = to_bool(self.config_dict.get('enable_aqi', True))

        self.cfg = Configuration(
            lock          = threading.Lock(),
            reading       = None,
            archive_delay = to_int(config_dict['StdArchive'].get('archive_delay', 15)),
            poll_secs     = poll_secs,
            fresh_secs    = fresh_secs,
            loop_fields   = AirGradient.configure_loop_fields(self.config_dict),
            sources       = AirGradient.configure_sources(self.config_dict),
            enable_aqi    = enable_aqi)

        log.info('poll_secs : %d' % self.cfg.poll_secs)
        log.info('fresh_secs: %d' % self.cfg.fresh_secs)
        log.info('enable_aqi: %s' % self.cfg.enable_aqi)
        loopfield_count: int = 0
        for key in self.cfg.loop_fields:
            loopfield_count += 1
            log.info('LoopField %d: %s = %s' % (loopfield_count, key, self.cfg.loop_fields[key]))
        source_count = 0
        for source in self.cfg.sources:
            if source.enable:
                source_count += 1
                log.info(
                    'Source %d for AirGradient readings: %s %s:%s, proxy: %s, timeout: %d' % (
                    source_count, 'airgradient-proxy' if source.is_proxy else 'sensor',
                    source.hostname, source.port, source.is_proxy, source.timeout))
        if source_count == 0:
            log.error('No sources configured for airgradient extension.  AirGradient extension is inoperable.')
        else:
            if self.cfg.enable_aqi:
                weewx.xtypes.xtypes.insert(0, AQI())

            with self.cfg.lock:
                self.cfg.reading = get_reading(self.cfg)

            # Start a thread to query proxies and make aqi available to loopdata
            dp: DevicePoller = DevicePoller(self.cfg)
            t: threading.Thread = threading.Thread(target=dp.poll_device)
            t.setName('AirGradient')
            t.setDaemon(True)
            t.start()

            self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)

    def new_loop_packet(self, event):
        log.debug('new_loop_packet(%s)' % event)
        with self.cfg.lock:
            log.debug('new_loop_packet: self.cfg.reading: %s' % self.cfg.reading)
            if self.cfg.reading is not None and \
                    self.cfg.reading.measurementTime.timestamp() + self.cfg.fresh_secs >= time.time():
                log.debug('Time of reading being inserted: %s' % timestamp_to_string(self.cfg.reading.measurementTime.timestamp()))

                reading_dict = self.cfg.reading.__dict__
                for rec_field in self.cfg.loop_fields.keys():
                    if rec_field in reading_dict and reading_dict[rec_field] is not None:
                        log.debug('packet[%s] = %r' % (self.cfg.loop_fields[rec_field], reading_dict[rec_field]))
                        if rec_field in ['atmp', 'atmpCompensated']:
                            # temperature needs to match units in loop record
                            temperature, _, _ = weewx.units.convertStd((reading_dict[rec_field], 'degree_C', 'group_temperature'), event.packet['usUnits'])
                            event.packet[self.cfg.loop_fields[rec_field]] = temperature
                        else:
                            # Don't have to worry about units convesion.
                            event.packet[self.cfg.loop_fields[rec_field]] = reading_dict[rec_field]
                if self.cfg.enable_aqi:
                    # compute aqi from pm02Compensated if present, else pm02
                    pm02: Optional[float] = None
                    if self.cfg.reading.pm02Compensated is not None:
                        pm02 = self.cfg.reading.pm02Compensated
                    elif self.cfg.reading.pm02 is not None:
                        pm02 = self.cfg.reading.pm02
                    if pm02 is not None:
                        event.packet['pm2_5_aqi'] = AQI.compute_pm2_5_aqi(pm02)
                        event.packet['pm2_5_aqi_color'] = AQI.compute_pm2_5_aqi_color(event.packet['pm2_5_aqi'])
            else:
                log.error('Found no fresh reading to insert.')

    def configure_loop_fields(config_dict):
        loop_fields = {}
        try:
            # Raise KeyEror if 'LoopFields' not in config_dict.
            loop_fields_dict = config_dict['LoopFields']
            for key in loop_fields_dict:
                if not isinstance(key, str):
                    log.info('keys in LoopFields must be strings that corresspond to AirGradient fields, skipping this entry: %s' % key)
                elif not isinstance(loop_fields_dict[key], str):
                    log.info('values in LoopFields must be strings that corresspond to Loop record fields, skipping this entry: %s' % key)
                else:
                    loop_fields[key] = loop_fields_dict[key]
        except KeyError:
            log.info("No LoopFields section in weewx.conf's AirGradient section, no fields will be written to Loop records")

        return loop_fields

    def configure_sources(config_dict):
        sources = []
        # Configure Proxies
        idx = 0
        while True:
            idx += 1
            try:
                source = Source(config_dict, 'Proxy%d' % idx, True)
                sources.append(source)
            except KeyError:
                break
        # Configure Sensors
        idx = 0
        while True:
            idx += 1
            try:
                source = Source(config_dict, 'Sensor%d' % idx, False)
                sources.append(source)
            except KeyError:
                break

        return sources

    def get_proxy_version(hostname, port, timeout):
        try:
            url = 'http://%s:%s/get-version' % (hostname, port)
            log.debug('get-proxy-version: url: %s' % url)
            # If the machine was just rebooted, a temporary failure in name
            # resolution is likely.  As such, try three times.
            for i in range(3):
                try:
                    r = requests.get(url=url, timeout=timeout)
                    r.raise_for_status()
                    break
                except requests.exceptions.ConnectionError as e:
                    if i < 2:
                        log.info('%s: Retrying.' % e)
                        time.sleep(5)
                    else:
                        raise e
            log.debug('get-proxy-version: r: %s' % r)
            if r is None:
                log.debug('get-proxy-version: request returned None')
                return None
            j = r.json()
            log.debug('get_proxy_version: returning version %s for %s.' % (j['version'], hostname))
            return j['version']
        except Exception as e:
            log.info('Could not get version from proxy %s: %s.  Down?' % (hostname, e))
            return None

    def get_earliest_timestamp(hostname, port, timeout):
        try:
            url = 'http://%s:%s/get-earliest-timestamp' % (hostname, port)
            r = requests.get(url=url, timeout=timeout)
            r.raise_for_status()
            log.debug('get-earliest-timestamp: r: %s' % r)
            if r is None:
                log.debug('get-earliest-timestamp: request returned None')
                return None
            j = r.json()
            log.debug('get_earliest_timestamp: returning earliest timestamp %s for %s.' % (j['timestamp'], hostname))
            return j['timestamp']
        except Exception as e:
            log.debug('Could not get earliest timestamp from proxy %s: %s.  Down?' % (hostname, e))
            return None

class DevicePoller:
    def __init__(self, cfg: Configuration):
        self.cfg = cfg

    def poll_device(self) -> None:
        log.debug('poll_device: start')
        while True:
            try:
                log.debug('poll_device: calling get_reading.')
                reading = get_reading(self.cfg)
            except Exception as e:
                log.error('poll_device exception: %s' % e)
                weeutil.logger.log_traceback(log.critical, "    ****  ")
                reading = None
            log.debug('poll_device: reading: %s' % reading)
            if reading is not None:
                with self.cfg.lock:
                    self.cfg.reading = reading
            log.debug('poll_device: Sleeping for %d seconds.' % self.cfg.poll_secs)
            time.sleep(self.cfg.poll_secs)

class AQI(weewx.xtypes.XType):
    """
    AQI XType which computes the AQI (air quality index) from
    the pm2_5 value.
    """

    def __init__(self):
        pass

    agg_sql_dict = {
        'avg': "SELECT AVG(pm2_5), MIN(usUnits) FROM %(table_name)s "
               "WHERE dateTime > %(start)s AND dateTime <= %(stop)s AND pm2_5 IS NOT NULL",
        'count': "SELECT COUNT(dateTime), MIN(usUnits) FROM %(table_name)s "
                 "WHERE dateTime > %(start)s AND dateTime <= %(stop)s AND pm2_5 IS NOT NULL",
        'first': "SELECT pm2_5, MIN(usUnits) FROM %(table_name)s "
                 "WHERE dateTime = (SELECT MIN(dateTime) FROM %(table_name)s "
                 "WHERE dateTime > %(start)s AND dateTime <= %(stop)s AND pm2_5 IS NOT NULL",
        'last': "SELECT pm2_5, MIN(usUnits) FROM %(table_name)s "
                "WHERE dateTime = (SELECT MAX(dateTime) FROM %(table_name)s "
                "WHERE dateTime > %(start)s AND dateTime <= %(stop)s AND pm2_5 IS NOT NULL",
        'min': "SELECT pm2_5, MIN(usUnits) FROM %(table_name)s "
               "WHERE dateTime > %(start)s AND dateTime <= %(stop)s AND pm2_5 IS NOT NULL "
               "ORDER BY pm2_5 ASC LIMIT 1;",
        'max': "SELECT pm2_5, MIN(usUnits) FROM %(table_name)s "
               "WHERE dateTime > %(start)s AND dateTime <= %(stop)s AND pm2_5 IS NOT NULL "
               "ORDER BY pm2_5 DESC LIMIT 1;",
        'sum': "SELECT SUM(pm2_5), MIN(usUnits) FROM %(table_name)s "
               "WHERE dateTime > %(start)s AND dateTime <= %(stop)s AND pm2_5 IS NOT NULL)",
    }

    day_boundary_avg_min_max_sql_dict = {
        'usUnits': "SELECT usUnits from %(table_name)s ORDER BY dateTime DESC LIMIT 1;",
        'avg'    : "SELECT sum(wsum) / sum(sumtime) FROM %(table_name)s%(pm2_5_summary_suffix)s "
                   "WHERE dateTime >= %(start)s AND dateTime < %(stop)s ",
        'min'    : "SELECT min FROM %(table_name)s%(pm2_5_summary_suffix)s "
                   "WHERE dateTime >= %(start)s AND dateTime < %(stop)s "
                   "ORDER BY min ASC LIMIT 1;",
        'max'    : "SELECT max FROM %(table_name)s%(pm2_5_summary_suffix)s "
                   "WHERE dateTime >= %(start)s AND dateTime < %(stop)s "
                   "ORDER BY max DESC LIMIT 1;",
    }

    @staticmethod
    def compute_pm2_5_aqi(pm2_5):
        #             U.S. EPA PM2.5 AQI
        #
        #  AQI Category  AQI Value  24-hr PM2.5
        # Good             0 -  50    0.0 -   9.0
        # Moderate        51 - 100    9.1 -  35.4
        # USG            101 - 150   35.5 -  55.4
        # Unhealthy      151 - 200   55.5 - 125.4
        # Very Unhealthy 201 - 300  125.5 - 225.4
        # Hazardous      301 - 500  225.5 and above

        # The EPA standard for AQI says to truncate PM2.5 to one decimal place.
        # See https://www3.epa.gov/airnow/aqi-technical-assistance-document-sept2018.pdf
        x = math.trunc(pm2_5 * 10) / 10

        if x <= 9.0: # Good
            return round(x / 9.0 * 50)
        elif x <= 35.4: # Moderate
            return round((x - 9.1) / 26.3 * 49.0 + 51.0)
        elif x <= 55.4: # Unhealthy for senstive
            return round((x - 35.5) / 19.9 * 49.0 + 101.0)
        elif x <= 125.4: # Unhealthy
            return round((x - 55.5) / 69.9 * 49.0 + 151.0)
        elif x <= 225.4: # Very Unhealthy
            return round((x - 125.5) / 99.9 * 99.0 + 201.0)
        else: # Hazardous
            return round((x - 225.5) / 199.9 * 199.0 + 301.0)

    @staticmethod
    def compute_pm2_5_aqi_color(pm2_5_aqi):
        if pm2_5_aqi <= 50:
            return 228 << 8                      # Green
        elif pm2_5_aqi <= 100:
            return (255 << 16) + (255 << 8)      # Yellow
        elif pm2_5_aqi <=  150:
            return (255 << 16) + (126 << 8)      # Orange
        elif pm2_5_aqi <= 200:
            return 255 << 16                     # Red
        elif pm2_5_aqi <= 300:
            return (143 << 16) + (63 << 8) + 151 # AirGradient
        else:
            return (126 << 16) + 35              # Maroon

    @staticmethod
    def get_scalar(obs_type, record, db_manager=None):
        log.debug('get_scalar(%s)' % obs_type)
        if obs_type not in [ 'pm2_5_aqi', 'pm2_5_aqi_color' ]:
            raise weewx.UnknownType(obs_type)
        log.debug('get_scalar(%s)' % obs_type)
        if record is None:
            log.debug('get_scalar called where record is None.')
            raise weewx.CannotCalculate(obs_type)
        if 'pm2_5' not in record:
            # Returning CannotCalculate causes exception in ImageGenerator, return UnknownType instead.
            # ERROR weewx.reportengine: Caught unrecoverable exception in generator 'weewx.imagegenerator.ImageGenerator'
            log.debug('get_scalar called where record does not contain pm2_5.')
            raise weewx.UnknownType(obs_type)
        if record['pm2_5'] is None:
            # Returning CannotCalculate causes exception in ImageGenerator, return UnknownType instead.
            # ERROR weewx.reportengine: Caught unrecoverable exception in generator 'weewx.imagegenerator.ImageGenerator'
            # This will happen for any catchup records inserted at weewx startup.
            log.debug('get_scalar called where record[pm2_5] is None.')
            raise weewx.UnknownType(obs_type)
        try:
            pm2_5 = record['pm2_5']
            if obs_type == 'pm2_5_aqi':
                value = AQI.compute_pm2_5_aqi(pm2_5)
            if obs_type == 'pm2_5_aqi_color':
                value = AQI.compute_pm2_5_aqi_color(AQI.compute_pm2_5_aqi(pm2_5))
            t, g = weewx.units.getStandardUnitType(record['usUnits'], obs_type)
            # Form the ValueTuple and return it:
            return weewx.units.ValueTuple(value, t, g)
        except KeyError:
            # Don't have everything we need. Raise an exception.
            raise weewx.CannotCalculate(obs_type)

    @staticmethod
    def get_series(obs_type, timespan, db_manager, aggregate_type=None, aggregate_interval=None):
        """Get a series, possibly with aggregation.
        """

        if obs_type not in [ 'pm2_5_aqi', 'pm2_5_aqi_color' ]:
            raise weewx.UnknownType(obs_type)

        log.debug('get_series(%s, %s, %s, aggregate:%s, aggregate_interval:%s)' % (
            obs_type, timestamp_to_string(timespan.start), timestamp_to_string(
            timespan.stop), aggregate_type, aggregate_interval))

        #  Prepare the lists that will hold the final results.
        start_vec = list()
        stop_vec = list()
        data_vec = list()

        # Is aggregation requested?
        if aggregate_type:
            # Yes. Just use the regular series function.
            return weewx.xtypes.ArchiveTable.get_series(obs_type, timespan, db_manager, aggregate_type,
                                           aggregate_interval)
        else:
            # No aggregation.
            sql_str = 'SELECT dateTime, usUnits, `interval`, pm2_5 FROM %s ' \
                      'WHERE dateTime >= ? AND dateTime <= ? AND pm2_5 IS NOT NULL' \
                      % db_manager.table_name
            std_unit_system = None

            for record in db_manager.genSql(sql_str, timespan):
                ts, unit_system, interval, pm2_5 = record
                if std_unit_system:
                    if std_unit_system != unit_system:
                        raise weewx.UnsupportedFeature(
                            "Unit type cannot change within a time interval.")
                else:
                    std_unit_system = unit_system

                if obs_type == 'pm2_5_aqi':
                    value = AQI.compute_pm2_5_aqi(pm2_5)
                if obs_type == 'pm2_5_aqi_color':
                    value = AQI.compute_pm2_5_aqi_color(AQI.compute_pm2_5_aqi(pm2_5))
                log.debug('get_series(%s): %s - %s - %s' % (obs_type,
                    timestamp_to_string(ts - interval * 60),
                    timestamp_to_string(ts), value))
                start_vec.append(ts - interval * 60)
                stop_vec.append(ts)
                data_vec.append(value)

            unit, unit_group = weewx.units.getStandardUnitType(std_unit_system, obs_type,
                                                               aggregate_type)

        return (ValueTuple(start_vec, 'unix_epoch', 'group_time'),
                ValueTuple(stop_vec, 'unix_epoch', 'group_time'),
                ValueTuple(data_vec, unit, unit_group))

    @staticmethod
    def get_aggregate(obs_type, timespan, aggregate_type, db_manager, **option_dict):
        """Returns an aggregation of pm2_5_aqi over a timespan by using the main archive
        table.

        obs_type: Must be 'pm2_5_aqi' or 'pm2_5_aqi_color'.

        timespan: An instance of weeutil.Timespan with the time period over which aggregation is to
        be done.

        aggregate_type: The type of aggregation to be done. For this function, must be 'avg',
        'sum', 'count', 'first', 'last', 'min', or 'max'. Anything else will cause
        weewx.UnknownAggregation to be raised.

        db_manager: An instance of weewx.manager.Manager or subclass.

        option_dict: Not used in this version.

        returns: A ValueTuple containing the result.
        """
        if obs_type not in [ 'pm2_5_aqi', 'pm2_5_aqi_color' ]:
            raise weewx.UnknownType(obs_type)

        log.debug('get_aggregate(%s, %s, %s, aggregate:%s)' % (
            obs_type, timestamp_to_string(timespan.start),
            timestamp_to_string(timespan.stop), aggregate_type))

        aggregate_type = aggregate_type.lower()

        # Raise exception if we don't know about this type of aggregation
        if aggregate_type not in list(AQI.agg_sql_dict.keys()):
            raise weewx.UnknownAggregation(aggregate_type)

        # Form the interpolation dictionary
        interpolation_dict = {
            'start': timespan.start,
            'stop': timespan.stop,
            'table_name': db_manager.table_name,
            'pm2_5_summary_suffix': '_day_pm2_5'
        }

        on_day_boundary = (timespan.stop - timespan.start) % (24 * 3600) == 0
        log.debug('day_boundary stop: %r start: %r delta: %r modulo: %d on_day_boundary: %s' % (timespan.stop , timespan.start, (timespan.stop - timespan.start), ((timespan.stop - timespan.start) % 3600), on_day_boundary))
        if aggregate_type in list(AQI.day_boundary_avg_min_max_sql_dict.keys()) and on_day_boundary:
            select_stmt = AQI.day_boundary_avg_min_max_sql_dict[aggregate_type] % interpolation_dict
            select_usunits_stmt = AQI.day_boundary_avg_min_max_sql_dict['usUnits'] % interpolation_dict
            need_usUnits = True
        else:
            select_stmt = AQI.agg_sql_dict[aggregate_type] % interpolation_dict
            need_usUnits = False
        if need_usUnits:
            row = db_manager.getSql(select_usunits_stmt)
            if row:
                std_unit_system, = row
            else:
                std_unit_system = None
        row = db_manager.getSql(select_stmt)
        if row:
            if need_usUnits:
                value, = row
            else:
                value, std_unit_system = row
        else:
            value = None
            std_unit_system = None

        if value is not None:
            if obs_type == 'pm2_5_aqi':
                value = AQI.compute_pm2_5_aqi(value)
            if obs_type == 'pm2_5_aqi_color':
                value = AQI.compute_pm2_5_aqi_color(AQI.compute_pm2_5_aqi(value))
        t, g = weewx.units.getStandardUnitType(std_unit_system, obs_type, aggregate_type)
        # Form the ValueTuple and return it:
        log.debug('get_aggregate(%s, %s, %s, aggregate:%s, select_stmt: %s, returning %s)' % (
            obs_type, timestamp_to_string(timespan.start), timestamp_to_string(timespan.stop),
            aggregate_type, select_stmt, value))
        return weewx.units.ValueTuple(value, t, g)

if __name__ == "__main__":
    usage = """%prog [options] [--help] [--debug]"""

    import weeutil.logger

    def main():
        import optparse

        parser = optparse.OptionParser(usage=usage)
        parser.add_option('--config', dest='cfgfn', type=str, metavar="FILE",
                          help="Use configuration file FILE. Default is /etc/weewx/weewx.conf or /home/weewx/weewx.conf")
        parser.add_option('--test-collector', dest='tc', action='store_true',
                          help='test the data collector')
        parser.add_option('--test-is-sane', dest='sane_test', action='store_true',
                          help='test the is_sane function')
        parser.add_option('--hostname', dest='hostname', action='store',
                          help='hostname to use with --test-collector')
        parser.add_option('--port', dest='port', action='store',
                          type=int, default=80,
                          help="port to use with --test-collector. Default is '80'")
        (options, args) = parser.parse_args()

        weeutil.logger.setup('airgradient', {})

        if options.tc:
            if not options.hostname:
                parser.error('--test-collector requires --hostname argument')
            test_collector(options.hostname, options.port)
        if options.sane_test:
            test_is_sane()

    def test_collector(hostname, port):
        while True:
            print(collect_data(hostname, port, 10))
            time.sleep(5)

    def test_is_sane():

        good_device = ('{'
                '"serialno": "ecda3b1eaaaf",'
                '"wifi": -65,'
                '"rco2": 730,'
                '"pm01": 2,'
                '"pm02": 3,'
                '"pm10": 4,'
                '"pm02Compensated": 3.5,'
                '"pm01Standard": 2.5,'
                '"pm02Standard": 4.5,'
                '"pm10Standard": 5.5,'
                '"pm003Count": 185,'
                '"pm005Count": 60,'
                '"pm01Count": 10,'
                '"pm02Count": 5,'
                '"pm50Count": 1,'
                '"pm10Count": 0,'
                '"atmp": 22.50,'
                '"atmpCompensated": 21.80,'
                '"rhum": 55,'
                '"rhumCompensated": 58,'
                '"tvocIndex": 100,'
                '"tvocRaw": 30000,'
                '"noxIndex": 1,'
                '"noxRaw": 16000,'
                '"boot": 7,'
                '"ledMode": "co2" }')

        good_proxy = ('{'
                '"measurementTime": "2025-10-25T17:45:00.000Z",'
                '"serialno": "ecda3b1eaaaf",'
                '"wifi": -65,'
                '"rco2": 730,'
                '"pm01": 2,'
                '"pm02": 3,'
                '"pm10": 4,'
                '"pm02Compensated": 3.5,'
                '"pm01Standard": 2.5,'
                '"pm02Standard": 4.5,'
                '"pm10Standard": 5.5,'
                '"pm003Count": 185,'
                '"pm005Count": 60,'
                '"pm01Count": 10,'
                '"pm02Count": 5,'
                '"pm50Count": 1,'
                '"pm10Count": 0,'
                '"atmp": 22.50,'
                '"atmpCompensated": 21.80,'
                '"rhum": 55,'
                '"rhumCompensated": 58,'
                '"tvocIndex": 100,'
                '"tvocRaw": 30000,'
                '"noxIndex": 1,'
                '"noxRaw": 16000,'
                '"boot": 7,'
                '"ledMode": "co2" }')
        bad_device = ('{'
                '"serialno": "ecda3b1eaaaf",'
                '"wifi": -65,'
                '"rco2": 730,'
                '"pm01": "nan",'
                '"pm02": "nan",'
                '"pm10": "nan",'
                '"pm02Compensated": 3.5,'
                '"pm01Standard": 2.5,'
                '"pm02Standard": 4.5,'
                '"pm10Standard": 5.5,'
                '"pm003Count": 185,'
                '"pm005Count": 60,'
                '"pm01Count": 10,'
                '"pm02Count": 5,'
                '"pm50Count": 1,'
                '"pm10Count": 0,'
                '"atmp": 22.50,'
                '"atmpCompensated": 21.80,'
                '"rhum": 55,'
                '"rhumCompensated": 58,'
                '"tvocIndex": 100,'
                '"tvocRaw": 30000,'
                '"noxIndex": 1,'
                '"noxRaw": 16000,'
                '"boot": 7,'
                '"ledMode": "co2" }')
        bad_proxy = ('{'
                '"measurementTime": "2025-10-25T17:45:00.000Z",'
                '"serialno": "ecda3b1eaaaf",'
                '"wifi": -65,'
                '"rco2": 730,'
                '"pm01": 2,'
                '"pm02": 3,'
                '"pm10": 4,'
                '"pm02Compensated": 3.5,'
                '"pm01Standard": 2.5,'
                '"pm02Standard": 4.5,'
                '"pm10Standard": 5.5,'
                '"pm003Count": 185,'
                '"pm005Count": 60,'
                '"pm01Count": 10,'
                '"pm02Count": 5,'
                '"pm50Count": 1,'
                '"pm10Count": 0,'
                '"atmp": 22.50,'
                '"atmpCompensated": 21.80,'
                '"rhum": 55,'
                '"rhumCompensated": "nan",'
                '"tvocIndex": 100,'
                '"tvocRaw": 30000,'
                '"noxIndex": 1,'
                '"noxRaw": 16000,'
                '"boot": 7,'
                '"ledMode": "co2" }')

        j = json.loads(good_proxy)
        sane, _ = is_sane(j)
        assert(sane)
        j = json.loads(good_device)
        sane, _ = is_sane(j)
        assert(sane)
        j = json.loads(bad_device)
        sane, reason = is_sane(j)
        assert(not sane)
        assert(reason == "pm01 is not an instance of any of the following type(s): [<class 'float'>, <class 'int'>]: nan")
        j = json.loads(bad_proxy)
        sane, reason = is_sane(j)
        assert(not sane)
        assert(reason == "rhumCompensated is not an instance of any of the following type(s): [<class 'float'>, <class 'int'>]: nan")
        print('passed')

    main()
