
#
#    See the file LICENSE.txt for your full rights.
#
"""Test processing packets."""

import logging
import unittest

from typing import Any, Dict

import weeutil.logger

import user.airgradient

log = logging.getLogger(__name__)

# Set up logging using the defaults.
weeutil.logger.setup('test_config', {})

VALID__PKT: Dict[str, Any] = {
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

VALID_PROXY_PKT: Dict[str, Any] = {
    "measurementTime": "2027-10-27T18:58:17.000Z",
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

class AirGradientTests(unittest.TestCase):
    #             U.S. EPA PM2.5 AQI
    #
    #  AQI Category  AQI Value  24-hr PM2.5
    # Good             0 -  50    0.0 -   9.0
    # Moderate        51 - 100    9.1 -  35.4
    # USG            101 - 150   35.5 -  55.4
    # Unhealthy      151 - 200   55.5 - 125.4
    # Very Unhealthy 201 - 300  125.5 - 225.4
    # Hazardous      301 - 500  225.5 and above

    def test_compute_pm2_5_aqi(self):

        # Good
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi( 0.0), 0)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi( 6.0), 33)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi( 9.0), 50)
        # 9.099 is truncated to 9
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(9.099), 50)

        # Moderate
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(9.1),  51)
        aqi = user.airgradient.AQI.compute_pm2_5_aqi(21.8)
        self.assertEqual(aqi, 75)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(35.499), 100)

        # USG
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(35.5), 101)
        aqi = user.airgradient.AQI.compute_pm2_5_aqi(45.4)
        self.assertEqual(aqi, 125)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(55.4), 150)

        # Unhealthy
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi( 55.5), 151)
        aqi = user.airgradient.AQI.compute_pm2_5_aqi(90.5)
        self.assertTrue(aqi, 175.4)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(125.4), 200)

        # Very Unhealthy
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(125.5), 201)
        aqi = user.airgradient.AQI.compute_pm2_5_aqi(175.4)
        self.assertEqual(aqi, 250)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(225.4), 300)

        # Harzadous
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(225.5), 301)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(325.0), 400)
        aqi = user.airgradient.AQI.compute_pm2_5_aqi(375.0)
        self.assertEqual(aqi, 450)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi(425.0), 500)

    #             U.S. EPA PM2.5 AQI
    #
    #  AQI Category  AQI Value  24-hr PM2.5
    # Good             0 -  50    0.0 -   9.0
    # Moderate        51 - 100    9.1 -  35.4
    # USG            101 - 150   35.5 -  55.4
    # Unhealthy      151 - 200   55.5 - 125.4
    # Very Unhealthy 201 - 300  125.5 - 225.4
    # Hazardous      301 - 500  225.5 and above

    def test_compute_pm2_5_aqi_color(self):

        # Good
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color( 0), 228 << 8)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(25), 228 << 8)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(50), 228 << 8)

        # Moderate
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color( 51), (255 << 16) + (255 << 8))
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color( 75), (255 << 16) + (255 << 8))
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(100), (255 << 16) + (255 << 8))

        # USG
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(101), (255 << 16) + (126 << 8))
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(125), (255 << 16) + (126 << 8))
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(150), (255 << 16) + (126 << 8))

        # Unhealthy
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(151), (255 << 16))
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(175), (255 << 16))
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(200), (255 << 16))

        # Very Unhealthy
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(201), (143 << 16) + (63 << 8) + 151)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(250), (143 << 16) + (63 << 8) + 151)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(300), (143 << 16) + (63 << 8) + 151)

        # Harzadous
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(301), (126 << 16) + 35)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(350), (126 << 16) + 35)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(400), (126 << 16) + 35)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(401), (126 << 16) + 35)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(450), (126 << 16) + 35)
        self.assertEqual(user.airgradient.AQI.compute_pm2_5_aqi_color(500), (126 << 16) + 35)

    def test_is_sane(self):
        ok, reason = user.airgradient.is_sane(VALID_PKT)
        self.assertTrue(ok, reason)

        ok, reason = user.airgradient.is_sane(VALID_PROXY_PKT)
        self.assertTrue(ok, reason)

        # Bad Date
        bad_pkt= VALID_PKT.copy()
        bad_pkt['measurementTime'] = 'xyz'
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertEqual(reason, 'measurementTime could not be converted to a dateTime: xyz')

        # Bad Temp
        bad_pkt = VALID_PKT.copy()
        bad_pkt['atmp'] = 'nan'
        ok, reason = user.airgradient.is_sane(bad_pkt)
        self.assertFalse(ok)
        self.assertEqual(reason, "atmp is not an instance of <class 'float'>: nan")

if __name__ == '__main__':
    unittest.main()
