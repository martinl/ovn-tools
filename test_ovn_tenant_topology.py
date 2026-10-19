#!/usr/bin/env python3
# Copyright 2026 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Unit tests for ovn_tenant_topology.py."""

import json
import unittest
from unittest.mock import patch

from ovn_tenant_topology import (
    Colors,
    ConnectionConfigError,
    OpenStackSDKMissingError,
    ProjectNotFoundError,
    TopologyError,
    TopologyQueryError,
    _dot_escape,
    _mermaid_escape,
    build_topology_from_data,
    get_mock_topology,
    main,
    render_ascii,
    render_dot,
    render_json,
    render_mermaid,
    render_table,
    render_tree,
)


class TestOvnTenantTopology(unittest.TestCase):
    def setUp(self):
        self.topo = get_mock_topology()
        self.colors_off = Colors(enabled=False)
        self.colors_on = Colors(enabled=True)

    def test_mock_topology_structure(self):
        """Verifies graph model links correctly."""
        self.assertEqual(self.topo.project_name, "demo")
        self.assertEqual(len(self.topo.external_networks), 1)
        self.assertIn("ext-net-uuid-001", self.topo.external_networks)

        # Router check
        self.assertEqual(len(self.topo.routers), 1)
        router = self.topo.routers["router-uuid-001"]
        self.assertEqual(router.external_gateway_ip, "192.168.1.105")
        self.assertEqual(len(router.interfaces), 2)

        # Isolated networks check
        self.assertEqual(len(self.topo.isolated_networks), 1)
        self.assertIn("tenant-net-uuid-103", self.topo.isolated_networks)

        # Servers check
        self.assertEqual(len(self.topo.servers), 4)

        # Floating IP check on web-frontend-01
        server1 = self.topo.servers["server-uuid-001"]
        self.assertEqual(len(server1.ports), 1)
        self.assertEqual(server1.ports[0].floating_ip, "192.168.1.150")

    def test_render_tree(self):
        """Verifies Unicode tree rendering contains key entities and details."""
        out = render_tree(self.topo, self.colors_off, ovn_details=True, verbose=True)
        self.assertIn("External Network: public", out)
        self.assertIn("Router: tenant-gateway-router", out)
        self.assertIn("OVN LR: neutron-router-uuid-001", out)
        self.assertIn("web-frontend-01", out)
        self.assertIn("Floating IP: 192.168.1.150", out)
        self.assertIn("Isolated / Unrouted Networks", out)
        self.assertIn("backup-runner", out)
        self.assertIn("powering-off", out)
        self.assertIn("MAC: fa:16:3e:20:01:15", out)
        self.assertIn("SG: secgroup-web", out)
        self.assertIn("geneve:1024", out)

    def test_render_ascii_ovn_and_verbose(self):
        """Verifies ASCII rendering output format with ovn_details and verbose."""
        out_simple = render_ascii(self.topo)
        self.assertIn("[EXT: public]", out_simple)
        self.assertIn("[ROUTER: tenant-gateway-router]", out_simple)
        self.assertIn("[VM: web-frontend-01]", out_simple)
        self.assertIn("[FIP: 192.168.1.150]", out_simple)
        self.assertIn("[ISOLATED NETWORKS]", out_simple)
        self.assertNotIn("OVN LR:", out_simple)

        # With ovn_details and verbose enabled
        out_full = render_ascii(self.topo, ovn_details=True, verbose=True)
        self.assertIn("[OVN LR: neutron-router-uuid-001]", out_full)
        self.assertIn("[OVN LS: neutron-tenant-net-uuid-101]", out_full)
        self.assertIn("(MAC: fa:16:3e:20:01:15)", out_full)
        self.assertIn("(SG: secgroup-web, secgroup-default)", out_full)
        self.assertIn("powering-off", out_full)

    def test_render_table(self):
        """Verifies ASCII table format rendering."""
        out = render_table(self.topo, self.colors_off, verbose=False)
        self.assertIn("RESOURCE SUMMARY:", out)
        self.assertIn("Ext Nets", out)
        self.assertIn("Routers", out)
        self.assertIn("Instances", out)
        self.assertIn("WORKLOAD INVENTORY & ROUTING PATH:", out)
        self.assertIn("web-frontend-01", out)
        self.assertIn("192.168.1.150", out)
        self.assertIn("tenant-gateway-router", out)
        self.assertIn("[Isolated]", out)

        # With verbose
        out_verbose = render_table(self.topo, self.colors_off, verbose=True)
        self.assertIn("MAC Address", out_verbose)
        self.assertIn("Security Groups", out_verbose)
        self.assertIn("fa:16:3e:20:01:15", out_verbose)
        self.assertIn("secgroup-web", out_verbose)

    def test_render_json(self):
        """Verifies JSON output is valid and well-structured."""
        out = render_json(self.topo)
        data = json.loads(out)
        self.assertEqual(data["project_name"], "demo")
        self.assertIn("external_networks", data)
        self.assertIn("routers", data)
        self.assertIn("tenant_networks", data)
        self.assertIn("servers", data)

    def test_render_dot(self):
        """Verifies Graphviz DOT output format and escaping."""
        out = render_dot(self.topo)
        self.assertTrue(out.startswith("digraph OpenStackTopology {"))
        self.assertIn('"router_router_uuid_001"', out)
        self.assertIn('"net_ext_net_uuid_001"', out)
        self.assertIn('"vm_server_uuid_001"', out)
        self.assertTrue(out.strip().endswith("}"))

    def test_render_mermaid(self):
        """Verifies Mermaid markdown output format and isolated subnet definitions."""
        out = render_mermaid(self.topo)
        self.assertIn("```mermaid", out)
        self.assertIn("graph TD", out)
        self.assertIn("EXT_ext_net_uuid_001", out)
        self.assertIn("RTR_router_uuid_001", out)
        self.assertIn("SUB_app_sn_uuid_101", out)
        # Isolated subnet must be explicitly defined
        self.assertIn('SUB_backup_sn_uuid_103["🔌 Subnet: isolated-backup-net (172.16.50.0/24)"]', out)
        self.assertIn("VM_server_uuid_001", out)
        self.assertIn("```", out)

    def test_all_external_networks_toggle(self):
        """Verifies filtering of unconnected external networks unless requested."""
        topo_filtered = get_mock_topology(all_external=False)
        self.assertEqual(len(topo_filtered.external_networks), 1)

        topo_all = get_mock_topology(all_external=True)
        self.assertEqual(len(topo_all.external_networks), 2)
        self.assertIn("ext-net-uuid-unused-002", topo_all.external_networks)

    def test_colors_and_badges(self):
        """Verifies ANSI coloring helper methods and badges."""
        self.assertEqual(self.colors_off.bold("test"), "test")
        self.assertTrue(self.colors_on.bold("test").startswith("\033[1m"))
        self.assertTrue(self.colors_on.cyan("test").startswith("\033[36m"))
        self.assertTrue(self.colors_on.green("test").startswith("\033[32m"))
        self.assertTrue(self.colors_on.red("test").startswith("\033[31m"))

        # Badges
        self.assertIn("ACTIVE", self.colors_off.status_badge("ACTIVE"))
        self.assertIn("SHUTOFF", self.colors_off.status_badge("SHUTOFF"))
        self.assertIn("BUILD", self.colors_off.status_badge("BUILD"))
        self.assertIn("ERROR", self.colors_off.status_badge("ERROR"))
        self.assertIn("UNKNOWN", self.colors_off.status_badge("UNKNOWN"))

    def test_escaping_helpers(self):
        """Verifies escaping for Graphviz DOT and Mermaid strings."""
        self.assertEqual(_dot_escape('vm-"quoted"'), 'vm-\\"quoted\\"')
        self.assertEqual(_dot_escape("back\\slash"), "back\\\\slash")
        self.assertEqual(_mermaid_escape('vm-"quoted"'), "vm-'quoted'")

    def test_custom_exceptions_hierarchy(self):
        """Verifies domain exception inheritance."""
        self.assertTrue(issubclass(OpenStackSDKMissingError, TopologyError))
        self.assertTrue(issubclass(ConnectionConfigError, TopologyError))
        self.assertTrue(issubclass(ProjectNotFoundError, TopologyError))
        self.assertTrue(issubclass(TopologyQueryError, TopologyError))

    def test_edge_case_topologies(self):
        """Verifies topology construction with edge case models."""
        raw_nets = [
            {"id": "net-empty", "name": "empty-net", "router:external": False},
            {"id": "net-routed", "name": "routed-net", "router:external": False},
        ]
        raw_subnets = [
            {"id": "sn-routed", "network_id": "net-routed", "cidr": "10.10.10.0/24"}
        ]
        raw_routers = [
            {
                "id": "rtr-no-gw",
                "name": "internal-only-router",
                "status": "ACTIVE",
                "external_gateway_info": None,
            }
        ]
        raw_ports = [
            # Router interface port
            {
                "id": "port-rtr-iface",
                "device_id": "rtr-no-gw",
                "device_owner": "network:router_interface",
                "network_id": "net-routed",
                "fixed_ips": [{"subnet_id": "sn-routed", "ip_address": "10.10.10.1"}],
            },
            # Server with dual-stack IPs on same subnet
            {
                "id": "port-dual-stack",
                "device_id": "srv-dual",
                "network_id": "net-routed",
                "fixed_ips": [
                    {"subnet_id": "sn-routed", "ip_address": "10.10.10.50"},
                    {"subnet_id": "sn-routed", "ip_address": "10.10.10.51"},
                ],
            },
        ]
        raw_servers = [
            {"id": "srv-dual", "name": "server-dual-stack", "status": "ACTIVE"},
            {"id": "srv-no-port", "name": "server-no-port", "status": "BUILD"},
        ]

        topo = build_topology_from_data(
            raw_networks=raw_nets,
            raw_subnets=raw_subnets,
            raw_routers=raw_routers,
            raw_ports=raw_ports,
            raw_servers=raw_servers,
            raw_floating_ips=[],
            project_id="test-proj-id",
            project_name="test-proj",
        )

        # Isolated net check
        self.assertIn("net-empty", topo.isolated_networks)

        # Renderers should handle these edge cases without error
        tree_out = render_tree(topo, self.colors_off)
        self.assertIn("server-dual-stack", tree_out)
        self.assertIn("Internal / Disconnected Routers", tree_out)

        table_out = render_table(topo, self.colors_off)
        self.assertIn("server-dual-stack", table_out)
        self.assertIn("server-no-port", table_out)
        self.assertIn("No Port", table_out)

    def test_cli_main_invocations(self):
        """Verifies CLI entrypoint main() across all output formats."""
        formats = ["tree", "ascii", "table", "json", "dot", "mermaid"]
        for fmt in formats:
            with patch("sys.stdout.write"):
                exit_code = main(["--mock", "--format", fmt, "--no-color"])
                self.assertEqual(exit_code, 0, f"Format {fmt} exited with code {exit_code}")

        # Test flags
        with patch("sys.stdout.write"):
            self.assertEqual(main(["--mock", "--ovn-details", "-v"]), 0)
            self.assertEqual(main(["--mock", "--all-external-networks"]), 0)


if __name__ == "__main__":
    unittest.main()
