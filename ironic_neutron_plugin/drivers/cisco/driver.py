# Copyright 2013 OpenStack Foundation
# All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Implements a Nexus-OS NETCONF over SSHv2 API Client.

This is lifted partially from the cisco ml2 mechanism.
"""
from oslo.config import cfg

from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging

from ironic_neutron_plugin.drivers import base as base_driver
from ironic_neutron_plugin.drivers.cisco import commands

LOG = logging.getLogger(__name__)
print __name__


# TODO(morgabra) fix this
IGNORE_CLEAR = [
"no spanning-tree bpduguard enable"
]

class CiscoException(base_driver.DriverException):
    pass


class CiscoDriver(base_driver.Driver):

    def __init__(self, dry_run=None):
        self.dry_run = dry_run
        if dry_run == None:
            self.dry_run = cfg.CONF.ironic.dry_run
        self.ncclient = None

    def _filter_interface_conf(self, c):
        """determine if an interface configuration string is relevant."""
        if c.startswith("!"):
          return False

        if c.startswith("version "):
          return False

        if c.startswith("interface"):
          return False

        if not c:
          return False

        return True

    def _negate_conf(self, c):
        """negate a line of configuration"""
        return "no %s" % c

    def _get_result(self, res):
        """Get text reponse from an ncclient command.

        Example XML from ncclient:

        <?xml version="1.0" encoding="ISO-8859-1"?>
        <rpc-reply xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"
                   xmlns:if="http://www.cisco.com/nxos:1.0:if_manager"
                   xmlns:nxos="http://www.cisco.com/nxos:1.0"
                   message-id="urn:uuid:4a9be8b4-df85-11e3-ab20-becafe000bed">
          <data>
            !Command: show running-config interface Ethernet1/20
            !Time: Mon May 19 18:40:08 2014

            version 6.0(2)U2(4)

            interface Ethernet1/20
              shutdown
              spanning-tree port type edge
              spanning-tree bpduguard enable

          </data>
        </rpc-reply>

        Example return value:
        ['shutdown', 'spanning-tree port type edge', 'spanning-tree bpduguard enable']
        """
        if not res:
            return []

        # get the first child from the xml response
        res = res._root.getchildren()
        if len(res) != 1:
            raise Exception("cannot parse command response")

        # split the raw text by newline
        res = res[0].text.split("\n")

        # filter comments and other unrelated data
        return [c.strip() for c in res if self._filter_interface_conf(c)]

    def show(self, port, type="ethernet"):
        LOG.debug("Fetching interface %s" % (port.interface))

        eth_int = commands._make_ethernet_interface(port.interface)
        cmds = commands.show_interface_configuration(type, eth_int)

        result = self._run_commands(
            port.switch_host,
            port.switch_username,
            port.switch_password,
            cmds)

        return self._get_result(result)

    def clear(self, port):
        """
        Remove all configuration for a given interface, which includes
        the ethernet interface, related port-channel, and any dhcp snooping
        bindings or other port security features.

        For some reason, you can't run 'no interface eth x/x' on
        the 3172. So we have to read the config for the interface first
        and manually negate each option.

        'no interface port-channel' works as expected.

        TODO(morgabra) port security (delete from the dhcp snooping table, etc)
        """
        LOG.debug("clearing interface %s" % (port.interface))

        interface = port.interface
        portchan_int = commands._make_portchannel_interface(interface)
        eth_int = commands._make_ethernet_interface(interface)

        eth_conf = self.show(port, type='ethernet')
        eth_conf = [self._negate_conf(c) for c in eth_conf]

        cmds = commands._configure_interface('ethernet', eth_int)
        cmds = cmds + eth_conf + ['shutdown']
        cmds = cmds + commands._delete_port_channel_interface(portchan_int)

        # TODO(morgabra) Remove this when it works
        for cmd in IGNORE_CLEAR:
            if cmd in cmds:
                i = cmds.index(cmd)
                cmds.pop(i)

        self._run_commands(
            port.switch_host,
            port.switch_username,
            port.switch_password,
            cmds)

    def create(self, port):

        LOG.debug("Creating port %s for hardware_id %s"
                  % (port.interface, port.hardware_id))
        LOG.debug("Attaching vlan %s to interface %s"
                  % (port.vlan_id, port.interface))

        cmds = commands.create_port(
            hardware_id=port.hardware_id,
            interface=port.interface,
            vlan_id=port.vlan_id,
            ip=port.ip,
            mac_address=port.mac_address,
            trunked=port.trunked)

        return self._run_commands(
            port.switch_host,
            port.switch_username,
            port.switch_password,
            cmds)

    def delete(self, port):

        LOG.debug("Deleting port %s for hardware_id %s"
                  % (port.interface, port.hardware_id))

        cmds = commands.delete_port(
            interface=port.interface,
            vlan_id=port.vlan_id,
            trunked=port.trunked)

        self.detach(port)
        return self._run_commands(
            port.switch_host,
            port.switch_username,
            port.switch_password,
            cmds)

    def attach(self, port):

        LOG.debug("Attaching vlan %s to interface %s"
                  % (port.vlan_id, port.interface))

        cmds = commands.add_vlan(
            interface=port.interface,
            vlan_id=port.vlan_id,
            ip=port.ip,
            mac_address=port.mac_address,
            trunked=port.trunked)

        return self._run_commands(
            port.switch_host,
            port.switch_username,
            port.switch_password,
            cmds)

    def detach(self, port):

        LOG.debug("Detaching vlan %s from interface %s"
                  % (port.vlan_id, port.interface))

        cmds = commands.remove_vlan(
            interface=port.interface,
            vlan_id=port.vlan_id,
            ip=port.ip,
            mac_address=port.mac_address,
            trunked=port.trunked)

        return self._run_commands(
            port.switch_host,
            port.switch_username,
            port.switch_password,
            cmds)

    def _import_ncclient(self):
        """Import the NETCONF client (ncclient) module.

        The ncclient module is not installed as part of the normal Neutron
        distributions. It is imported dynamically in this module so that
        the import can be mocked, allowing unit testing without requiring
        the installation of ncclient.

        """
        return importutils.import_module('ncclient.manager')

    def _run_commands(self, host, username, password, commands):

        if not commands:
            LOG.debug("No commands to run")
            return

        LOG.debug("Switch host:%s executing commands: %s" % (host, commands))

        if self.dry_run:
            LOG.debug("Dry run is enabled, skipping")
            return None

        conn = None
        try:
            conn = self._connect(host, username, password)
            return conn.command(commands)
        except Exception as e:
            raise CiscoException(e)
        finally:
            try:
                if conn:
                    conn.close_session()
            except Exception as e:
                raise CiscoException(e)

    def _connect(self, host, username, password, port=22):
        if not self.ncclient:
            self.ncclient = self._import_ncclient()

        try:
            return self.ncclient.connect(host=host,
                                         port=port,
                                         username=username,
                                         password=password)
        except Exception as e:
            raise CiscoException(e)
