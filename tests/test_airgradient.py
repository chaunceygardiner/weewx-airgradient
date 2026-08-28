#
#    See the file LICENSE.txt for your full rights.
#
"""Hermetic tests for weewx-airgradient.  No network access: everything from
the fetch stack down is exercised with mocks, and the xtype SQL paths run
against an in-memory SQLite database."""

import datetime
import importlib
import importlib.util
import logging
import os
import re
import sqlite3
import threading
import time
import types
import unittest

from typing import Any, Dict
from unittest import mock

import configobj

import weeutil.logger
import weeutil.weeutil
import weewx
import weewx.accum
import weewx.units
import weewx.xtypes

import user.airgradient

from user.airgradient import AQI, AirGradient, Configuration, Reading, Source

log = logging.getLogger(__name__)

# Set up logging using the defaults.
weeutil.logger.setup('test_config', {})

# A class whose *name* matches weewxd's shutdown exception.  weewxd raises
# Terminate from its SIGTERM handler; airgradient.py recognizes it by name.
Terminate = type('Terminate', (Exception,), {})

# As reported by an AirGradient ONE (I-9PSL) queried directly.
VALID_PKT: Dict[str, Any] = {
    "pm01":0.67,
    "pm02":0.67,
    "pm10":0.67,
    "pm01Standard":0.67,
    "pm02Standard":0.67,
    "pm10Standard":0.67,
    "pm003Count":568.33,
    "pm005Count":383.33,
    "pm01Count":11,
    "pm02Count":0,
    "pm50Count":0,
    "pm10Count":0,
    "pm02Compensated":1.03,
    "atmp":21.91,
    "atmpCompensated":21.91,
    "rhum":58.86,
    "rhumCompensated":58.86,
    "rco2":514,
    "tvocIndex":75,
    "tvocRaw":32100.5,
    "noxIndex":1,
    "noxRaw":18138.67,
    "boot":0,
    "bootCount":0,
    "wifi":-72,
    "ledMode":"pm",
    "serialno":"d83bda1b9464",
    "firmware":"3.3.7",
    "model":"I-9PSL"}

def proxy_pkt(measurement_time='2027-10-27T18:58:17.000Z') -> Dict[str, Any]:
    """A copy of VALID_PKT as an airgradient-proxy would report it: with a
    measurementTime."""
    pkt = VALID_PKT.copy()
    pkt['measurementTime'] = measurement_time
    return pkt

# The mapping the README recommends for [LoopFields].
LOOP_FIELDS: Dict[str, str] = {
    'pm01'           : 'pm1_0',
    'pm02Compensated': 'pm2_5',
    'pm10'           : 'pm10_0',
    'rco2'           : 'co2',
    'tvocIndex'      : 'tvocIndex',
    'tvocRaw'        : 'tvoc',
    'noxIndex'       : 'noxIndex',
    'noxRaw'         : 'nox',
}

class FakeResponse:
    """Just enough of requests.Response for collect_data/parse_response."""
    def __init__(self, j, status_error=None):
        self._j = j
        self._status_error = status_error
        self.text = repr(j)
    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error
    def json(self):
        return self._j

class FakeDBManager:
    """Just enough of weewx.manager.Manager for the AQI xtype: a table name
    plus getSql/genSql running against a real SQLite connection."""
    def __init__(self, conn, table_name='archive'):
        self.conn = conn
        self.table_name = table_name
    def getSql(self, sql, sqlargs=()):
        return self.conn.execute(sql, sqlargs).fetchone()
    def genSql(self, sql, sqlargs=()):
        yield from self.conn.execute(sql, sqlargs)

def make_reading(age_secs=10.0, **overrides) -> Reading:
    kwargs = dict(
        measurementTime = datetime.datetime.now(tz=datetime.timezone.utc)
                          - datetime.timedelta(seconds=age_secs),
        serialno        = 'd83bda1b9464',
        wifi            = -72.0,
        pm01            = 0.67,
        pm02            = 0.67,
        pm10            = 0.67,
        pm02Compensated = 1.03,
        pm01Standard    = 0.67,
        pm02Standard    = 0.67,
        pm10Standard    = 0.67,
        rco2            = 514.0,
        pm003Count      = 568.33,
        pm005Count      = 383.33,
        pm01Count       = 11.0,
        pm02Count       = 0.0,
        pm50Count       = 0.0,
        pm10Count       = 0.0,
        atmp            = 21.91,
        atmpCompensated = 21.91,
        rhum            = 58.86,
        rhumCompensated = 58.86,
        tvocIndex       = 75.0,
        tvocRaw         = 32100.5,
        noxIndex        = 1.0,
        noxRaw          = 18138.67,
        boot            = 0,
        bootCount       = 0,
        ledMode         = 'pm',
        firmware        = '3.3.7',
        model           = 'I-9PSL')
    kwargs.update(overrides)
    return Reading(**kwargs)

def make_cfg(sources=None, poll_secs=15, fresh_secs=120, reading=None,
             loop_fields=None, enable_aqi=True):
    return Configuration(
        lock        = threading.Lock(),
        reading     = reading,
        poll_secs   = poll_secs,
        fresh_secs  = fresh_secs,
        loop_fields = loop_fields if loop_fields is not None else dict(LOOP_FIELDS),
        sources     = sources if sources is not None else [],
        enable_aqi  = enable_aqi)

def make_source(name='Sensor1', is_proxy=False, enable=True, hostname='host', **kwargs):
    d = {'enable': enable, 'hostname': hostname}
    d.update(kwargs)
    return Source({name: d}, name, is_proxy)

#             U.S. EPA PM2.5 AQI (May 2024 AirNow TAD)
#
#  AQI Category  AQI Value  24-hr PM2.5
# Good             0 -  50    0.0 -   9.0
# Moderate        51 - 100    9.1 -  35.4
# USG            101 - 150   35.5 -  55.4
# Unhealthy      151 - 200   55.5 - 125.4
# Very Unhealthy 201 - 300  125.5 - 225.4
# Hazardous      301 - 500  225.5 - 325.4
#
# Above 325.4, AQI values continue past 500 on the same (Hazardous) slope;
# there is no upper cap.

class TestComputeAqi(unittest.TestCase):

    def test_good(self):
        self.assertEqual(AQI.compute_pm2_5_aqi(0.0), 0)
        self.assertEqual(AQI.compute_pm2_5_aqi(6.0), 33)
        self.assertEqual(AQI.compute_pm2_5_aqi(9.0), 50)
        # 9.099 is truncated to 9.0
        self.assertEqual(AQI.compute_pm2_5_aqi(9.099), 50)

    def test_moderate(self):
        self.assertEqual(AQI.compute_pm2_5_aqi(9.1), 51)
        self.assertEqual(AQI.compute_pm2_5_aqi(21.8), 75)
        self.assertEqual(AQI.compute_pm2_5_aqi(35.4), 100)
        self.assertEqual(AQI.compute_pm2_5_aqi(35.499), 100)

    def test_usg(self):
        self.assertEqual(AQI.compute_pm2_5_aqi(35.5), 101)
        self.assertEqual(AQI.compute_pm2_5_aqi(45.4), 125)
        self.assertEqual(AQI.compute_pm2_5_aqi(55.4), 150)

    def test_unhealthy(self):
        self.assertEqual(AQI.compute_pm2_5_aqi(55.5), 151)
        self.assertEqual(AQI.compute_pm2_5_aqi(90.5), 176)
        self.assertEqual(AQI.compute_pm2_5_aqi(125.4), 200)

    def test_very_unhealthy(self):
        self.assertEqual(AQI.compute_pm2_5_aqi(125.5), 201)
        self.assertEqual(AQI.compute_pm2_5_aqi(175.4), 250)
        self.assertEqual(AQI.compute_pm2_5_aqi(225.4), 300)

    def test_hazardous(self):
        # Per the May 2024 AirNow TAD (breakpoint-table footnote 4), the
        # concentration for AQI 500 is 325.4: slope 199 AQI per 99.9 ug/m^3.
        self.assertEqual(AQI.compute_pm2_5_aqi(225.5), 301)
        self.assertEqual(AQI.compute_pm2_5_aqi(275.4), 400)
        self.assertEqual(AQI.compute_pm2_5_aqi(325.4), 500)

    def test_above_500_extrapolates_hazardous_slope(self):
        # The TAD FAQ: values above 500 are "based on the same linear slope
        # as the AQI values between 301 and 500".  No upper cap.
        self.assertEqual(AQI.compute_pm2_5_aqi(375.0), 599)
        self.assertEqual(AQI.compute_pm2_5_aqi(425.0), 698)
        self.assertEqual(AQI.compute_pm2_5_aqi(1000.0), 1844)

    def test_negative_concentration_maps_to_zero(self):
        # A (bogus) negative concentration must not map below 0.
        self.assertEqual(AQI.compute_pm2_5_aqi(-5.0), 0)

class TestComputeAqiColor(unittest.TestCase):

    GREEN       = 228 << 8
    YELLOW      = (255 << 16) + (255 << 8)
    ORANGE      = (255 << 16) + (126 << 8)
    RED         = 255 << 16
    PURPLE      = (143 << 16) + (63 << 8) + 151
    MAROON      = (126 << 16) + 35

    def test_category_boundaries(self):
        for aqi, expected in [
                (  0, self.GREEN),  ( 25, self.GREEN),  ( 50, self.GREEN),
                ( 51, self.YELLOW), ( 75, self.YELLOW), (100, self.YELLOW),
                (101, self.ORANGE), (125, self.ORANGE), (150, self.ORANGE),
                (151, self.RED),    (175, self.RED),    (200, self.RED),
                (201, self.PURPLE), (250, self.PURPLE), (300, self.PURPLE),
                (301, self.MAROON), (400, self.MAROON), (500, self.MAROON),
                # Above 500 is still Hazardous/Maroon.
                (501, self.MAROON), (750, self.MAROON)]:
            self.assertEqual(AQI.compute_pm2_5_aqi_color(aqi), expected,
                             'wrong color for AQI %d' % aqi)

class TestCheckType(unittest.TestCase):

    def test_matching_types(self):
        ok, _ = user.airgradient.check_type({'a': 1, 'b': 2.5}, [float, int], ['a', 'b'])
        self.assertTrue(ok)
        ok, _ = user.airgradient.check_type({'a': 'x'}, [str], ['a'])
        self.assertTrue(ok)

    def test_missing_field_acceptable(self):
        # All AirGradient fields are optional; models differ in what they report.
        ok, _ = user.airgradient.check_type({'a': 1}, [int], ['a', 'zz'])
        self.assertTrue(ok)

    def test_null_field_acceptable(self):
        ok, _ = user.airgradient.check_type({'a': None}, [int], ['a'])
        self.assertTrue(ok)

    def test_wrong_type(self):
        ok, reason = user.airgradient.check_type({'a': 'nan'}, [float, int], ['a'])
        self.assertFalse(ok)
        self.assertEqual(reason, "a is not an instance of any of the following "
                                 "type(s): [<class 'float'>, <class 'int'>]: nan")

    def test_bool_never_acceptable(self):
        # JSON true/false parse as bool, a subclass of int.
        ok, _ = user.airgradient.check_type({'a': True}, [int], ['a'])
        self.assertFalse(ok)
        ok, _ = user.airgradient.check_type({'a': False}, [float, int], ['a'])
        self.assertFalse(ok)

    def test_exception_swallowed(self):
        class Exploder:
            def get(self, key):
                raise RuntimeError('boom')
        ok, reason = user.airgradient.check_type(Exploder(), [int], ['x'])
        self.assertFalse(ok)
        self.assertIn('exception', reason)

class TestIsSane(unittest.TestCase):

    def test_valid_device_packet(self):
        ok, reason = user.airgradient.is_sane(VALID_PKT)
        self.assertTrue(ok, reason)

    def test_valid_proxy_packet(self):
        ok, reason = user.airgradient.is_sane(proxy_pkt())
        self.assertTrue(ok, reason)

    def test_sparse_packet_is_sane(self):
        # Models differ in which fields they report; a bare packet passes.
        ok, reason = user.airgradient.is_sane({'serialno': 'abc', 'pm02': 1.0})
        self.assertTrue(ok, reason)

    def test_bad_measurement_time(self):
        bad_pkt = proxy_pkt('xyz')
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertEqual(reason, 'measurementTime could not be converted to a dateTime: xyz')

    def test_non_string_measurement_time(self):
        bad_pkt = VALID_PKT.copy()
        bad_pkt['measurementTime'] = 1698346420
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertIn('measurementTime', reason)

    def test_null_measurement_time_tolerated(self):
        pkt = VALID_PKT.copy()
        pkt['measurementTime'] = None
        ok, reason = user.airgradient.is_sane(pkt)
        self.assertTrue(ok, reason)

    def test_bad_temp(self):
        bad_pkt = VALID_PKT.copy()
        bad_pkt['atmp'] = 'nan'
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertEqual(reason, "atmp is not an instance of any of the following "
                                 "type(s): [<class 'float'>, <class 'int'>]: nan")

    def test_bad_serialno(self):
        bad_pkt = VALID_PKT.copy()
        bad_pkt['serialno'] = 12345
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertIn('serialno', reason)

    def test_non_integer_boot(self):
        bad_pkt = VALID_PKT.copy()
        bad_pkt['boot'] = 1.5
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertIn('boot', reason)

    def test_bool_concentration_rejected(self):
        bad_pkt = VALID_PKT.copy()
        bad_pkt['pm02'] = True
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertIn('pm02', reason)

class TestDatetimeFromReading(unittest.TestCase):

    def test_utc_z_suffix(self):
        dt = user.airgradient.datetime_from_reading('2027-10-27T18:58:17.000Z')
        self.assertEqual(
            dt.timestamp(),
            datetime.datetime(2027, 10, 27, 18, 58, 17,
                              tzinfo=datetime.timezone.utc).timestamp())

class TestParseResponse(unittest.TestCase):

    def test_device_packet_stamped_now(self):
        # No measurementTime: the reading is direct from a sensor and is
        # stamped with the current UTC time.
        now = datetime.datetime(2027, 10, 27, 18, 58, 17, tzinfo=datetime.timezone.utc)
        with mock.patch('user.airgradient.utc_now', return_value=now):
            reading = user.airgradient.parse_response('sensor', FakeResponse(VALID_PKT.copy()))
        self.assertIsNotNone(reading)
        self.assertEqual(reading.measurementTime, now)
        self.assertEqual(reading.serialno, 'd83bda1b9464')
        self.assertEqual(reading.pm02Compensated, 1.03)
        self.assertEqual(reading.atmp, 21.91)
        self.assertEqual(reading.rco2, 514.0)
        self.assertEqual(reading.boot, 0)
        self.assertEqual(reading.ledMode, 'pm')
        self.assertEqual(reading.model, 'I-9PSL')

    def test_proxy_packet_keeps_measurement_time(self):
        reading = user.airgradient.parse_response('proxy', FakeResponse(proxy_pkt()))
        self.assertEqual(
            reading.measurementTime.timestamp(),
            datetime.datetime(2027, 10, 27, 18, 58, 17,
                              tzinfo=datetime.timezone.utc).timestamp())

    def test_whole_number_fields_become_floats(self):
        reading = user.airgradient.parse_response('sensor', FakeResponse(VALID_PKT.copy()))
        self.assertIsInstance(reading.rco2, float)
        self.assertIsInstance(reading.wifi, float)

    def test_missing_optional_fields_are_none(self):
        pkt = {'serialno': 'abc', 'pm02': 3.0}
        reading = user.airgradient.parse_response('sensor', FakeResponse(pkt))
        self.assertEqual(reading.pm02, 3.0)
        self.assertIsNone(reading.pm01)
        self.assertIsNone(reading.rco2)
        self.assertIsNone(reading.firmware)

    def test_null_fields_are_none(self):
        # A JSON null passes the sanity check and must not crash float().
        pkt = VALID_PKT.copy()
        pkt['rco2'] = None
        pkt['boot'] = None
        reading = user.airgradient.parse_response('sensor', FakeResponse(pkt))
        self.assertIsNotNone(reading)
        self.assertIsNone(reading.rco2)
        self.assertIsNone(reading.boot)

    def test_insane_packet_returns_none(self):
        pkt = VALID_PKT.copy()
        pkt['pm02'] = 'nan'
        self.assertIsNone(
            user.airgradient.parse_response('sensor', FakeResponse(pkt)))

    def test_missing_serialno_raises(self):
        pkt = VALID_PKT.copy()
        del pkt['serialno']
        with self.assertRaises(KeyError):
            user.airgradient.parse_response('sensor', FakeResponse(pkt))

class TestCollectData(unittest.TestCase):

    def test_successful_fetch(self):
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse(proxy_pkt())) as m:
            reading = user.airgradient.collect_data('proxy.example', 8080, 10, True)
        m.assert_called_once()
        self.assertIsNotNone(reading)
        self.assertEqual(reading.serialno, 'd83bda1b9464')
        self.assertEqual(
            m.call_args[1]['url'],
            'http://proxy.example:8080/measures/current')

    def test_insane_reading_returns_none(self):
        pkt = VALID_PKT.copy()
        pkt['pm02'] = 'nan'
        with mock.patch('user.airgradient.requests.get', return_value=FakeResponse(pkt)):
            self.assertIsNone(user.airgradient.collect_data('sensor.example', 80, 10))

    def test_unparseable_reading_returns_none(self):
        # A packet without serialno makes parse_response raise; collect_data
        # swallows it.
        pkt = VALID_PKT.copy()
        del pkt['serialno']
        with mock.patch('user.airgradient.requests.get', return_value=FakeResponse(pkt)):
            self.assertIsNone(user.airgradient.collect_data('sensor.example', 80, 10))

    def test_connection_error_returns_none(self):
        import requests
        with mock.patch('user.airgradient.requests.get',
                        side_effect=requests.exceptions.ConnectionError('no route')):
            self.assertIsNone(user.airgradient.collect_data('sensor.example', 80, 10))

    def test_http_error_returns_none(self):
        import requests
        resp = FakeResponse(None, status_error=requests.exceptions.HTTPError('500'))
        with mock.patch('user.airgradient.requests.get', return_value=resp):
            self.assertIsNone(user.airgradient.collect_data('sensor.example', 80, 10))

class TestTerminatePassThrough(unittest.TestCase):
    """weewxd stops by raising Terminate from its SIGTERM handler inside
    whatever the main thread is executing.  The broad exception handlers on
    main-thread paths must hand it back."""

    def test_reraise_if_terminate(self):
        with self.assertRaises(Terminate):
            user.airgradient.reraise_if_terminate(Terminate())
        # Any other exception is not re-raised.
        self.assertIsNone(user.airgradient.reraise_if_terminate(ValueError()))

    def test_collect_data_passes_terminate_through(self):
        with mock.patch('user.airgradient.requests.get', side_effect=Terminate()):
            with self.assertRaises(Terminate):
                user.airgradient.collect_data('sensor.example', 80, 10)

    def test_collect_data_swallows_other_exceptions(self):
        with mock.patch('user.airgradient.requests.get', side_effect=RuntimeError('boom')):
            self.assertIsNone(user.airgradient.collect_data('sensor.example', 80, 10))

    def test_check_type_passes_terminate_through(self):
        class Exploder:
            def get(self, key):
                raise Terminate()
        with self.assertRaises(Terminate):
            user.airgradient.check_type(Exploder(), [int], ['x'])

class TestConfigureSources(unittest.TestCase):

    def test_proxies_then_sensors_in_order(self):
        config = {
            'Sensor1': {'enable': True,  'hostname': 's1'},
            'Sensor2': {'enable': False, 'hostname': 's2'},
            'Proxy1':  {'enable': True,  'hostname': 'p1'},
        }
        sources = AirGradient.configure_sources(config)
        self.assertEqual([s.hostname for s in sources], ['p1', 's1', 's2'])
        self.assertTrue(sources[0].is_proxy)
        self.assertFalse(sources[1].is_proxy)

    def test_numbering_must_be_consecutive(self):
        config = {
            'Sensor1': {'enable': True, 'hostname': 's1'},
            'Sensor3': {'enable': True, 'hostname': 's3'},
        }
        sources = AirGradient.configure_sources(config)
        self.assertEqual([s.hostname for s in sources], ['s1'])

    def test_defaults(self):
        sensor = make_source('Sensor1', is_proxy=False)
        self.assertEqual(sensor.port, 80)
        self.assertEqual(sensor.timeout, 10)
        # airgradient-proxy's REST API listens on 8080 by default.
        proxy = make_source('Proxy1', is_proxy=True)
        self.assertEqual(proxy.port, 8080)
        # enable defaults to False, and parses strings.
        s = Source({'Sensor1': {'hostname': 'h'}}, 'Sensor1', False)
        self.assertFalse(s.enable)
        s = Source({'Sensor1': {'hostname': 'h', 'enable': 'true'}}, 'Sensor1', False)
        self.assertTrue(s.enable)

class TestConfigureLoopFields(unittest.TestCase):

    def test_mapping_parsed(self):
        config = {'LoopFields': dict(LOOP_FIELDS)}
        self.assertEqual(AirGradient.configure_loop_fields(config), LOOP_FIELDS)

    def test_missing_section_yields_empty_mapping(self):
        self.assertEqual(AirGradient.configure_loop_fields({}), {})

    def test_empty_mapping_logs_error(self):
        # An empty mapping means nothing is written to loop packets; that
        # must be loud, not silent.
        with self.assertLogs('user.airgradient', level='ERROR'):
            AirGradient.configure_loop_fields({})
        with self.assertLogs('user.airgradient', level='ERROR'):
            AirGradient.configure_loop_fields({'LoopFields': {}})

    def test_non_string_entries_skipped(self):
        config = {'LoopFields': {'pm01': 'pm1_0', 3: 'bad_key', 'rco2': 4}}
        self.assertEqual(AirGradient.configure_loop_fields(config), {'pm01': 'pm1_0'})

class TestGetReading(unittest.TestCase):

    def test_single_source(self):
        cfg = make_cfg(sources=[make_source()])
        reading = make_reading()
        with mock.patch('user.airgradient.collect_data', return_value=reading) as m:
            self.assertIs(user.airgradient.get_reading(cfg), reading)
        m.assert_called_once_with('host', 80, 10, False)

    def test_disabled_source_skipped(self):
        s1 = make_source('Sensor1', enable=False, hostname='s1')
        s2 = make_source('Sensor2', hostname='s2')
        cfg = make_cfg(sources=[s1, s2])
        with mock.patch('user.airgradient.collect_data',
                        return_value=make_reading()) as m:
            self.assertIsNotNone(user.airgradient.get_reading(cfg))
        m.assert_called_once()
        self.assertEqual(m.call_args[0][0], 's2')

    def test_failing_source_falls_through_to_next(self):
        s1 = make_source('Sensor1', hostname='s1')
        s2 = make_source('Sensor2', hostname='s2')
        cfg = make_cfg(sources=[s1, s2])
        with mock.patch('user.airgradient.collect_data',
                        side_effect=[None, make_reading()]) as m:
            self.assertIsNotNone(user.airgradient.get_reading(cfg))
        self.assertEqual(m.call_count, 2)

    def test_stale_source_falls_through_to_next(self):
        # With fresh_secs 120 and poll_secs 15, the cutoff is 100s.
        s1 = make_source('Sensor1', hostname='s1')
        s2 = make_source('Sensor2', hostname='s2')
        cfg = make_cfg(sources=[s1, s2])
        with mock.patch('user.airgradient.collect_data',
                        side_effect=[make_reading(age_secs=101),
                                     make_reading(age_secs=10)]) as m:
            reading = user.airgradient.get_reading(cfg)
        self.assertEqual(m.call_count, 2)
        self.assertIsNotNone(reading)

    def test_reading_within_cutoff_accepted(self):
        cfg = make_cfg(sources=[make_source()])
        with mock.patch('user.airgradient.collect_data',
                        return_value=make_reading(age_secs=99)):
            self.assertIsNotNone(user.airgradient.get_reading(cfg))

    def test_stale_reading_ignored(self):
        cfg = make_cfg(sources=[make_source()])
        with mock.patch('user.airgradient.collect_data',
                        return_value=make_reading(age_secs=101)):
            self.assertIsNone(user.airgradient.get_reading(cfg))

    def test_no_sources_respond(self):
        cfg = make_cfg(sources=[make_source()])
        with mock.patch('user.airgradient.collect_data', return_value=None):
            self.assertIsNone(user.airgradient.get_reading(cfg))

class TestNewLoopPacket(unittest.TestCase):

    @staticmethod
    def make_airgradient(reading, loop_fields=None, enable_aqi=True):
        # Build an AirGradient without running __init__ (which needs an
        # engine and does a synchronous fetch).
        ag = AirGradient.__new__(AirGradient)
        ag.cfg = make_cfg(reading=reading, loop_fields=loop_fields,
                          enable_aqi=enable_aqi)
        ag.stale_logged = False
        ag.archive_interval = 300
        ag.reading_times = []
        ag.reading_retention_secs = 600
        ag.proxy_retry_after = {}
        return ag

    @staticmethod
    def make_event(unit_system=weewx.US):
        return types.SimpleNamespace(packet={'usUnits': unit_system})

    def test_fields_inserted_per_loop_fields_mapping(self):
        ag = self.make_airgradient(make_reading())
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertEqual(event.packet['pm1_0'], 0.67)
        self.assertEqual(event.packet['pm2_5'], 1.03)   # pm02Compensated
        self.assertEqual(event.packet['pm10_0'], 0.67)
        self.assertEqual(event.packet['co2'], 514.0)
        self.assertEqual(event.packet['tvocIndex'], 75.0)
        self.assertEqual(event.packet['tvoc'], 32100.5)
        self.assertEqual(event.packet['noxIndex'], 1.0)
        self.assertEqual(event.packet['nox'], 18138.67)
        # Unmapped reading fields stay out of the packet.
        self.assertNotIn('rhum', event.packet)

    def test_aqi_computed_from_pm02_compensated(self):
        ag = self.make_airgradient(make_reading(pm02Compensated=35.4, pm02=9.0))
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertEqual(event.packet['pm2_5_aqi'], 100)
        self.assertEqual(event.packet['pm2_5_aqi_color'], TestComputeAqiColor.YELLOW)

    def test_aqi_falls_back_to_pm02(self):
        ag = self.make_airgradient(make_reading(pm02Compensated=None, pm02=9.0))
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertEqual(event.packet['pm2_5_aqi'], 50)

    def test_no_pm02_no_aqi(self):
        ag = self.make_airgradient(make_reading(pm02Compensated=None, pm02=None))
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertNotIn('pm2_5_aqi', event.packet)
        self.assertNotIn('pm2_5_aqi_color', event.packet)

    def test_enable_aqi_false_suppresses_aqi(self):
        ag = self.make_airgradient(make_reading(), enable_aqi=False)
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertNotIn('pm2_5_aqi', event.packet)
        self.assertIn('pm2_5', event.packet)

    def test_temperature_converted_to_packet_units(self):
        loop_fields = {'atmp': 'AGtemp', 'atmpCompensated': 'AGtempComp'}
        ag = self.make_airgradient(
            make_reading(atmp=20.0, atmpCompensated=21.0), loop_fields=loop_fields)
        event = self.make_event(weewx.US)
        ag.new_loop_packet(event)
        self.assertAlmostEqual(event.packet['AGtemp'], 68.0)      # degree_F
        self.assertAlmostEqual(event.packet['AGtempComp'], 69.8)

    def test_temperature_unconverted_in_metric_packet(self):
        ag = self.make_airgradient(
            make_reading(atmp=20.0), loop_fields={'atmp': 'AGtemp'})
        event = self.make_event(weewx.METRIC)
        ag.new_loop_packet(event)
        self.assertAlmostEqual(event.packet['AGtemp'], 20.0)      # degree_C

    def test_none_fields_skipped(self):
        ag = self.make_airgradient(make_reading(rco2=None))
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertNotIn('co2', event.packet)
        self.assertIn('pm1_0', event.packet)

    def test_empty_loop_fields_still_computes_aqi(self):
        ag = self.make_airgradient(make_reading(), loop_fields={})
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertNotIn('pm2_5', event.packet)
        self.assertIn('pm2_5_aqi', event.packet)

    def test_stale_reading_not_inserted(self):
        ag = self.make_airgradient(make_reading(age_secs=121))
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertEqual(event.packet, {'usUnits': weewx.US})

    def test_no_reading_not_inserted(self):
        ag = self.make_airgradient(None)
        event = self.make_event()
        ag.new_loop_packet(event)
        self.assertEqual(event.packet, {'usUnits': weewx.US})

    def test_stale_logged_once_per_outage(self):
        ag = self.make_airgradient(make_reading(age_secs=121))
        ag.new_loop_packet(self.make_event())
        self.assertTrue(ag.stale_logged)
        ag.new_loop_packet(self.make_event())
        self.assertTrue(ag.stale_logged)
        # Fresh data again: flag resets.
        with ag.cfg.lock:
            ag.cfg.reading = make_reading()
        ag.new_loop_packet(self.make_event())
        self.assertFalse(ag.stale_logged)

class TestAirGradientInit(unittest.TestCase):
    """Startup wiring: config parsing, xtype registration, poller launch.
    The engine is a mock and both the initial fetch and the poller thread
    are patched out, so nothing touches the network."""

    def test_startup_with_sources(self):
        engine = mock.Mock()
        engine.console.archive_interval = 300
        config = {
            'AirGradient': {
                'poll_secs': 50,
                'LoopFields': dict(LOOP_FIELDS),
                'Proxy1': {'enable': True, 'hostname': 'proxy1'},
                'Sensor1': {'enable': False, 'hostname': 'sensor1'},
            },
        }
        reading = make_reading()
        n_xtypes = len(weewx.xtypes.xtypes)
        orig_accum_maps = list(weewx.accum.accum_dict.maps)
        try:
            with mock.patch('user.airgradient.get_reading', return_value=reading) as gr, \
                 mock.patch('user.airgradient.threading.Thread') as thread_cls:
                ag = AirGradient(engine, config)
            # The synchronous startup fetch ran and its result is stored.
            gr.assert_called_once()
            self.assertIs(ag.cfg.reading, reading)
            self.assertEqual(ag.cfg.poll_secs, 50)
            self.assertEqual(ag.cfg.fresh_secs, 150)  # max(120, 3 * 50)
            self.assertEqual(len(ag.cfg.sources), 2)  # disabled sources still parsed
            self.assertEqual(ag.cfg.loop_fields, LOOP_FIELDS)
            # The AQI xtype is registered at the front of the list.
            self.assertEqual(len(weewx.xtypes.xtypes), n_xtypes + 1)
            self.assertIsInstance(weewx.xtypes.xtypes[0], AQI)
            # The noop accumulator extractors are registered, so the archive
            # record can't shadow the xtype.
            self.assertEqual(
                weewx.accum.accum_dict['pm2_5_aqi'], {'extractor': 'noop'})
            # The poller thread was created as a daemon and started.
            _, kwargs = thread_cls.call_args
            self.assertTrue(kwargs['daemon'])
            self.assertEqual(kwargs['name'], 'AirGradient')
            thread_cls.return_value.start.assert_called_once()
            # Bound to NEW_LOOP_PACKET, and -- because a proxy is enabled --
            # to NEW_ARCHIVE_RECORD for the archive backfill.
            self.assertEqual(engine.bind.call_args_list, [
                mock.call(weewx.NEW_LOOP_PACKET, ag.new_loop_packet),
                mock.call(weewx.NEW_ARCHIVE_RECORD, ag.new_archive_record)])
        finally:
            # Unregister anything this test added to the global xtypes list
            # and the global accumulator config.
            del weewx.xtypes.xtypes[0:len(weewx.xtypes.xtypes) - n_xtypes]
            weewx.accum.accum_dict.maps[:] = orig_accum_maps

    def test_startup_with_aqi_disabled(self):
        engine = mock.Mock()
        engine.console.archive_interval = 300
        config = {
            'AirGradient': {
                'enable_aqi': 'false',
                'Sensor1': {'enable': True, 'hostname': 'sensor1'},
            },
        }
        n_xtypes = len(weewx.xtypes.xtypes)
        with mock.patch('user.airgradient.get_reading', return_value=None), \
             mock.patch('user.airgradient.threading.Thread'):
            ag = AirGradient(engine, config)
        # No xtype registered, but the poller still runs and packets still
        # get loop fields.
        self.assertEqual(len(weewx.xtypes.xtypes), n_xtypes)
        self.assertFalse(ag.cfg.enable_aqi)
        engine.bind.assert_called_once()
        # No AQI in loop packets means no accumulator override either.
        self.assertNotIn('pm2_5_aqi', weewx.accum.accum_dict)

    def test_startup_without_sources_is_inoperable(self):
        engine = mock.Mock()
        engine.console.archive_interval = 300
        config = {'AirGradient': {'Sensor1': {'enable': False, 'hostname': 's'}}}
        n_xtypes = len(weewx.xtypes.xtypes)
        with mock.patch('user.airgradient.get_reading') as gr, \
             mock.patch('user.airgradient.threading.Thread') as thread_cls:
            ag = AirGradient(engine, config)
        # No fetch, no xtype, no poller, no binding -- but no crash either.
        gr.assert_not_called()
        thread_cls.assert_not_called()
        engine.bind.assert_not_called()
        self.assertEqual(len(weewx.xtypes.xtypes), n_xtypes)
        # Defaults were still parsed.
        self.assertEqual(ag.cfg.poll_secs, 15)
        self.assertEqual(ag.cfg.fresh_secs, 120)

class TestAccumulatorExtractors(unittest.TestCase):
    """The accumulator must not fold the loop-injected AQI fields into
    archive records: WeeWX's default avg extractor would average the
    already-rounded AQI integers (a meaningless quantity), and during
    real-time report generation $current uses the archive record directly,
    shadowing the AQI xtype.  extractor = noop drops the fields so lookups
    fall through to the xtype."""

    def setUp(self):
        self.orig_accum_maps = list(weewx.accum.accum_dict.maps)

    def tearDown(self):
        weewx.accum.accum_dict.maps[:] = self.orig_accum_maps

    def test_noop_extractor_registered_for_aqi_and_color(self):
        AQI.register_accumulator_extractors()
        for obs_type in ['pm2_5_aqi', 'pm2_5_aqi_color']:
            self.assertEqual(
                weewx.accum.accum_dict[obs_type]['extractor'], 'noop')

    def test_aqi_fields_dropped_from_extracted_record(self):
        AQI.register_accumulator_extractors()
        accum = weewx.accum.Accum(
            weeutil.weeutil.TimeSpan(1700000000, 1700000300))
        # Loop packets whose AQI toggles between 0 and 1: the default avg
        # extractor would put a bogus fractional AQI in the record.
        for i, (pm, aqi) in enumerate([(0.05, 0), (0.155, 1), (0.155, 1)]):
            accum.addRecord({
                'dateTime': 1700000100 + 15 * i,
                'usUnits': weewx.US,
                'pm2_5': pm,
                'pm2_5_aqi': aqi,
                'pm2_5_aqi_color': 128 * i,
            })
        record = accum.getRecord()
        # The concentration is extracted (averaged) as before...
        self.assertAlmostEqual(record['pm2_5'], (0.05 + 0.155 + 0.155) / 3)
        # ...but the AQI fields are dropped, leaving $current to the xtype.
        self.assertNotIn('pm2_5_aqi', record)
        self.assertNotIn('pm2_5_aqi_color', record)

    def test_user_accumulator_config_takes_precedence(self):
        AQI.register_accumulator_extractors()
        # weewx.accum.initialize() loads the user's [Accumulator] section in
        # front of everything else; a user override must win over ours.
        weewx.accum.initialize(
            {'Accumulator': {'pm2_5_aqi': {'extractor': 'avg'}}})
        self.assertEqual(
            weewx.accum.accum_dict['pm2_5_aqi']['extractor'], 'avg')
        # Types the user didn't override still get ours.
        self.assertEqual(
            weewx.accum.accum_dict['pm2_5_aqi_color']['extractor'], 'noop')

class TestGetScalar(unittest.TestCase):

    def test_aqi(self):
        record = {'dateTime': 1700000000, 'usUnits': weewx.US, 'pm2_5': 21.8}
        vt = AQI.get_scalar('pm2_5_aqi', record)
        self.assertEqual(vt.value, 75)
        self.assertEqual(vt.unit, 'aqi')
        self.assertEqual(vt.group, 'air_quality_index')

    def test_aqi_color(self):
        record = {'dateTime': 1700000000, 'usUnits': weewx.US, 'pm2_5': 21.8}
        vt = AQI.get_scalar('pm2_5_aqi_color', record)
        self.assertEqual(vt.value, TestComputeAqiColor.YELLOW)
        self.assertEqual(vt.unit, 'aqi_color')

    def test_unknown_type(self):
        with self.assertRaises(weewx.UnknownType):
            AQI.get_scalar('outTemp', {'pm2_5': 1.0})

    def test_no_record(self):
        with self.assertRaises(weewx.CannotCalculate):
            AQI.get_scalar('pm2_5_aqi', None)

    def test_record_without_pm2_5(self):
        with self.assertRaises(weewx.UnknownType):
            AQI.get_scalar('pm2_5_aqi', {'dateTime': 1700000000, 'usUnits': weewx.US})

    def test_record_with_null_pm2_5(self):
        # Catchup records inserted at startup have pm2_5 of None.
        with self.assertRaises(weewx.UnknownType):
            AQI.get_scalar('pm2_5_aqi',
                           {'dateTime': 1700000000, 'usUnits': weewx.US, 'pm2_5': None})

    def test_record_without_usunits(self):
        with self.assertRaises(weewx.CannotCalculate):
            AQI.get_scalar('pm2_5_aqi', {'dateTime': 1700000000, 'pm2_5': 21.8})

class TestGetSeries(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute(
            "CREATE TABLE archive (dateTime INTEGER PRIMARY KEY, usUnits INTEGER, "
            "`interval` INTEGER, pm2_5 REAL)")
        self.db_manager = FakeDBManager(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_unknown_type(self):
        with self.assertRaises(weewx.UnknownType):
            AQI.get_series('outTemp', weeutil.weeutil.TimeSpan(0, 4000), self.db_manager)

    def test_series_without_aggregation(self):
        rows = [(3600, weewx.US, 5, 9.0), (3900, weewx.US, 5, 35.4)]
        self.conn.executemany("INSERT INTO archive VALUES (?, ?, ?, ?)", rows)
        start_vt, stop_vt, data_vt = AQI.get_series(
            'pm2_5_aqi', weeutil.weeutil.TimeSpan(0, 4000), self.db_manager)
        self.assertEqual(start_vt.value, [3300, 3600])
        self.assertEqual(stop_vt.value, [3600, 3900])
        self.assertEqual(data_vt.value, [50, 100])
        self.assertEqual(data_vt.unit, 'aqi')
        self.assertEqual(data_vt.group, 'air_quality_index')

    def test_series_of_colors(self):
        self.conn.execute("INSERT INTO archive VALUES (?, ?, ?, ?)",
                          (3600, weewx.US, 5, 55.5))
        _, _, data_vt = AQI.get_series(
            'pm2_5_aqi_color', weeutil.weeutil.TimeSpan(0, 4000), self.db_manager)
        self.assertEqual(data_vt.value, [TestComputeAqiColor.RED])

    def test_mixed_unit_systems_rejected(self):
        rows = [(3600, weewx.US, 5, 9.0), (3900, weewx.METRIC, 5, 35.4)]
        self.conn.executemany("INSERT INTO archive VALUES (?, ?, ?, ?)", rows)
        with self.assertRaises(weewx.UnsupportedFeature):
            AQI.get_series('pm2_5_aqi', weeutil.weeutil.TimeSpan(0, 4000), self.db_manager)

    def test_aggregation_delegates_to_archive_table(self):
        sentinel = object()
        with mock.patch.object(weewx.xtypes.ArchiveTable, 'get_series',
                               return_value=sentinel) as m:
            result = AQI.get_series('pm2_5_aqi', weeutil.weeutil.TimeSpan(0, 4000),
                                    self.db_manager, 'avg', 3600)
        self.assertIs(result, sentinel)
        m.assert_called_once()

class TestGetAggregate(unittest.TestCase):
    """Runs the xtype's aggregation SQL against a real (SQLite) database:
    an archive table and a pm2_5 daily summary table."""

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute(
            "CREATE TABLE archive (dateTime INTEGER PRIMARY KEY, usUnits INTEGER, "
            "`interval` INTEGER, pm2_5 REAL)")
        self.conn.execute(
            "CREATE TABLE archive_day_pm2_5 (dateTime INTEGER PRIMARY KEY, "
            "min REAL, mintime INTEGER, max REAL, maxtime INTEGER, "
            "sum REAL, count INTEGER, wsum REAL, sumtime INTEGER)")
        self.db_manager = FakeDBManager(self.conn)

    def tearDown(self):
        self.conn.close()

    def insert_archive(self, rows):
        self.conn.executemany("INSERT INTO archive VALUES (?, ?, ?, ?)",
                              [(ts, weewx.US, 5, pm) for ts, pm in rows])

    def test_unknown_type(self):
        with self.assertRaises(weewx.UnknownType):
            AQI.get_aggregate('outTemp', weeutil.weeutil.TimeSpan(1000, 5000),
                              'avg', self.db_manager)

    def test_unknown_aggregation(self):
        for agg in ['sum', 'not_a_thing']:
            with self.assertRaises(weewx.UnknownAggregation):
                AQI.get_aggregate('pm2_5_aqi', weeutil.weeutil.TimeSpan(1000, 5000),
                                  agg, self.db_manager)

    def test_archive_table_aggregates(self):
        # A span NOT on day boundaries: every aggregate must run against
        # the archive table.  (Before v2.0 the first/last SQL was
        # syntactically invalid; this test executes every statement.)
        self.insert_archive([(2000, 9.0), (3000, 35.4), (4000, 55.4)])
        span = weeutil.weeutil.TimeSpan(1000, 5000)
        expectations = {
            'first': 50,   # pm2_5 9.0
            'last': 150,   # pm2_5 55.4
            'min': 50,
            'max': 150,
            'avg': 96,     # pm2_5 (9.0 + 35.4 + 55.4) / 3 = 33.26
        }
        for agg, expected in expectations.items():
            vt = AQI.get_aggregate('pm2_5_aqi', span, agg, self.db_manager)
            self.assertEqual(vt.value, expected, 'aggregate %s' % agg)
            self.assertEqual(vt.unit, 'aqi')

    def test_count_is_not_aqi_transformed(self):
        # Regression: count used to be run through the AQI computation.
        self.insert_archive([(2000, 9.0), (3000, 35.4), (4000, 55.4)])
        vt = AQI.get_aggregate('pm2_5_aqi', weeutil.weeutil.TimeSpan(1000, 5000),
                               'count', self.db_manager)
        self.assertEqual(vt.value, 3)

    def test_color_aggregate(self):
        self.insert_archive([(2000, 9.0), (3000, 55.4)])
        vt = AQI.get_aggregate('pm2_5_aqi_color', weeutil.weeutil.TimeSpan(1000, 5000),
                               'max', self.db_manager)
        self.assertEqual(vt.value, TestComputeAqiColor.ORANGE)

    def test_empty_span(self):
        vt = AQI.get_aggregate('pm2_5_aqi', weeutil.weeutil.TimeSpan(6000, 7000),
                               'min', self.db_manager)
        self.assertIsNone(vt.value)

    @staticmethod
    def local_midnight(year, month, day):
        return int(time.mktime(
            datetime.datetime(year, month, day).timetuple()))

    def populate_day_summaries(self, with_archive=True):
        day1 = self.local_midnight(2026, 1, 5)
        day2 = self.local_midnight(2026, 1, 6)
        day3 = self.local_midnight(2026, 1, 7)
        # day1: avg 10, min 5, max 25.  day2: avg 30, min 15, max 35.
        self.conn.execute(
            "INSERT INTO archive_day_pm2_5 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (day1, 5.0, day1 + 60, 25.0, day1 + 120, 1000.0, 100, 1000.0, 100))
        self.conn.execute(
            "INSERT INTO archive_day_pm2_5 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (day2, 15.0, day2 + 60, 35.0, day2 + 120, 3000.0, 100, 3000.0, 100))
        if with_archive:
            # The day-boundary path reads usUnits from the archive table.
            self.insert_archive([(day1 + 300, 10.0)])
        return day1, day3

    def test_day_boundary_fast_path(self):
        day1, day3 = self.populate_day_summaries()
        span = weeutil.weeutil.TimeSpan(day1, day3)
        # Overall: avg (1000+3000)/(100+100) = 20, min 5, max 35.
        self.assertEqual(
            AQI.get_aggregate('pm2_5_aqi', span, 'avg', self.db_manager).value,
            AQI.compute_pm2_5_aqi(20.0))
        self.assertEqual(
            AQI.get_aggregate('pm2_5_aqi', span, 'min', self.db_manager).value,
            AQI.compute_pm2_5_aqi(5.0))
        self.assertEqual(
            AQI.get_aggregate('pm2_5_aqi', span, 'max', self.db_manager).value,
            AQI.compute_pm2_5_aqi(35.0))

    def test_day_boundary_with_empty_archive_table(self):
        # Day summaries but no archive rows: the usUnits lookup finds no
        # row.  The value still computes; the unit system is unknown.
        day1, day3 = self.populate_day_summaries(with_archive=False)
        vt = AQI.get_aggregate('pm2_5_aqi', weeutil.weeutil.TimeSpan(day1, day3),
                               'avg', self.db_manager)
        self.assertEqual(vt.value, AQI.compute_pm2_5_aqi(20.0))
        self.assertIsNone(vt.unit)

    def test_trailing_24h_window_uses_archive_table(self):
        # Regression: a span whose length is a multiple of 24 hours but
        # which does NOT start at midnight used to be routed to the daily
        # summary table, silently including data outside the span.
        day1, _ = self.populate_day_summaries()
        start = day1 + 3600
        stop = start + 24 * 3600
        self.insert_archive([(start + 300, 9.0), (start + 600, 35.4)])
        vt = AQI.get_aggregate('pm2_5_aqi', weeutil.weeutil.TimeSpan(start, stop),
                               'avg', self.db_manager)
        # Average of the archive rows within the span, (9.0 + 35.4) / 2 = 22.2;
        # the daily summaries (which would give 10.0) must not be consulted.
        self.assertEqual(vt.value, AQI.compute_pm2_5_aqi(22.2))

class TestLoopValues(unittest.TestCase):
    """The one place a Reading becomes weewx fields.  Both the loop path and
    the archive backfill go through it, which is what makes a backfilled
    record carry the same quantity the loop path stores."""

    def test_fields_mapped_and_named(self):
        values = user.airgradient.loop_values(make_reading(), weewx.US, dict(LOOP_FIELDS))
        self.assertEqual(values['pm1_0'], 0.67)
        self.assertEqual(values['pm2_5'], 1.03)   # pm02Compensated
        self.assertEqual(values['co2'], 514.0)
        self.assertEqual(values['nox'], 18138.67)

    def test_temperature_converted_to_us(self):
        values = user.airgradient.loop_values(
            make_reading(), weewx.US, {'atmp': 'purple_temperature'})
        self.assertAlmostEqual(values['purple_temperature'], 71.438, places=3)

    def test_temperature_unconverted_in_metric(self):
        values = user.airgradient.loop_values(
            make_reading(), weewx.METRIC, {'atmp': 'purple_temperature'})
        self.assertEqual(values['purple_temperature'], 21.91)

    def test_none_field_absent(self):
        values = user.airgradient.loop_values(
            make_reading(rco2=None), weewx.US, dict(LOOP_FIELDS))
        self.assertNotIn('co2', values)

    def test_unknown_airgradient_field_absent(self):
        values = user.airgradient.loop_values(
            make_reading(), weewx.US, {'no_such_field': 'whatever'})
        self.assertEqual(values, {})

class TestReadingTimeTally(unittest.TestCase):
    """When the extension had a fresh reading to insert -- the only evidence
    that tells a period the accumulator has samples for from one it does not.

    Recorded per PACKET, not per field: which fields a reading carries is a
    property of the monitor (an Open Air reports no CO2, an SGP41-less unit
    no NOx), and a field the monitor never reports is not a gap a proxy
    polling that same monitor could fill."""

    @staticmethod
    def make_airgradient(loop_fields=None, retention_secs=600):
        ag = AirGradient.__new__(AirGradient)
        ag.cfg = make_cfg(reading=None, loop_fields=loop_fields)
        ag.stale_logged = False
        ag.archive_interval = 300
        ag.reading_times = []
        ag.reading_retention_secs = retention_secs
        ag.proxy_retry_after = {}
        return ag

    def test_packet_time_is_tallied(self):
        ag = self.make_airgradient()
        ag.record_reading_time({'dateTime': 1000.0})
        self.assertEqual(ag.reading_times, [1000.0])

    def test_window_is_exclusive_at_the_start_and_inclusive_at_the_end(self):
        ag = self.make_airgradient()
        ag.record_reading_time({'dateTime': 1000.0})
        self.assertTrue(ag.saw_reading_in(700.0, 1000.0))   # inclusive end
        self.assertFalse(ag.saw_reading_in(1000.0, 1300.0)) # exclusive start
        self.assertFalse(ag.saw_reading_in(1001.0, 1300.0))

    def test_period_with_no_reading_was_not_seen(self):
        ag = self.make_airgradient()
        ag.record_reading_time({'dateTime': 1000.0})
        self.assertFalse(ag.saw_reading_in(1300.0, 1600.0))

    def test_old_entries_are_pruned(self):
        ag = self.make_airgradient(retention_secs=600)
        for ts in [1000.0, 1300.0, 1600.0, 1900.0]:
            ag.record_reading_time({'dateTime': ts})
        # Retention is two archive intervals back from the newest packet.
        self.assertEqual(ag.reading_times, [1300.0, 1600.0, 1900.0])

    def test_packet_without_datetime_uses_now(self):
        ag = self.make_airgradient()
        before = time.time()
        ag.record_reading_time({})
        self.assertGreaterEqual(ag.reading_times[0], before)

    def test_packet_with_a_null_datetime_uses_now(self):
        # to_float(None) is None, and the cutoff arithmetic would then raise
        # inside new_loop_packet -- which has no handler, so it would escape
        # into dispatchEvent and stop weewxd.
        ag = self.make_airgradient()
        before = time.time()
        ag.record_reading_time({'dateTime': None})
        self.assertGreaterEqual(ag.reading_times[0], before)

    def test_a_fresh_reading_counts_even_when_it_carries_none_of_the_mapped_fields(self):
        # The monitor reporting nothing this install maps is not the same as
        # this extension being absent, and a proxy polling that same monitor
        # has nothing to offer either.
        ag = self.make_airgradient(loop_fields={'rco2': 'co2'})
        ag.cfg.reading = make_reading(rco2=None)
        event = types.SimpleNamespace(packet={'usUnits': weewx.US, 'dateTime': 1000.0})
        ag.new_loop_packet(event)
        self.assertNotIn('co2', event.packet)
        self.assertTrue(ag.saw_reading_in(700.0, 1000.0))

    def test_a_stale_reading_is_not_tallied(self):
        # Nothing was inserted, so the accumulator got nothing -- this is a
        # period a proxy genuinely may be able to answer for.
        ag = self.make_airgradient()
        ag.cfg.reading = make_reading(age_secs=10000.0)
        event = types.SimpleNamespace(packet={'usUnits': weewx.US, 'dateTime': 1000.0})
        ag.new_loop_packet(event)
        self.assertEqual(ag.reading_times, [])

class TestFetchProxyArchiveRecords(unittest.TestCase):
    """Asking an airgradient-proxy for the records covering one period."""

    def test_url_uses_comma_separated_args(self):
        # The proxy splits its args on ',', not '&' -- this is not a normal
        # query string.  since_ts is exclusive and max_ts inclusive, which is
        # exactly a WeeWX archive period.
        source = make_source('Proxy1', is_proxy=True, hostname='proxy1')
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([])) as get:
            user.airgradient.fetch_proxy_archive_records(source, 1000, 1300)
        self.assertEqual(
            get.call_args.kwargs['url'],
            'http://proxy1:8080/fetch-archive-records?since_ts=1000,max_ts=1300')
        self.assertEqual(get.call_args.kwargs['timeout'], 1)

    def test_records_parsed(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([proxy_pkt(), proxy_pkt()])):
            readings = user.airgradient.fetch_proxy_archive_records(source, 1000, 1300)
        self.assertEqual(len(readings), 2)
        self.assertEqual(readings[0].pm02Compensated, 1.03)

    def test_empty_period_is_an_empty_list_not_none(self):
        # An empty answer means the proxy has no record for the period -- a
        # different thing from a proxy that could not be reached.
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([])):
            self.assertEqual(
                user.airgradient.fetch_proxy_archive_records(source, 1000, 1300), [])

    def test_insane_record_skipped(self):
        source = make_source('Proxy1', is_proxy=True)
        bad = proxy_pkt()
        bad['atmp'] = 'hot'
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([bad, proxy_pkt()])):
            readings = user.airgradient.fetch_proxy_archive_records(source, 1000, 1300)
        self.assertEqual(len(readings), 1)

    def test_answer_that_is_not_a_list_returns_none(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse({'not': 'a list'})):
            self.assertIsNone(
                user.airgradient.fetch_proxy_archive_records(source, 1000, 1300))

    def test_entry_that_is_not_a_record_costs_only_that_entry(self):
        # Skipped like any other unusable record.  Returning None would tell
        # the caller the proxy is DOWN -- an archive interval of cooldown and
        # no two minute fallback -- for what is a parse problem.
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse(['nonsense', proxy_pkt()])):
            readings = user.airgradient.fetch_proxy_archive_records(source, 1000, 1300)
        self.assertEqual(len(readings), 1)

    def test_a_list_of_nothing_usable_is_empty_not_unreachable(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse(['nonsense', None])):
            self.assertEqual(
                user.airgradient.fetch_proxy_archive_records(source, 1000, 1300), [])

    def test_record_without_serialno_costs_only_that_record(self):
        # reading_from_json raises on it (is_sane treats serialno as optional
        # and the proxy omits the field when it is NULL, so a proxy can emit
        # such a record persistently).  Caught per entry: the rest of the
        # period survives, and the caller is never told the proxy is down.
        source = make_source('Proxy1', is_proxy=True)
        pkt = proxy_pkt()
        del pkt['serialno']
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([pkt, proxy_pkt()])):
            readings = user.airgradient.fetch_proxy_archive_records(source, 1000, 1300)
        self.assertEqual(len(readings), 1)

    def test_a_batch_of_only_bad_records_is_empty_not_unreachable(self):
        # An empty list means "no data for the period" and lets the two
        # minute fallback run; None would mean "unreachable" and would put
        # the proxy on an archive-interval cooldown.
        source = make_source('Proxy1', is_proxy=True)
        pkt = proxy_pkt()
        del pkt['serialno']
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([pkt])):
            self.assertEqual(
                user.airgradient.fetch_proxy_archive_records(source, 1000, 1300), [])

    def test_terminate_in_a_record_passes_through(self):
        # The per-entry handler is on the main thread too.
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([proxy_pkt()])), \
             mock.patch('user.airgradient.reading_from_json', side_effect=Terminate()):
            with self.assertRaises(Terminate):
                user.airgradient.fetch_proxy_archive_records(source, 1000, 1300)

    def test_unreachable_proxy_returns_none(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        side_effect=Exception('connection refused')):
            self.assertIsNone(
                user.airgradient.fetch_proxy_archive_records(source, 1000, 1300))

    def test_terminate_passes_through(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get', side_effect=Terminate()):
            with self.assertRaises(Terminate):
                user.airgradient.fetch_proxy_archive_records(source, 1000, 1300)

class TestFetchProxyTwoMinuteReading(unittest.TestCase):
    """The stand-in for a just-closed period no proxy has archived yet.  It is
    a SEPARATE endpoint from the /measures/current this extension polls:
    against a proxy that one answers with the single latest reading, and only
    /fetch-two-minute-record is an average."""

    def test_url_and_parse(self):
        source = make_source('Proxy1', is_proxy=True, hostname='proxy1')
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse(proxy_pkt())) as get:
            reading = user.airgradient.fetch_proxy_two_minute_reading(source)
        self.assertEqual(get.call_args.kwargs['url'],
                         'http://proxy1:8080/fetch-two-minute-record')
        self.assertEqual(reading.pm02Compensated, 1.03)

    def test_empty_object_returns_none(self):
        # The proxy answers {} when it holds no two minute record.  It passes
        # is_sane (every field is optional) but has no serialno to parse.
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse({})):
            self.assertIsNone(user.airgradient.fetch_proxy_two_minute_reading(source))

    def test_answer_that_is_not_an_object_returns_none(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse([proxy_pkt()])):
            self.assertIsNone(user.airgradient.fetch_proxy_two_minute_reading(source))

    def test_unreachable_proxy_returns_none(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        side_effect=Exception('connection refused')):
            self.assertIsNone(user.airgradient.fetch_proxy_two_minute_reading(source))

    def test_unparseable_record_is_reported_as_a_parse_problem(self):
        # Not as a failure to reach the proxy: the proxy answered, and the
        # record it answered with is what could not be used.
        source = make_source('Proxy1', is_proxy=True)
        pkt = proxy_pkt()
        del pkt['serialno']
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse(pkt)), \
             self.assertLogs('user.airgradient', level='WARNING') as logged:
            self.assertIsNone(user.airgradient.fetch_proxy_two_minute_reading(source))
        self.assertIn('could not be parsed', ''.join(logged.output))

    def test_terminate_passes_through(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get', side_effect=Terminate()):
            with self.assertRaises(Terminate):
                user.airgradient.fetch_proxy_two_minute_reading(source)

    def test_terminate_while_parsing_passes_through(self):
        source = make_source('Proxy1', is_proxy=True)
        with mock.patch('user.airgradient.requests.get',
                        return_value=FakeResponse(proxy_pkt())), \
             mock.patch('user.airgradient.reading_from_json', side_effect=Terminate()):
            with self.assertRaises(Terminate):
                user.airgradient.fetch_proxy_two_minute_reading(source)

class TestAverageReadings(unittest.TestCase):
    """Averaging the proxy records that cover one WeeWX archive period.  With
    both intervals at 300s there is exactly one; a proxy archiving more often
    than WeeWX does yields several."""

    def test_values_are_averaged(self):
        readings = [make_reading(rco2=400.0, pm02Compensated=2.0),
                    make_reading(rco2=600.0, pm02Compensated=4.0)]
        values = user.airgradient.average_readings(readings, weewx.US, dict(LOOP_FIELDS))
        self.assertEqual(values['co2'], 500.0)
        self.assertEqual(values['pm2_5'], 3.0)

    def test_single_reading_is_itself(self):
        values = user.airgradient.average_readings(
            [make_reading()], weewx.US, dict(LOOP_FIELDS))
        self.assertEqual(values['pm2_5'], 1.03)

    def test_field_missing_from_some_readings(self):
        # Averaged over the readings that carry it, not over all of them.
        readings = [make_reading(rco2=400.0), make_reading(rco2=None)]
        values = user.airgradient.average_readings(readings, weewx.US, dict(LOOP_FIELDS))
        self.assertEqual(values['co2'], 400.0)

    def test_non_numeric_fields_are_skipped(self):
        # [LoopFields] can map ledMode, firmware, model or serialno, and there
        # is no average of those.
        loop_fields = {'ledMode': 'led_mode', 'firmware': 'fw', 'rco2': 'co2'}
        values = user.airgradient.average_readings(
            [make_reading(), make_reading()], weewx.US, loop_fields)
        self.assertEqual(values, {'co2': 514.0})

    def test_temperature_converted_before_averaging(self):
        readings = [make_reading(atmp=0.0), make_reading(atmp=100.0)]
        values = user.airgradient.average_readings(
            readings, weewx.US, {'atmp': 'purple_temperature'})
        self.assertEqual(values['purple_temperature'], 122.0)  # (32 + 212) / 2

    def test_no_readings_yields_nothing(self):
        self.assertEqual(
            user.airgradient.average_readings([], weewx.US, dict(LOOP_FIELDS)), {})

class TestArchiveInterval(unittest.TestCase):
    """The interval WeeWX actually archives on -- the console's under hardware
    record generation, weewx.conf's under software.  It sets the tally
    retention, the proxy cooldown, and the window for a record that arrives
    without an interval of its own."""

    @staticmethod
    def build(engine, config):
        with mock.patch('user.airgradient.get_reading', return_value=make_reading()), \
             mock.patch('user.airgradient.threading.Thread'):
            return AirGradient(engine, config)

    @staticmethod
    def base_config(**std_archive):
        return {
            'StdArchive': std_archive,
            'AirGradient': {
                'LoopFields': dict(LOOP_FIELDS),
                'Sensor1': {'enable': True, 'hostname': 'sensor1'},
            },
        }

    def setUp(self):
        self.n_xtypes = len(weewx.xtypes.xtypes)
        self.orig_accum_maps = list(weewx.accum.accum_dict.maps)

    def tearDown(self):
        del weewx.xtypes.xtypes[0:len(weewx.xtypes.xtypes) - self.n_xtypes]
        weewx.accum.accum_dict.maps[:] = self.orig_accum_maps

    def test_hardware_generation_prefers_the_console(self):
        engine = mock.Mock()
        engine.console.archive_interval = 600
        ag = self.build(engine, self.base_config(archive_interval=300))
        self.assertEqual(ag.archive_interval, 600)
        self.assertEqual(ag.reading_retention_secs, 1200)

    def test_software_generation_ignores_the_console(self):
        engine = mock.Mock()
        engine.console.archive_interval = 1800
        ag = self.build(engine, self.base_config(
            record_generation='software', archive_interval=300))
        self.assertEqual(ag.archive_interval, 300)

    def test_console_of_none_falls_back_to_config(self):
        # A driver that answers None would otherwise stop weewx from starting.
        engine = mock.Mock()
        engine.console.archive_interval = None
        ag = self.build(engine, self.base_config(archive_interval=600))
        self.assertEqual(ag.archive_interval, 600)

    def test_driver_that_cannot_report_falls_back_to_config(self):
        engine = mock.Mock()
        type(engine.console).archive_interval = mock.PropertyMock(
            side_effect=NotImplementedError)
        try:
            ag = self.build(engine, self.base_config(archive_interval=600))
            self.assertEqual(ag.archive_interval, 600)
        finally:
            del type(engine.console).archive_interval

    def test_default_is_five_minutes(self):
        engine = mock.Mock()
        engine.console.archive_interval = None
        ag = self.build(engine, self.base_config())
        self.assertEqual(ag.archive_interval, 300)

class TestNewArchiveRecord(unittest.TestCase):
    """Filling in the periods WeeWX was not running for.

    The record itself cannot say whether the accumulator has anything for the
    period: under hardware record generation the graft happens AFTER this
    service's handler, so every hardware record is empty at this point.  What
    this extension injected is the discriminator."""

    @staticmethod
    def make_airgradient(sources=None, loop_fields=None, archive_interval=300):
        ag = AirGradient.__new__(AirGradient)
        ag.cfg = make_cfg(
            sources=sources if sources is not None
                    else [make_source('Proxy1', is_proxy=True, hostname='proxy1')],
            reading=None, loop_fields=loop_fields)
        ag.stale_logged = False
        ag.archive_interval = archive_interval
        ag.reading_times = []
        ag.reading_retention_secs = 2 * archive_interval
        ag.proxy_retry_after = {}
        return ag

    @staticmethod
    def make_event(ts, interval=5, unit_system=weewx.US, **fields):
        record = {'dateTime': ts, 'usUnits': unit_system, 'interval': interval}
        record.update(fields)
        return types.SimpleNamespace(record=record)

    def test_empty_period_is_filled_from_the_proxy(self):
        ag = self.make_airgradient()
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[make_reading()]) as fetch:
            ag.new_archive_record(event)
        # The window is the record's own interval: (dateTime - 5*60, dateTime].
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args[0][1:], (1000, 1300))
        self.assertEqual(event.record['pm2_5'], 1.03)
        self.assertEqual(event.record['co2'], 514.0)

    def test_period_we_had_readings_for_is_left_alone(self):
        # Whatever the accumulator made of them stands, and the proxy is not
        # asked at all.
        ag = self.make_airgradient()
        ag.record_reading_time({'dateTime': 1100.0})
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records') as fetch:
            ag.new_archive_record(event)
        fetch.assert_not_called()
        self.assertNotIn('pm2_5', event.record)
        self.assertNotIn('co2', event.record)

    def test_a_field_the_monitor_never_reports_is_not_a_gap(self):
        # Regression: tallying per field left a mapped field the monitor does
        # not report (no CO2 on an outdoor Open Air, no NOx without an SGP41)
        # looking like a gap on EVERY archive record -- a blocking proxy fetch
        # and a log line every archive period, forever, on a healthy install,
        # for a field a proxy polling that same monitor cannot supply either.
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5',
                                                'rco2': 'co2'})
        ag.cfg.reading = make_reading(rco2=None)
        packet = {'usUnits': weewx.US, 'dateTime': 1100.0}
        ag.new_loop_packet(types.SimpleNamespace(packet=packet))
        self.assertNotIn('co2', packet)  # the monitor reported no CO2
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records') as fetch:
            ag.new_archive_record(event)
        fetch.assert_not_called()
        self.assertNotIn('co2', event.record)

    def test_nothing_mapped_means_no_fetch_at_all(self):
        ag = self.make_airgradient(loop_fields={})
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records') as fetch:
            ag.new_archive_record(event)
        fetch.assert_not_called()

    def test_present_but_none_is_filled(self):
        # Under software record generation the accumulator has already had its
        # say, and it writes None for a type it holds with no usable values.
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300, pm2_5=None)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[make_reading()]):
            ag.new_archive_record(event)
        self.assertEqual(event.record['pm2_5'], 1.03)

    def test_a_value_already_in_the_record_is_never_overwritten(self):
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300, pm2_5=7.5)
        with mock.patch('user.airgradient.fetch_proxy_archive_records') as fetch:
            ag.new_archive_record(event)
        fetch.assert_not_called()
        self.assertEqual(event.record['pm2_5'], 7.5)

    def test_fractional_interval_is_not_truncated(self):
        # Under software record generation WeeWX sets interval to
        # archive_interval / 60, so a 90 second archive interval arrives as
        # 1.5.  to_int would make that a 60 second window, putting start_ts
        # 30 seconds inside the period.
        ag = self.make_airgradient(archive_interval=90,
                                   loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300, interval=1.5)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]) as fetch:
            ag.new_archive_record(event)
        self.assertEqual(fetch.call_args[0][1:], (1210, 1300))

    def test_a_reading_in_the_fractional_tail_of_the_period_counts_as_seen(self):
        # The 30 seconds truncation used to drop off the front of the window.
        ag = self.make_airgradient(archive_interval=90,
                                   loop_fields={'pm02Compensated': 'pm2_5'})
        ag.record_reading_time({'dateTime': 1220.0})
        event = self.make_event(1300, interval=1.5)
        with mock.patch('user.airgradient.fetch_proxy_archive_records') as fetch:
            ag.new_archive_record(event)
        fetch.assert_not_called()

    def test_interval_of_none_does_not_escape(self):
        # This runs outside the try, on a main-thread path: a TypeError here
        # would go up through dispatchEvent and stop weewxd.
        ag = self.make_airgradient(archive_interval=300,
                                   loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300, interval=None)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]) as fetch:
            ag.new_archive_record(event)
        self.assertEqual(fetch.call_args[0][1:], (1000, 1300))

    def test_record_without_an_interval_uses_the_archive_interval(self):
        ag = self.make_airgradient(archive_interval=600)
        event = self.make_event(1800, interval=0)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]) as fetch:
            ag.new_archive_record(event)
        self.assertEqual(fetch.call_args[0][1:], (1200, 1800))

    def test_temperature_is_filled_in_the_records_unit_system(self):
        ag = self.make_airgradient(loop_fields={'atmp': 'purple_temperature'})
        event = self.make_event(1300, unit_system=weewx.METRIC)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[make_reading()]):
            ag.new_archive_record(event)
        self.assertEqual(event.record['purple_temperature'], 21.91)

    def test_period_no_proxy_can_answer_for_is_left_empty(self):
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]), \
             mock.patch('user.airgradient.fetch_proxy_two_minute_reading') as two_min:
            ag.new_archive_record(event)
        # Long past: the two minute average says nothing about it, so it is
        # not even asked for.
        two_min.assert_not_called()
        self.assertNotIn('pm2_5', event.record)

    def test_proxies_are_tried_in_order_and_the_first_answer_wins(self):
        sources = [make_source('Proxy1', is_proxy=True, hostname='proxy1'),
                   make_source('Proxy2', is_proxy=True, hostname='proxy2')]
        ag = self.make_airgradient(sources=sources,
                                   loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        side_effect=[[], [make_reading(pm02Compensated=9.0)]]) as fetch:
            ag.new_archive_record(event)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(event.record['pm2_5'], 9.0)

    def test_disabled_and_sensor_sources_are_not_asked(self):
        sources = [make_source('Sensor1', is_proxy=False, hostname='sensor1'),
                   make_source('Proxy1', is_proxy=True, hostname='proxy1', enable=False),
                   make_source('Proxy2', is_proxy=True, hostname='proxy2')]
        ag = self.make_airgradient(sources=sources,
                                   loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[make_reading()]) as fetch:
            ag.new_archive_record(event)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args[0][0].hostname, 'proxy2')

    def test_unreachable_proxy_is_not_asked_again_for_an_archive_interval(self):
        # A catchup burst delivers records back to back; without the cooldown
        # a dead proxy would cost its whole timeout for every one of them.
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5'})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=None) as fetch:
            ag.new_archive_record(self.make_event(1300))
            ag.new_archive_record(self.make_event(1600))
            ag.new_archive_record(self.make_event(1900))
        fetch.assert_called_once()

    def test_unreachable_proxy_is_asked_again_after_the_cooldown(self):
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5'})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=None) as fetch:
            ag.new_archive_record(self.make_event(1300))
            # Pretend the cooldown has expired.
            ag.proxy_retry_after = {k: 0.0 for k in ag.proxy_retry_after}
            ag.new_archive_record(self.make_event(1600))
        self.assertEqual(fetch.call_count, 2)

    def test_backfill_failure_is_logged_and_does_not_escape(self):
        # Main thread: an exception escaping here goes up through
        # dispatchEvent and stops weewxd.
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5'})
        event = self.make_event(1300)
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        side_effect=Exception('boom')):
            ag.new_archive_record(event)
        self.assertNotIn('pm2_5', event.record)

    def test_terminate_passes_through(self):
        ag = self.make_airgradient(loop_fields={'pm02Compensated': 'pm2_5'})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        side_effect=Terminate()):
            with self.assertRaises(Terminate):
                ag.new_archive_record(self.make_event(1300))

class TestTwoMinuteStandIn(unittest.TestCase):
    """When no proxy has archived the period that just closed -- a proxy with
    a poll-freq-offset, or one that was down -- its two minute average can
    stand in, but only for a period that average actually covers."""

    @staticmethod
    def make_airgradient():
        ag = AirGradient.__new__(AirGradient)
        ag.cfg = make_cfg(sources=[make_source('Proxy1', is_proxy=True, hostname='proxy1')],
                          reading=None, loop_fields={'pm02Compensated': 'pm2_5'})
        ag.stale_logged = False
        ag.archive_interval = 300
        ag.reading_times = []
        ag.reading_retention_secs = 600
        ag.proxy_retry_after = {}
        return ag

    @staticmethod
    def two_minute_reading(ts):
        return make_reading(
            measurementTime=datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc),
            pm02Compensated=9.0)

    def test_stands_in_for_the_period_that_just_closed(self):
        ag = self.make_airgradient()
        end_ts = int(time.time()) - 20
        event = types.SimpleNamespace(
            record={'dateTime': end_ts, 'usUnits': weewx.US, 'interval': 5})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]), \
             mock.patch('user.airgradient.fetch_proxy_two_minute_reading',
                        return_value=self.two_minute_reading(end_ts + 10)):
            ag.new_archive_record(event)
        self.assertEqual(event.record['pm2_5'], 9.0)

    def test_stale_reading_whose_span_misses_the_period_is_refused(self):
        # The reading is stamped at the end of the span it covers, so it
        # describes (ts - 120, ts].  The enclosing recency guard settles the
        # far edge, so the only way that span can miss the period is by
        # ending before the period even began -- which is what a proxy that
        # stopped polling leaves behind.
        ag = self.make_airgradient()
        end_ts = int(time.time()) - 20
        start_ts = end_ts - 300
        event = types.SimpleNamespace(
            record={'dateTime': end_ts, 'usUnits': weewx.US, 'interval': 5})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]), \
             mock.patch('user.airgradient.fetch_proxy_two_minute_reading',
                        return_value=self.two_minute_reading(start_ts - 10)):
            ag.new_archive_record(event)
        self.assertNotIn('pm2_5', event.record)

    def test_reading_stamped_inside_the_period_is_taken(self):
        # The boundary the test above sits just outside of.
        ag = self.make_airgradient()
        end_ts = int(time.time()) - 20
        start_ts = end_ts - 300
        event = types.SimpleNamespace(
            record={'dateTime': end_ts, 'usUnits': weewx.US, 'interval': 5})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]), \
             mock.patch('user.airgradient.fetch_proxy_two_minute_reading',
                        return_value=self.two_minute_reading(start_ts + 10)):
            ag.new_archive_record(event)
        self.assertEqual(event.record['pm2_5'], 9.0)

    def test_older_period_never_asks_for_it(self):
        ag = self.make_airgradient()
        end_ts = int(time.time()) - 3600
        event = types.SimpleNamespace(
            record={'dateTime': end_ts, 'usUnits': weewx.US, 'interval': 5})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]), \
             mock.patch('user.airgradient.fetch_proxy_two_minute_reading') as two_min:
            ag.new_archive_record(event)
        two_min.assert_not_called()
        self.assertNotIn('pm2_5', event.record)

    def test_unreachable_proxy_is_not_asked_for_a_two_minute_reading(self):
        # It was just put on cooldown for failing the archive fetch.
        ag = self.make_airgradient()
        end_ts = int(time.time()) - 20
        event = types.SimpleNamespace(
            record={'dateTime': end_ts, 'usUnits': weewx.US, 'interval': 5})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=None), \
             mock.patch('user.airgradient.fetch_proxy_two_minute_reading') as two_min:
            ag.new_archive_record(event)
        two_min.assert_not_called()

    def test_proxy_holding_no_two_minute_record_fills_nothing(self):
        ag = self.make_airgradient()
        end_ts = int(time.time()) - 20
        event = types.SimpleNamespace(
            record={'dateTime': end_ts, 'usUnits': weewx.US, 'interval': 5})
        with mock.patch('user.airgradient.fetch_proxy_archive_records',
                        return_value=[]), \
             mock.patch('user.airgradient.fetch_proxy_two_minute_reading',
                        return_value=None):
            ag.new_archive_record(event)
        self.assertNotIn('pm2_5', event.record)

class TestArchiveHandlerBinding(unittest.TestCase):
    """A direct-sensor install has no history to ask for, so it must see no
    trace of the backfill: no binding, no fetches, no log messages."""

    def setUp(self):
        self.n_xtypes = len(weewx.xtypes.xtypes)
        self.orig_accum_maps = list(weewx.accum.accum_dict.maps)

    def tearDown(self):
        del weewx.xtypes.xtypes[0:len(weewx.xtypes.xtypes) - self.n_xtypes]
        weewx.accum.accum_dict.maps[:] = self.orig_accum_maps

    def build(self, sources):
        engine = mock.Mock()
        engine.console.archive_interval = 300
        config = {'AirGradient': dict({'LoopFields': dict(LOOP_FIELDS)}, **sources)}
        with mock.patch('user.airgradient.get_reading', return_value=make_reading()), \
             mock.patch('user.airgradient.threading.Thread'):
            ag = AirGradient(engine, config)
        return engine, ag

    def test_sensor_only_install_does_not_bind(self):
        engine, ag = self.build({'Sensor1': {'enable': True, 'hostname': 'sensor1'}})
        self.assertEqual(engine.bind.call_args_list,
                         [mock.call(weewx.NEW_LOOP_PACKET, ag.new_loop_packet)])

    def test_disabled_proxy_does_not_bind(self):
        engine, ag = self.build({
            'Proxy1': {'enable': False, 'hostname': 'proxy1'},
            'Sensor1': {'enable': True, 'hostname': 'sensor1'}})
        self.assertEqual(engine.bind.call_args_list,
                         [mock.call(weewx.NEW_LOOP_PACKET, ag.new_loop_packet)])

    def test_enabled_proxy_binds(self):
        engine, ag = self.build({'Proxy1': {'enable': True, 'hostname': 'proxy1'}})
        self.assertIn(mock.call(weewx.NEW_ARCHIVE_RECORD, ag.new_archive_record),
                      engine.bind.call_args_list)

class TestInstallerConfig(unittest.TestCase):
    """install.py's [StdReport] and [AirGradient] defaults.  These are only
    ever read on a fresh `weectl extension install`, so a wrong value ships
    silently: weecfg merges the stanza with conditional_merge, which fills in
    absent keys only and never rewrites an existing weewx.conf."""

    REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @classmethod
    def installer_module(cls):
        """install.py, loaded as a module."""
        spec = importlib.util.spec_from_file_location(
            'airgradient_install', os.path.join(cls.REPO_DIR, 'install.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @classmethod
    def installer_config(cls):
        """install.py's config stanza, whichever form it is written in."""
        return cls.installer_module().AirGradientInstaller()['config']

    def test_html_root_is_a_bare_subdirectory(self):
        """HTML_ROOT must NOT carry a public_html prefix.  weecfg prepends the
        installation's own StdReport HTML_ROOT at install time
        (ExtensionEngine.install_config -> prepend_path), so 'airgradient'
        becomes public_html/airgradient -- or whatever that installation uses.
        Writing 'public_html/airgradient' here would land the report in
        public_html/public_html/airgradient."""
        report = self.installer_config()['StdReport']['AirGradientReport']
        self.assertEqual(report['HTML_ROOT'], 'airgradient')
        self.assertEqual(report['skin'], 'airgradient')

    def test_demo_report_is_enabled_by_default(self):
        # The demo page is meant to render without the user turning it on.
        report = self.installer_config()['StdReport']['AirGradientReport']
        self.assertTrue(weeutil.weeutil.to_bool(report['enable']))

    def test_loop_fields_ships_empty(self):
        """DELIBERATELY empty.  weectl merges this stanza into an existing
        [AirGradient] section on upgrade, so a prefilled mapping would be
        injected into a customized one -- which is exactly what happened on
        07/18/2026, when the pm mappings landed in a weewx-purple
        coexistence config and this extension began overwriting purple's
        loop values."""
        airgradient = self.installer_config()['AirGradient']
        self.assertIn('LoopFields', airgradient)
        self.assertEqual(dict(airgradient['LoopFields']), {})

    def test_source_defaults(self):
        """One sensor enabled, every proxy and the second sensor off.  Values
        are compared through to_bool/to_int because a ConfigObj stanza yields
        strings where a plain dict yields bools and ints -- the installed
        weewx.conf is text either way, and airgradient.py coerces on read."""
        airgradient = self.installer_config()['AirGradient']
        self.assertEqual(weeutil.weeutil.to_int(airgradient['poll_secs']), 15)
        for name in ['Proxy1', 'Proxy2', 'Proxy3', 'Proxy4']:
            source = airgradient[name]
            self.assertFalse(weeutil.weeutil.to_bool(source['enable']), name)
            # airgradient-proxy listens on 8080, not 8000.
            self.assertEqual(weeutil.weeutil.to_int(source['port']), 8080, name)
            # A proxy answers from its own database on the LAN; if it has not
            # answered in a second it is down.  This also bounds the archive
            # backfill, which runs on the main thread once per record.
            self.assertEqual(weeutil.weeutil.to_int(source['timeout']), 1, name)
        self.assertEqual(airgradient['Proxy1']['hostname'], 'proxy1')
        self.assertTrue(weeutil.weeutil.to_bool(airgradient['Sensor1']['enable']))
        self.assertFalse(weeutil.weeutil.to_bool(airgradient['Sensor2']['enable']))
        for name in ['Sensor1', 'Sensor2']:
            source = airgradient[name]
            self.assertEqual(weeutil.weeutil.to_int(source['port']), 80, name)
            self.assertEqual(weeutil.weeutil.to_int(source['timeout']), 15, name)
        self.assertEqual(airgradient['Sensor1']['hostname'], 'airgradient')
        self.assertEqual(airgradient['Sensor2']['hostname'], 'airgradient2')

    def test_stanza_carries_comments(self):
        """The point of building the stanza from a ConfigObj: weectl writes
        the comments into a fresh weewx.conf, so each option arrives with a
        line saying what it does."""
        config = self.installer_config()
        self.assertTrue(config['AirGradient'].comments['poll_secs'])
        self.assertTrue(config['AirGradient']['Proxy1'].comments['timeout'])

    def test_version_matches_the_module(self):
        """The version lives in THREE places and they must not drift:
        install.py, WEEWX_AIRGRADIENT_VERSION, and the skin's [Extras]
        version.  Without the third, a release that forgets skin.conf ships
        a stale version silently -- nothing else reads it."""
        self.assertEqual(self.installer_module().AirGradientInstaller()['version'],
                         user.airgradient.WEEWX_AIRGRADIENT_VERSION)
        skin = configobj.ConfigObj(
            os.path.join(self.REPO_DIR, 'skins', 'airgradient', 'skin.conf'),
            encoding='utf-8', file_error=True)
        self.assertEqual(skin['Extras']['version'],
                         user.airgradient.WEEWX_AIRGRADIENT_VERSION)

    def test_imports_extension_installer_canonically(self):
        """`from weecfg.extension import ExtensionInstaller`, never
        `from setup import ...`.  WeeWX's own bundled examples have used the
        canonical form since at least 4.6.0, and `weecfg.extension` carries
        ExtensionInstaller at every version this extension supports.  The
        `setup` name only resolves through a compatibility shim WeeWX added
        on 2023-01-30 (`sys.modules['setup'] = sys.modules[__name__]`), which
        is absent from 4.6.0 through 4.10.0 -- so the legacy spelling would
        fail across most of the supported range, and weecfg would report it
        as the misleading \"Cannot find 'install' module\"."""
        with open(os.path.join(self.REPO_DIR, 'install.py'),
                  encoding='utf-8') as f:
            source = f.read()
        self.assertIn('from weecfg.extension import ExtensionInstaller', source)
        self.assertNotIn('from setup import', source)

class TestWeewxVersionAtLeast(unittest.TestCase):
    """The WeeWX 4.6 floor.  The demo skin's template uses $lang and
    $gettext, which arrived in WeeWX 4.6.0; below that Cheetah's
    `#errorCatcher Echo` renders them into the page verbatim, so the page
    is visibly broken rather than falling back to English."""

    # 'unknown' stands for a version string with no digits at all: it must
    # be refused, not crash.  '4.5b1'/'4.6b1' put the non-digit inside a
    # chunk that is actually compared, which the .0b1 forms never do.
    REJECT = ['3.9.2', '4', '4.0.0', '4.5.1', '4.5.1a1', '4.5b1', 'unknown']
    ACCEPT = ['4.6', '4.6.0', '4.6.2', '4.6b1', '4.9.1', '4.10.0', '4.10.2',
              '5', '5.0.0', '5.1.0b1', '5.5.0']

    def check(self, version):
        with mock.patch.object(weewx, '__version__', version):
            return user.airgradient.weewx_version_at_least((4, 6))

    def test_rejects_below_4_6(self):
        for version in self.REJECT:
            self.assertFalse(self.check(version), version)

    def test_accepts_4_6_and_later(self):
        for version in self.ACCEPT:
            self.assertTrue(self.check(version), version)

    def test_the_string_comparison_trap(self):
        """Why this helper exists at all: WeeWX 4.10 -- the last WeeWX 4
        series -- sorts below "4.6" as text, so `weewx.__version__ < "4.6"`
        would refuse the newest WeeWX 4 releases."""
        self.assertTrue('4.10.0' < '4.6')
        self.assertTrue('4.10.2' < '4.6')
        self.assertTrue(self.check('4.10.0'))
        self.assertTrue(self.check('4.10.2'))

    def test_installer_guard_matches_the_module(self):
        """install.py carries its own copy -- it cannot import the
        extension -- so the two must agree on every version."""
        module = TestInstallerConfig.installer_module()
        for version in self.REJECT + self.ACCEPT:
            with mock.patch.object(weewx, '__version__', version):
                self.assertEqual(module.weewx_version_at_least((4, 6)),
                                 user.airgradient.weewx_version_at_least((4, 6)),
                                 version)


class TestI18n(unittest.TestCase):
    """The demo skin's translation plumbing -- the same machinery
    weewx-purple/skyfield/celestial/loopdata ship: [Texts] is gettext-style
    (the English string IS the key; a report falls back to it one string at a
    time) and observation labels ride [Labels] [[Generic]].  Both live only
    in lang/<lang>.conf; skin.conf repeats neither, because a string in both
    places would shadow its own translation.  Unit labels are NOT part of
    this -- see test_no_lang_file_carries_unit_labels."""

    REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    SKIN_DIR = os.path.join(REPO_DIR, 'skins', 'airgradient')
    LANG_DIR = os.path.join(SKIN_DIR, 'lang')
    LANGUAGES = ['en', 'de', 'fr', 'nl', 'es']

    @classmethod
    def lang_conf(cls, name: str) -> configobj.ConfigObj:
        return configobj.ConfigObj(os.path.join(cls.LANG_DIR, name),
                                   encoding='utf-8', file_error=True)

    @classmethod
    def rendered_keys(cls):
        """Every translation key the page can render, read from the
        $gettext("...")/$gettext(\'...\') literals in the template (keys are
        single-line literals by convention)."""
        with open(os.path.join(cls.SKIN_DIR, 'index.html.tmpl'),
                  encoding='utf-8') as f:
            found = re.findall(r'\$gettext\(\s*(?:"([^"]+)"|\'([^\']+)\')\s*\)',
                               f.read())
        assert found
        return {a or b for a, b in found}

    def test_installer_lists_lang_files(self):
        # Scrape the source rather than reading the loaded module: the file
        # list is what must be right, and a typo'd path would still import.
        with open(os.path.join(self.REPO_DIR, 'install.py'),
                  encoding='utf-8') as f:
            installed = set(re.findall(r"'skins/airgradient/lang/(\w+\.conf)'",
                                       f.read()))
        on_disk = {name for name in os.listdir(self.LANG_DIR)
                   if name.endswith('.conf')}
        self.assertEqual(installed, on_disk)
        self.assertEqual(on_disk, {lang + '.conf' for lang in self.LANGUAGES})

    def test_en_conf_ships_exactly_what_renders(self):
        """Both directions: a rendered key missing from lang/en.conf fails,
        and an en.conf key nothing renders fails -- the English file is the
        reference dictionary for translators."""
        conf = self.lang_conf('en.conf')
        shipped = dict(conf['Texts'])
        rendered = self.rendered_keys()
        self.assertEqual(sorted(rendered - set(shipped)), [],
                         'rendered but not in en.conf')
        self.assertEqual(sorted(set(shipped) - rendered), [],
                         'in en.conf but never rendered')
        # English is the identity translation: every value equals its key.
        self.assertEqual([k for k, v in shipped.items() if v != k], [])
        # en.conf is the only home for the observation labels.
        self.assertEqual(sorted(conf['Labels']['Generic']),
                         ['co2', 'nox', 'noxIndex', 'pm2_5', 'pm2_5_aqi',
                          'tvoc', 'tvocIndex'])

    def test_skin_conf_repeats_no_translatable_string(self):
        """skin.conf must carry no [Labels], [Texts] or [Units] [[Labels]].

        WeeWX merges the lang file named by skin.conf's own `lang` line
        BEFORE it merges the rest of skin.conf (reportengine._build_skin_dict),
        so any translatable string left in skin.conf shadows its own
        translation: `lang = de` there used to give a German title and tabs
        beside an English "Air Quality Index" heading and English plot
        titles.  Keeping the lang files as the single source removes the
        cause rather than documenting it."""
        skin = configobj.ConfigObj(os.path.join(self.SKIN_DIR, 'skin.conf'),
                                   encoding='utf-8', file_error=True)
        self.assertNotIn('Labels', skin)
        self.assertNotIn('Texts', skin)
        self.assertNotIn('Labels', skin.get('Units', {}))
        # The year axis format is language-specific too, and shadows the
        # same way: a value here beat de.conf's %d.%m. whenever lang was
        # chosen in skin.conf.  day/week/month legitimately keep theirs --
        # %H:%M and %d are language-neutral and no lang file overrides them.
        self.assertNotIn('x_label_format',
                         skin['ImageGenerator']['year_images'])
        for period in ['day_images', 'week_images', 'month_images']:
            self.assertIn('x_label_format', skin['ImageGenerator'][period],
                          period)

    def test_lang_files_consistent(self):
        """Every shipped lang file must parse, translate exactly en.conf's
        keys (a stale key would silently never render; a missing one ships
        an untranslated string), and carry the same [Labels] keys."""
        en = self.lang_conf('en.conf')
        for lang in self.LANGUAGES:
            conf = self.lang_conf(lang + '.conf')
            self.assertEqual(set(conf['Texts']), set(en['Texts']), lang)
            for key, val in dict(conf['Texts']).items():
                self.assertIsInstance(val, str, (lang, key))
                self.assertTrue(val, (lang, key))
            self.assertEqual(set(conf['Labels']['Generic']),
                             set(en['Labels']['Generic']), lang)

    def test_no_lang_file_carries_unit_labels(self):
        """A [Units] [[Labels]] block in a lang file would be dead text.
        weewx.units.Formatter.get_label_string consults
        weewx.units.default_unit_label_dict FIRST and only then the
        skin/lang dictionary, and airgradient.py registers aqi, aqi_color,
        tvoc_index and nox_index into that dict at import time -- so a
        translation of those four could never render, while looking for all
        the world like it had been translated.  This shipped in a draft of
        4.0: the German page drew German plot titles beside y axes still
        reading "TVOC Index" and "NOx Index"."""
        for lang in self.LANGUAGES:
            conf = self.lang_conf(lang + '.conf')
            self.assertNotIn('Units', conf, lang)
        # And the precedence this rests on, so the test fails loudly if a
        # future WeeWX ever reverses it.
        formatter = weewx.units.Formatter(
            unit_label_dict={'tvoc_index': ' NOT THIS ONE'})
        self.assertEqual(formatter.get_label_string('tvoc_index'),
                         weewx.units.default_unit_label_dict['tvoc_index'])

    def test_every_lang_file_sets_the_year_axis_format(self):
        """Including en.conf.  Merge order is lang file -> [[Defaults]] ->
        report section, so a station running [[Defaults]] lang = de with
        lang = en on this report merges the German %d.%m. first; if en.conf
        were silent, nothing would put the US order back and the English
        page would carry a German year axis."""
        for lang in self.LANGUAGES:
            conf = self.lang_conf(lang + '.conf')
            self.assertIn('ImageGenerator', conf, lang)
            fmt = conf['ImageGenerator']['year_images']['x_label_format']
            self.assertTrue(fmt, lang)
        # en carries the US month/day order, which is what skin.conf used
        # to hold before it had to stop shadowing the translations.
        self.assertEqual(
            self.lang_conf('en.conf')['ImageGenerator']['year_images']['x_label_format'],
            '%m/%d')

    def test_html_lang_is_reduced_to_a_bare_language_tag(self):
        """WeeWX 5.1+ accepts locale-style specs (de_DE.UTF-8) and loads
        de.conf for them, and its own SkinInfo strips the country -- but only
        the country (.split('_')[0]), and only from 5.x.  Below that the raw
        spec reaches the lang attribute, where it is not a valid BCP 47 tag.
        The template must reduce it rather than emit $lang directly, and it
        strips the encoding too, which WeeWX itself does not."""
        with open(os.path.join(self.SKIN_DIR, 'index.html.tmpl'),
                  encoding='utf-8') as f:
            tmpl = f.read()
        self.assertIn("#set $html_lang = $lang.split('.')[0].split('_')[0]",
                      tmpl)
        self.assertIn('<html lang="$html_lang">', tmpl)
        self.assertNotIn('<html lang="$lang">', tmpl)
        # And the reduction itself, on the forms WeeWX documents.
        for spec, expected in [('en', 'en'), ('de', 'de'), ('de_DE', 'de'),
                               ('de_DE.UTF-8', 'de'), ('en_AU.utf8', 'en')]:
            self.assertEqual(spec.split('.')[0].split('_')[0], expected, spec)

    def test_matches_weewx_seasons_vocabulary(self):
        """The plot-period tabs are copied from WeeWX's own Seasons lang
        files; if a sibling weewx checkout is present, pin them to it."""
        seasons_lang = os.path.join(self.REPO_DIR, '..', 'weewx', 'src',
                                    'weewx_data', 'skins', 'Seasons', 'lang')
        if not os.path.isdir(seasons_lang):
            self.skipTest('no ../weewx checkout')
        ours = self.rendered_keys()
        for lang in self.LANGUAGES:
            if lang == 'en':
                continue
            seasons = configobj.ConfigObj(
                os.path.join(seasons_lang, lang + '.conf'),
                encoding='utf-8', file_error=True)
            conf = self.lang_conf(lang + '.conf')
            shared = ours & set(seasons['Texts'])
            self.assertEqual(shared, {'Day', 'Week', 'Month', 'Year'}, lang)
            for key in shared:
                self.assertEqual(conf['Texts'][key], seasons['Texts'][key],
                                 (lang, key))


if __name__ == '__main__':
    unittest.main()
