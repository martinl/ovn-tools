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

from ovn_tenant_topology import (
    Colors,
    build_topology_from_data,
    get_mock_topology,
    render_ascii,
    render_dot,
    render_json,
    render_mermaid,
    render_tree,
)


class TestOvnTenantTopology(unittest.TestCase):
    def setUp(self):
        self.topo = get_mock_topology()
        self.colors_off = Colors(enabled=False)

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
        """Verifies Unicode tree rendering contains key entities."""
        out = render_tree(self.topo, self.colors_off, ovn_details=True, verbose=True)
        self.assertIn("External Network: public", out)
        self.assertIn("Router: tenant-gateway-router", out)
        self.assertIn("OVN LR: neutron-router-uuid-001", out)
        self.assertIn("web-frontend-01", out)
        self.assertIn("Floating IP: 192.168.1.150", out)
        self.assertIn("Isolated / Unrouted Networks", out)
        self.assertIn("backup-runner", out)

    def test_render_ascii(self):
        """Verifies ASCII rendering output format."""
        out = render_ascii(self.topo)
        self.assertIn("[EXT: public]", out)
        self.assertIn("[ROUTER: tenant-gateway-router]", out)
        self.assertIn("[VM: web-frontend-01]", out)
        self.assertIn("[FIP: 192.168.1.150]", out)
        self.assertIn("[ISOLATED NETWORKS]", out)

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
        """Verifies Graphviz DOT output format."""
        out = render_dot(self.topo)
        self.assertTrue(out.startswith("digraph OpenStackTopology {"))
        self.assertIn('"router_router_uuid_001"', out)
        self.assertIn('"net_ext_net_uuid_001"', out)
        self.assertIn('"vm_server_uuid_001"', out)
        self.assertTrue(out.strip().endswith("}"))

    def test_render_mermaid(self):
        """Verifies Mermaid markdown output format."""
        out = render_mermaid(self.topo)
        self.assertIn("```mermaid", out)
        self.assertIn("graph TD", out)
        self.assertIn("EXT_ext_net_uuid_001", out)
        self.assertIn("RTR_router_uuid_001", out)
        self.assertIn("VM_server_uuid_001", out)
        self.assertIn("```", out)


if __name__ == "__main__":
    unittest.main()
