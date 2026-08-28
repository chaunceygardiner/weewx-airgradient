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

import sys
from io import StringIO

import configobj

import weewx

from weecfg.extension import ExtensionInstaller

CONFIG="""
[StdReport]
    [[AirGradientReport]]
        # The "AirGradientReport" uses the "airgradient" skin, which showcases
        # the extension.  Images and files are placed in a dedicated
        # subdirectory.
        HTML_ROOT = airgradient
        enable = true
        skin = airgradient

[AirGradient]
    # This section is for configuring the extension weewx-airgradient.
    # See the README.md for details.

    # How often to poll the sensor/proxy, in seconds.
    poll_secs = 15

    # Which AirGradient readings to insert into loop packets, and what to call
    # them there: <airgradient-field> = <weewx-field>.
    #
    # DELIBERATELY EMPTY.  weectl merges this stanza into an existing
    # [AirGradient] section on upgrade, so any entry here would be injected
    # into a customized mapping -- for instance the README's weewx-purple
    # coexistence setup, in which purple owns the pm fields and this extension
    # must NOT map them.  The extension logs an error while this is empty; see
    # the README for the suggested mapping to paste in.
    [[LoopFields]]

    # Proxies are instances of airgradient-proxy.  A proxy keeps its own
    # archive history, which is the only thing that can fill in the archive
    # periods WeeWX was not running for -- see "Filling gaps after downtime"
    # in the README.
    [[Proxy1]]
        enable = False
        # Replace with the host name or IP address of the first proxy
        hostname = proxy1
        # The port airgradient-proxy listens on
        port = 8080
        # http timeout (seconds).  A proxy answers out of its own database on
        # the local network, so a second is ample; if it has not answered by
        # then, it is down.  This timeout also bounds the archive backfill,
        # which runs on WeeWX's main thread once per archive record.
        timeout = 1
    [[Proxy2]]
        enable = False
        hostname = proxy2
        port = 8080
        timeout = 1
    [[Proxy3]]
        enable = False
        hostname = proxy3
        port = 8080
        timeout = 1
    [[Proxy4]]
        enable = False
        hostname = proxy4
        port = 8080
        timeout = 1

    # Sensors are AirGradient monitors, polled directly.
    [[Sensor1]]
        enable = True
        # Replace with the host name or IP address of the first sensor
        hostname = airgradient
        # Port is usually 80
        port = 80
        # http timeout (seconds).  A sensor's own processor is slow and
        # easily overwhelmed, so this is generous.
        timeout = 15
    [[Sensor2]]
        enable = False
        # Replace with the host name or IP address of the second sensor
        hostname = airgradient2
        port = 80
        timeout = 15
"""

airgradient_dict = configobj.ConfigObj(StringIO(CONFIG))

# Kept in step with the copy in bin/user/airgradient.py; install.py cannot
# import the extension.
def weewx_version_at_least(minimum):
    """Is the running WeeWX at least `minimum` (e.g. (4, 6))?

    Compared as integers, not as text: WeeWX 4.10 sorts BELOW "4.6" as a
    string, so a plain comparison would reject the whole 4.10 series (the
    last of WeeWX 4).  weeutil's own version_compare cannot be used here --
    it arrived after 4.6, so it is missing from some of the versions this
    has to reject.
    """
    running = []
    for chunk in weewx.__version__.split('.')[:len(minimum)]:
        digits = ''
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        running.append(int(digits) if digits else 0)
    return tuple(running) >= minimum

def loader():
    if sys.version_info[0] < 3 or (sys.version_info[0] == 3 and sys.version_info[1] < 9):
        sys.exit("weewx-airgradient requires Python 3.9 or later, found %s.%s" % (sys.version_info[0], sys.version_info[1]))

    # The demo skin's template uses $lang and $gettext, which arrived in
    # WeeWX 4.6.0; below that they render into the page verbatim.
    if not weewx_version_at_least((4, 6)):
        sys.exit("weewx-airgradient requires WeeWX 4.6 or later, found %s" % weewx.__version__)

    return AirGradientInstaller()

class AirGradientInstaller(ExtensionInstaller):
    def __init__(self):
        super(AirGradientInstaller, self).__init__(
            version="4.0",
            name='airgradient',
            description='Record air quality readings from AirGradient monitors (or airgradient-proxy services).',
            author="John A Kline",
            author_email="john@johnkline.com",
            data_services='user.airgradient.AirGradient',
            config = airgradient_dict,
            files=[
                ('bin/user', ['bin/user/airgradient.py']),
                ('skins/airgradient', [
                    'skins/airgradient/index.html.tmpl',
                    'skins/airgradient/skin.conf',
                ]),
                ('skins/airgradient/font', [
                    'skins/airgradient/font/OpenSans-Regular.ttf',
                    'skins/airgradient/font/OpenSans-Bold.ttf',
                    'skins/airgradient/font/license.txt',
                ]),
                ('skins/airgradient/lang', [
                    'skins/airgradient/lang/en.conf',
                    'skins/airgradient/lang/de.conf',
                    'skins/airgradient/lang/fr.conf',
                    'skins/airgradient/lang/nl.conf',
                    'skins/airgradient/lang/es.conf',
                ]),
            ]
        )
