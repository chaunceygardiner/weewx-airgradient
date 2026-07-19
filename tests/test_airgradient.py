#
#    See the file LICENSE.txt for your full rights.
#
"""Hermetic tests for weewx-airgradient.  No network access: everything from
the fetch stack down is exercised with mocks, and the xtype SQL paths run
against an in-memory SQLite database."""

import datetime
import logging
import sqlite3
import threading
import time
import types
import unittest

from typing import Any, Dict
from unittest import mock

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
            # Bound to NEW_LOOP_PACKET.
            engine.bind.assert_called_once_with(weewx.NEW_LOOP_PACKET, ag.new_loop_packet)
        finally:
            # Unregister anything this test added to the global xtypes list
            # and the global accumulator config.
            del weewx.xtypes.xtypes[0:len(weewx.xtypes.xtypes) - n_xtypes]
            weewx.accum.accum_dict.maps[:] = orig_accum_maps

    def test_startup_with_aqi_disabled(self):
        engine = mock.Mock()
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

if __name__ == '__main__':
    unittest.main()
