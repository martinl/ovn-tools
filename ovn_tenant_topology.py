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

"""
Horizon Tenant OVN Network Topology CLI

Outputs OpenStack tenant network topology in the terminal, matching Horizon's
Project -> Network -> Network Topology graph. Designed for OpenStack Director
(TripleO / Overcloud / Undercloud) and standalone OpenStack environments.

Authentication:
  Automatically loads standard OS_* environment variables populated by:
    $ source ~/overcloudrc
    or
    $ source ~/myprojectrc

Usage:
  # View default colored Unicode tree
  python3 ovn_tenant_topology.py

  # Test without an active OpenStack connection
  python3 ovn_tenant_topology.py --mock

  # Formats: tree, ascii, table, json, dot, mermaid
  python3 ovn_tenant_topology.py --format ascii
  python3 ovn_tenant_topology.py --format table
  python3 ovn_tenant_topology.py --format json
  python3 ovn_tenant_topology.py --format dot | dot -Tsvg -o topology.svg

  # Target specific project (with admin credentials)
  python3 ovn_tenant_topology.py --project demo-project

  # Self-signed Director SSL endpoint
  python3 ovn_tenant_topology.py --insecure
"""

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass, field
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# Domain Exceptions
# ==============================================================================

class TopologyError(Exception):
    """Base exception for topology errors."""
    pass


class OpenStackSDKMissingError(TopologyError):
    """Raised when openstacksdk is not installed."""
    pass


class ConnectionConfigError(TopologyError):
    """Raised when openstack connection configuration fails."""
    pass


class ProjectNotFoundError(TopologyError):
    """Raised when a specified project cannot be found."""
    pass


class TopologyQueryError(TopologyError):
    """Raised when an OpenStack API query fails."""
    pass


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class SubnetInfo:
    id: str
    name: str
    cidr: str
    gateway_ip: Optional[str] = None
    enable_dhcp: bool = True
    allocation_pools: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class PortInfo:
    id: str
    name: str
    mac_address: str
    fixed_ips: List[Dict[str, str]] = field(default_factory=list)
    device_id: str = ""
    device_owner: str = ""
    status: str = "ACTIVE"
    floating_ip: Optional[str] = None
    security_groups: List[str] = field(default_factory=list)


@dataclass
class ServerInfo:
    id: str
    name: str
    status: str
    task_state: Optional[str] = None
    ports: List[PortInfo] = field(default_factory=list)


@dataclass
class NetworkInfo:
    id: str
    name: str
    status: str
    is_external: bool = False
    mtu: Optional[int] = None
    subnets: List[SubnetInfo] = field(default_factory=list)
    ports: List[PortInfo] = field(default_factory=list)
    network_type: Optional[str] = None
    segmentation_id: Optional[int] = None

    @property
    def ovn_logical_switch(self) -> str:
        return f"neutron-{self.id}"


@dataclass
class RouterInterface:
    port_id: str
    subnet_id: str
    ip_address: str
    mac_address: str
    network_id: str


@dataclass
class RouterInfo:
    id: str
    name: str
    status: str
    admin_state_up: bool = True
    external_network_id: Optional[str] = None
    external_gateway_ip: Optional[str] = None
    enable_snat: bool = True
    interfaces: List[RouterInterface] = field(default_factory=list)

    @property
    def ovn_logical_router(self) -> str:
        return f"neutron-{self.id}"


@dataclass
class Topology:
    project_id: str
    project_name: str
    auth_url: str
    region_name: str
    external_networks: Dict[str, NetworkInfo] = field(default_factory=dict)
    routers: Dict[str, RouterInfo] = field(default_factory=dict)
    tenant_networks: Dict[str, NetworkInfo] = field(default_factory=dict)
    servers: Dict[str, ServerInfo] = field(default_factory=dict)
    floating_ips: List[Dict[str, Any]] = field(default_factory=list)
    isolated_networks: Dict[str, NetworkInfo] = field(default_factory=dict)


# ==============================================================================
# Terminal Color & Styling Utilities
# ==============================================================================

class Colors:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _c(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self._c("1", text)

    def cyan(self, text: str) -> str:
        return self._c("36", text)

    def bold_cyan(self, text: str) -> str:
        return self._c("1;36", text)

    def blue(self, text: str) -> str:
        return self._c("34", text)

    def bold_blue(self, text: str) -> str:
        return self._c("1;34", text)

    def green(self, text: str) -> str:
        return self._c("32", text)

    def bold_green(self, text: str) -> str:
        return self._c("1;32", text)

    def yellow(self, text: str) -> str:
        return self._c("33", text)

    def bold_yellow(self, text: str) -> str:
        return self._c("1;33", text)

    def red(self, text: str) -> str:
        return self._c("31", text)

    def bold_red(self, text: str) -> str:
        return self._c("1;31", text)

    def magenta(self, text: str) -> str:
        return self._c("35", text)

    def bold_magenta(self, text: str) -> str:
        return self._c("1;35", text)

    def dim(self, text: str) -> str:
        return self._c("2", text)

    def status_badge(self, status: str) -> str:
        st = status.upper()
        if st in ("ACTIVE", "UP", "ONLINE"):
            return self.green(f"[{st}]")
        elif st in ("SHUTOFF", "DOWN", "STOPPED"):
            return self.dim(f"[{st}]")
        elif st in ("BUILD", "PENDING", "RESIZING"):
            return self.yellow(f"[{st}]")
        elif st in ("ERROR", "FAILED"):
            return self.bold_red(f"[{st}]")
        return self.dim(f"[{st}]")


# ==============================================================================
# Helper Sanitizers & Escaping
# ==============================================================================

def _safe_id(prefix: str, raw_id: str) -> str:
    """Creates a syntax-safe identifier for Graphviz DOT and Mermaid."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw_id))
    return f"{prefix}_{sanitized}"


def _dot_escape(text: str) -> str:
    """Escapes strings for Graphviz DOT label attributes."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _mermaid_escape(text: str) -> str:
    """Escapes strings for Mermaid node labels."""
    return str(text).replace('"', "'")


# ==============================================================================
# Topology Graph Engine
# ==============================================================================

def build_topology_from_data(
    raw_networks: List[Dict[str, Any]],
    raw_subnets: List[Dict[str, Any]],
    raw_routers: List[Dict[str, Any]],
    raw_ports: List[Dict[str, Any]],
    raw_servers: List[Dict[str, Any]],
    raw_floating_ips: List[Dict[str, Any]],
    project_id: str,
    project_name: str,
    auth_url: str = "",
    region_name: str = "",
    all_external: bool = False,
) -> Topology:
    """Reconciles OpenStack resource lists into a connected topology graph."""

    # 1. Index Subnets by ID
    subnets_by_id: Dict[str, SubnetInfo] = {}
    subnets_by_net: Dict[str, List[SubnetInfo]] = defaultdict(list)
    for sn in raw_subnets:
        s_obj = SubnetInfo(
            id=sn.get("id", ""),
            name=sn.get("name") or sn.get("id", "")[:8],
            cidr=sn.get("cidr", ""),
            gateway_ip=sn.get("gateway_ip"),
            enable_dhcp=sn.get("enable_dhcp", True),
            allocation_pools=sn.get("allocation_pools", []),
        )
        subnets_by_id[s_obj.id] = s_obj
        subnets_by_net[sn.get("network_id", "")].append(s_obj)

    # 2. Index Floating IPs by fixed port ID
    fip_by_port: Dict[str, str] = {}
    for fip in raw_floating_ips:
        p_id = fip.get("port_id")
        fip_addr = fip.get("floating_ip_address")
        if p_id and fip_addr:
            fip_by_port[p_id] = fip_addr

    # 3. Categorize Networks (External vs. Tenant)
    ext_networks: Dict[str, NetworkInfo] = {}
    tenant_networks: Dict[str, NetworkInfo] = {}

    for net in raw_networks:
        n_id = net.get("id", "")
        is_ext = bool(net.get("router:external") or net.get("is_router_external"))
        n_obj = NetworkInfo(
            id=n_id,
            name=net.get("name") or n_id[:8],
            status=net.get("status", "ACTIVE"),
            is_external=is_ext,
            mtu=net.get("mtu"),
            subnets=subnets_by_net.get(n_id, []),
            network_type=net.get("provider:network_type"),
            segmentation_id=net.get("provider:segmentation_id"),
        )
        if is_ext:
            ext_networks[n_id] = n_obj
        else:
            tenant_networks[n_id] = n_obj

    # 4. Process Ports
    ports_by_device: Dict[str, List[PortInfo]] = defaultdict(list)
    router_interface_ports: Dict[str, List[RouterInterface]] = defaultdict(list)

    for p in raw_ports:
        p_id = p.get("id", "")
        dev_id = p.get("device_id", "")
        dev_owner = p.get("device_owner", "")
        fixed_ips = p.get("fixed_ips", [])
        status = p.get("status", "ACTIVE")
        mac = p.get("mac_address", "")
        net_id = p.get("network_id", "")
        sec_groups = p.get("security_groups", [])

        port_obj = PortInfo(
            id=p_id,
            name=p.get("name") or p_id[:8],
            mac_address=mac,
            fixed_ips=fixed_ips,
            device_id=dev_id,
            device_owner=dev_owner,
            status=status,
            floating_ip=fip_by_port.get(p_id),
            security_groups=sec_groups,
        )

        # Attach port to its network if known
        if net_id in tenant_networks:
            tenant_networks[net_id].ports.append(port_obj)
        elif net_id in ext_networks:
            ext_networks[net_id].ports.append(port_obj)

        # Index router interface ports
        if dev_owner in (
            "network:router_interface",
            "network:router_interface_distributed",
            "network:ha_router_replicated_interface",
        ):
            for f_ip in fixed_ips:
                r_iface = RouterInterface(
                    port_id=p_id,
                    subnet_id=f_ip.get("subnet_id", ""),
                    ip_address=f_ip.get("ip_address", ""),
                    mac_address=mac,
                    network_id=net_id,
                )
                router_interface_ports[dev_id].append(r_iface)

        # Index compute / server ports
        if dev_id:
            ports_by_device[dev_id].append(port_obj)

    # 5. Process Routers
    routers: Dict[str, RouterInfo] = {}
    subnets_connected_to_routers: Set[str] = set()

    for r in raw_routers:
        r_id = r.get("id", "")
        gw_info = r.get("external_gateway_info") or {}
        ext_net_id = gw_info.get("network_id")
        snat = gw_info.get("enable_snat", True)

        ext_gw_ip = None
        ext_fixed_ips = gw_info.get("external_fixed_ips", [])
        if ext_fixed_ips:
            ext_gw_ip = ext_fixed_ips[0].get("ip_address")

        ifaces = router_interface_ports.get(r_id, [])
        for iface in ifaces:
            subnets_connected_to_routers.add(iface.subnet_id)

        r_obj = RouterInfo(
            id=r_id,
            name=r.get("name") or r_id[:8],
            status=r.get("status", "ACTIVE"),
            admin_state_up=r.get("admin_state_up", True),
            external_network_id=ext_net_id,
            external_gateway_ip=ext_gw_ip,
            enable_snat=snat,
            interfaces=ifaces,
        )
        routers[r_id] = r_obj

    # 5b. Filter external networks to only connected/active ones unless all_external is True
    if not all_external and ext_networks:
        connected_ext_ids: Set[str] = {
            r.external_network_id for r in routers.values() if r.external_network_id
        }
        for fip in raw_floating_ips:
            fip_net_id = fip.get("floating_network_id")
            if fip_net_id:
                connected_ext_ids.add(fip_net_id)

        filtered_ext = {
            nid: net for nid, net in ext_networks.items() if nid in connected_ext_ids
        }
        if filtered_ext:
            ext_networks = filtered_ext

    # 6. Process Compute Instances (Servers)
    servers: Dict[str, ServerInfo] = {}
    for s in raw_servers:
        s_id = s.get("id", "")
        s_ports = ports_by_device.get(s_id, [])
        s_obj = ServerInfo(
            id=s_id,
            name=s.get("name") or s_id[:8],
            status=s.get("status", "ACTIVE"),
            task_state=s.get("OS-EXT-STS:task_state") or s.get("task_state"),
            ports=s_ports,
        )
        servers[s_id] = s_obj

    # 7. Identify Isolated / Unrouted Networks
    isolated_networks: Dict[str, NetworkInfo] = {}
    for net_id, net in tenant_networks.items():
        is_routed = any(sn.id in subnets_connected_to_routers for sn in net.subnets)
        if not is_routed:
            isolated_networks[net_id] = net

    return Topology(
        project_id=project_id,
        project_name=project_name,
        auth_url=auth_url,
        region_name=region_name,
        external_networks=ext_networks,
        routers=routers,
        tenant_networks=tenant_networks,
        servers=servers,
        floating_ips=raw_floating_ips,
        isolated_networks=isolated_networks,
    )


# ==============================================================================
# Visual Renderers
# ==============================================================================

def render_tree(topo: Topology, colors: Colors, ovn_details: bool = False, verbose: bool = False) -> str:
    """Renders topology as a colored Unicode tree."""
    lines: List[str] = []

    # Header
    lines.append(colors.bold_cyan("=" * 78))
    lines.append(
        f"{colors.bold_cyan('OpenStack Tenant Network Topology')} | "
        f"Project: {colors.bold_yellow(topo.project_name)} ({colors.dim(topo.project_id)})"
    )
    if topo.region_name or topo.auth_url:
        reg = topo.region_name or "N/A"
        auth = topo.auth_url or "N/A"
        lines.append(f"{colors.dim('Region:')} {reg} | {colors.dim('Auth URL:')} {auth}")
    lines.append(colors.bold_cyan("=" * 78))
    lines.append("")

    # Map subnets to instances (grouping IPs per port on that subnet)
    subnet_vms: Dict[str, List[Tuple[ServerInfo, PortInfo, List[str]]]] = defaultdict(list)
    for s in topo.servers.values():
        for p in s.ports:
            ips_by_sn: Dict[str, List[str]] = defaultdict(list)
            for fip in p.fixed_ips:
                sn_id = fip.get("subnet_id", "")
                ip_addr = fip.get("ip_address", "")
                if sn_id and ip_addr:
                    ips_by_sn[sn_id].append(ip_addr)
            for sn_id, ip_list in ips_by_sn.items():
                subnet_vms[sn_id].append((s, p, ip_list))

    # Map External Network -> Routers
    ext_routers: Dict[str, List[RouterInfo]] = defaultdict(list)
    unattached_routers: List[RouterInfo] = []

    for r in topo.routers.values():
        if r.external_network_id and r.external_network_id in topo.external_networks:
            ext_routers[r.external_network_id].append(r)
        else:
            unattached_routers.append(r)

    # 1. Render External Networks & Connected Routers
    if topo.external_networks:
        for ext_id, ext_net in topo.external_networks.items():
            subnets_str = ", ".join(sn.cidr for sn in ext_net.subnets) or "No subnets"
            lines.append(
                f"🌐 {colors.bold_blue('External Network:')} {colors.bold(ext_net.name)} "
                f"({subnets_str}) {colors.status_badge(ext_net.status)}"
            )

            c_routers = ext_routers.get(ext_id, [])
            if not c_routers:
                lines.append(f"    └── {colors.dim('(no tenant routers attached)')}")
            for r_idx, r in enumerate(c_routers):
                is_last_r = (r_idx == len(c_routers) - 1)
                r_prefix = "└── " if is_last_r else "├── "
                r_indent = "    " if is_last_r else "│   "

                gw_str = f"Gateway: {r.external_gateway_ip}" if r.external_gateway_ip else "No Gateway IP"
                snat_str = "SNAT: enabled" if r.enable_snat else "SNAT: disabled"
                ovn_lr_str = f" [{colors.magenta('OVN LR:')} {r.ovn_logical_router}]" if ovn_details else ""

                lines.append(
                    f"{r_prefix}🔀 {colors.bold_yellow('Router:')} {colors.bold(r.name)} "
                    f"{colors.status_badge(r.status)} ({gw_str} | {snat_str}){ovn_lr_str}"
                )

                ifaces = r.interfaces
                for i_idx, iface in enumerate(ifaces):
                    is_last_i = (i_idx == len(ifaces) - 1)
                    i_prefix = r_indent + ("└── " if is_last_i else "├── ")
                    i_indent = r_indent + ("    " if is_last_i else "│   ")

                    t_net = topo.tenant_networks.get(iface.network_id)
                    net_name = t_net.name if t_net else iface.network_id[:8]
                    sn_obj = None
                    if t_net:
                        for s_candidate in t_net.subnets:
                            if s_candidate.id == iface.subnet_id:
                                sn_obj = s_candidate
                                break

                    cidr = sn_obj.cidr if sn_obj else "unknown CIDR"
                    gw_ip = sn_obj.gateway_ip if sn_obj else iface.ip_address

                    encap_str = ""
                    if t_net and t_net.network_type:
                        seg = f":{t_net.segmentation_id}" if t_net.segmentation_id else ""
                        encap_str = f" [{t_net.network_type}{seg}]"

                    ovn_ls_str = (
                        f" [{colors.magenta('OVN LS:')} {t_net.ovn_logical_switch}]"
                        if (ovn_details and t_net)
                        else ""
                    )

                    lines.append(
                        f"{i_prefix}🔌 {colors.bold_green('Subnet:')} {net_name} / {cidr} "
                        f"(GW: {gw_ip}){encap_str}{ovn_ls_str}"
                    )

                    vms_in_sn = subnet_vms.get(iface.subnet_id, [])
                    if not vms_in_sn:
                        lines.append(f"{i_indent}└── {colors.dim('(no compute instances attached)')}")
                    else:
                        for vm_idx, (vm, port, ips) in enumerate(vms_in_sn):
                            is_last_vm = (vm_idx == len(vms_in_sn) - 1)
                            vm_prefix = i_indent + ("└── " if is_last_vm else "├── ")

                            fip_str = ""
                            if port.floating_ip:
                                fip_str = f" ➜ 🌐 {colors.bold_cyan('Floating IP:')} {colors.bold(port.floating_ip)}"

                            details_parts = []
                            if verbose and port.mac_address:
                                details_parts.append(f"MAC: {port.mac_address}")
                            if verbose and port.security_groups:
                                details_parts.append(f"SG: {', '.join(port.security_groups)}")
                            if ovn_details and port.id:
                                details_parts.append(f"LSP: {port.id[:8]}")
                            details_str = f" ({colors.dim(', '.join(details_parts))})" if details_parts else ""

                            status_str = colors.status_badge(vm.status)
                            if vm.task_state:
                                status_str += f" {colors.yellow(f'({vm.task_state})')}"

                            ips_str = ", ".join(ips)
                            lines.append(
                                f"{vm_prefix}🖥️  {colors.bold(vm.name)} {status_str} "
                                f"[{ips_str}]{details_str}{fip_str}"
                            )
            lines.append("")
    else:
        lines.append(colors.yellow("No External Networks found."))
        lines.append("")

    # 2. Render Unattached Routers
    if unattached_routers:
        lines.append(colors.bold_yellow("🔀 Internal / Disconnected Routers:"))
        for r_idx, r in enumerate(unattached_routers):
            is_last_r = (r_idx == len(unattached_routers) - 1)
            r_prefix = "└── " if is_last_r else "├── "
            r_indent = "    " if is_last_r else "│   "

            ovn_lr_str = f" [{colors.magenta('OVN LR:')} {r.ovn_logical_router}]" if ovn_details else ""
            lines.append(
                f"{r_prefix}🔀 {colors.bold_yellow('Router:')} {colors.bold(r.name)} "
                f"{colors.status_badge(r.status)} (No External Gateway){ovn_lr_str}"
            )
            for i_idx, iface in enumerate(r.interfaces):
                is_last_i = (i_idx == len(r.interfaces) - 1)
                i_prefix = r_indent + ("└── " if is_last_i else "├── ")
                i_indent = r_indent + ("    " if is_last_i else "│   ")

                t_net = topo.tenant_networks.get(iface.network_id)
                net_name = t_net.name if t_net else iface.network_id[:8]
                sn_obj = None
                if t_net:
                    for s_candidate in t_net.subnets:
                        if s_candidate.id == iface.subnet_id:
                            sn_obj = s_candidate
                            break

                cidr = sn_obj.cidr if sn_obj else "unknown CIDR"
                gw_ip = sn_obj.gateway_ip if sn_obj else iface.ip_address

                encap_str = ""
                if t_net and t_net.network_type:
                    seg = f":{t_net.segmentation_id}" if t_net.segmentation_id else ""
                    encap_str = f" [{t_net.network_type}{seg}]"

                ovn_ls_str = (
                    f" [{colors.magenta('OVN LS:')} {t_net.ovn_logical_switch}]"
                    if (ovn_details and t_net)
                    else ""
                )

                lines.append(
                    f"{i_prefix}🔌 {colors.bold_green('Subnet:')} {net_name} / {cidr} "
                    f"(GW: {gw_ip}){encap_str}{ovn_ls_str}"
                )

                vms_in_sn = subnet_vms.get(iface.subnet_id, [])
                if not vms_in_sn:
                    lines.append(f"{i_indent}└── {colors.dim('(no compute instances attached)')}")
                else:
                    for vm_idx, (vm, port, ips) in enumerate(vms_in_sn):
                        is_last_vm = (vm_idx == len(vms_in_sn) - 1)
                        vm_prefix = i_indent + ("└── " if is_last_vm else "├── ")

                        fip_str = ""
                        if port.floating_ip:
                            fip_str = f" ➜ 🌐 {colors.bold_cyan('Floating IP:')} {colors.bold(port.floating_ip)}"

                        details_parts = []
                        if verbose and port.mac_address:
                            details_parts.append(f"MAC: {port.mac_address}")
                        if verbose and port.security_groups:
                            details_parts.append(f"SG: {', '.join(port.security_groups)}")
                        if ovn_details and port.id:
                            details_parts.append(f"LSP: {port.id[:8]}")
                        details_str = f" ({colors.dim(', '.join(details_parts))})" if details_parts else ""

                        status_str = colors.status_badge(vm.status)
                        if vm.task_state:
                            status_str += f" {colors.yellow(f'({vm.task_state})')}"

                        ips_str = ", ".join(ips)
                        lines.append(
                            f"{vm_prefix}🖥️  {colors.bold(vm.name)} {status_str} "
                            f"[{ips_str}]{details_str}{fip_str}"
                        )
        lines.append("")

    # 3. Render Isolated / Unrouted Networks
    if topo.isolated_networks:
        lines.append(colors.bold_magenta("📦 Isolated / Unrouted Networks (No Router Interface):"))
        for net_id, net in topo.isolated_networks.items():
            subnets_str = ", ".join(f"{sn.name} ({sn.cidr})" for sn in net.subnets) or "No Subnets"
            encap_str = ""
            if net.network_type:
                seg = f":{net.segmentation_id}" if net.segmentation_id else ""
                encap_str = f" [{net.network_type}{seg}]"
            ovn_ls_str = f" [{colors.magenta('OVN LS:')} {net.ovn_logical_switch}]" if ovn_details else ""
            lines.append(
                f"└── 🔌 {colors.bold_magenta('Network:')} {net.name} {colors.status_badge(net.status)} "
                f"[{subnets_str}]{encap_str}{ovn_ls_str}"
            )

            has_vms = False
            for sn in net.subnets:
                vms_in_sn = subnet_vms.get(sn.id, [])
                for vm, port, ips in vms_in_sn:
                    has_vms = True
                    status_str = colors.status_badge(vm.status)
                    if vm.task_state:
                        status_str += f" {colors.yellow(f'({vm.task_state})')}"
                    ips_str = ", ".join(ips)
                    details_parts = []
                    if verbose and port.mac_address:
                        details_parts.append(f"MAC: {port.mac_address}")
                    if verbose and port.security_groups:
                        details_parts.append(f"SG: {', '.join(port.security_groups)}")
                    details_str = f" ({colors.dim(', '.join(details_parts))})" if details_parts else ""

                    lines.append(
                        f"    └── 🖥️  {colors.bold(vm.name)} {status_str} [{ips_str}]{details_str}"
                    )
            if not has_vms:
                lines.append(f"    └── {colors.dim('(no compute instances attached)')}")
        lines.append("")

    return "\n".join(lines)


def render_ascii(topo: Topology, ovn_details: bool = False, verbose: bool = False) -> str:
    """Renders topology as clean plain-text ASCII suitable for logs and scripts."""
    lines: List[str] = []
    lines.append(f"=== OpenStack Tenant Topology: {topo.project_name} ({topo.project_id}) ===")
    if topo.region_name or topo.auth_url:
        reg = topo.region_name or "N/A"
        auth = topo.auth_url or "N/A"
        lines.append(f"Region: {reg} | Auth URL: {auth}")
    lines.append("")

    subnet_vms: Dict[str, List[Tuple[ServerInfo, PortInfo, List[str]]]] = defaultdict(list)
    for s in topo.servers.values():
        for p in s.ports:
            ips_by_sn: Dict[str, List[str]] = defaultdict(list)
            for fip in p.fixed_ips:
                sn_id = fip.get("subnet_id", "")
                ip_addr = fip.get("ip_address", "")
                if sn_id and ip_addr:
                    ips_by_sn[sn_id].append(ip_addr)
            for sn_id, ip_list in ips_by_sn.items():
                subnet_vms[sn_id].append((s, p, ip_list))

    unattached_routers: List[RouterInfo] = []
    for r in topo.routers.values():
        if not r.external_network_id or r.external_network_id not in topo.external_networks:
            unattached_routers.append(r)

    for ext_net in topo.external_networks.values():
        subnets_str = ", ".join(sn.cidr for sn in ext_net.subnets) or "no-subnets"
        lines.append(f"[EXT: {ext_net.name}] ({subnets_str}) [{ext_net.status}]")

        routers = [r for r in topo.routers.values() if r.external_network_id == ext_net.id]
        for r_idx, r in enumerate(routers):
            is_last_r = (r_idx == len(routers) - 1)
            r_prefix = "  \\-- " if is_last_r else "  +-- "
            r_indent = "      " if is_last_r else "  |   "

            gw = r.external_gateway_ip or "no-gw"
            snat = "snat:on" if r.enable_snat else "snat:off"
            ovn_lr = f" [OVN LR: {r.ovn_logical_router}]" if ovn_details else ""
            lines.append(f"{r_prefix}[ROUTER: {r.name}] [{r.status}] (GW: {gw}, {snat}){ovn_lr}")

            for i_idx, iface in enumerate(r.interfaces):
                is_last_i = (i_idx == len(r.interfaces) - 1)
                i_prefix = r_indent + ("\\-- " if is_last_i else "+-- ")
                i_indent = r_indent + ("    " if is_last_i else "|   ")

                t_net = topo.tenant_networks.get(iface.network_id)
                net_name = t_net.name if t_net else iface.network_id[:8]
                ovn_ls = f" [OVN LS: {t_net.ovn_logical_switch}]" if (ovn_details and t_net) else ""
                lines.append(f"{i_prefix}[SUBNET: {net_name}] (IP: {iface.ip_address}){ovn_ls}")

                vms = subnet_vms.get(iface.subnet_id, [])
                for vm_idx, (vm, port, ips) in enumerate(vms):
                    is_last_vm = (vm_idx == len(vms) - 1)
                    vm_prefix = i_indent + ("\\-- " if is_last_vm else "+-- ")
                    fip_str = f" <=> [FIP: {port.floating_ip}]" if port.floating_ip else ""
                    mac_str = f" (MAC: {port.mac_address})" if verbose else ""
                    sg_str = f" (SG: {', '.join(port.security_groups)})" if (verbose and port.security_groups) else ""
                    task_str = f" ({vm.task_state})" if vm.task_state else ""
                    ips_str = ", ".join(ips)
                    lines.append(
                        f"{vm_prefix}[VM: {vm.name}] [{vm.status}{task_str}] (IP: {ips_str}){mac_str}{sg_str}{fip_str}"
                    )
        lines.append("")

    if unattached_routers:
        lines.append("[INTERNAL ROUTERS]")
        for r_idx, r in enumerate(unattached_routers):
            ovn_lr = f" [OVN LR: {r.ovn_logical_router}]" if ovn_details else ""
            lines.append(f"  +-- [ROUTER: {r.name}] [{r.status}] (No Gateway){ovn_lr}")
            for iface in r.interfaces:
                t_net = topo.tenant_networks.get(iface.network_id)
                net_name = t_net.name if t_net else iface.network_id[:8]
                ovn_ls = f" [OVN LS: {t_net.ovn_logical_switch}]" if (ovn_details and t_net) else ""
                lines.append(f"      +-- [SUBNET: {net_name}] (IP: {iface.ip_address}){ovn_ls}")
                vms = subnet_vms.get(iface.subnet_id, [])
                for vm, port, ips in vms:
                    ips_str = ", ".join(ips)
                    mac_str = f" (MAC: {port.mac_address})" if verbose else ""
                    task_str = f" ({vm.task_state})" if vm.task_state else ""
                    lines.append(f"          +-- [VM: {vm.name}] [{vm.status}{task_str}] (IP: {ips_str}){mac_str}")
        lines.append("")

    if topo.isolated_networks:
        lines.append("[ISOLATED NETWORKS]")
        for net in topo.isolated_networks.values():
            subnets_str = ", ".join(sn.cidr for sn in net.subnets)
            ovn_ls = f" [OVN LS: {net.ovn_logical_switch}]" if ovn_details else ""
            lines.append(f"  +-- [NET: {net.name}] ({subnets_str}){ovn_ls}")
            for sn in net.subnets:
                for vm, port, ips in subnet_vms.get(sn.id, []):
                    ips_str = ", ".join(ips)
                    mac_str = f" (MAC: {port.mac_address})" if verbose else ""
                    sg_str = f" (SG: {', '.join(port.security_groups)})" if (verbose and port.security_groups) else ""
                    task_str = f" ({vm.task_state})" if vm.task_state else ""
                    lines.append(f"      +-- [VM: {vm.name}] [{vm.status}{task_str}] (IP: {ips_str}){mac_str}{sg_str}")
        lines.append("")

    return "\n".join(lines)


def render_table(topo: Topology, colors: Colors, verbose: bool = False) -> str:
    """Renders topology as clean aligned ASCII tables (summary card and workload inventory)."""
    lines: List[str] = []

    # 1. Summary Card / Table
    lines.append(colors.bold_cyan("=" * 88))
    lines.append(
        f"{colors.bold_cyan('OpenStack Tenant Network Topology Overview')} | "
        f"Project: {colors.bold_yellow(topo.project_name)} ({colors.dim(topo.project_id)})"
    )
    if topo.region_name or topo.auth_url:
        reg = topo.region_name or "N/A"
        auth = topo.auth_url or "N/A"
        lines.append(f"{colors.dim('Region:')} {reg} | {colors.dim('Auth URL:')} {auth}")
    lines.append(colors.bold_cyan("=" * 88))
    lines.append("")

    # Summary Stats
    total_ext = len(topo.external_networks)
    total_routers = len(topo.routers)
    total_tenant_nets = len(topo.tenant_networks)
    total_isolated = len(topo.isolated_networks)
    total_subnets = sum(len(n.subnets) for n in topo.tenant_networks.values())
    total_servers = len(topo.servers)
    total_fips = len(topo.floating_ips)

    lines.append(colors.bold("RESOURCE SUMMARY:"))
    summary_headers = ["Ext Nets", "Routers", "Tenant Nets", "Isolated Nets", "Subnets", "Instances", "Floating IPs"]
    summary_values = [str(total_ext), str(total_routers), str(total_tenant_nets), str(total_isolated), str(total_subnets), str(total_servers), str(total_fips)]

    col_w = [max(len(h), len(v)) + 2 for h, v in zip(summary_headers, summary_values)]
    border = "+" + "+".join("-" * w for w in col_w) + "+"
    hdr_row = "|" + "|".join(f" {h.center(w - 2)} " for h, w in zip(summary_headers, col_w)) + "|"
    val_row = "|" + "|".join(f" {v.center(w - 2)} " for v, w in zip(summary_values, col_w)) + "|"

    lines.append(border)
    lines.append(hdr_row)
    lines.append(border)
    lines.append(val_row)
    lines.append(border)
    lines.append("")

    # 2. Workload / Compute Inventory Table
    lines.append(colors.bold("WORKLOAD INVENTORY & ROUTING PATH:"))

    # Map subnet to Router
    sn_to_router: Dict[str, RouterInfo] = {}
    for r in topo.routers.values():
        for iface in r.interfaces:
            sn_to_router[iface.subnet_id] = r

    # Map subnet ID to SubnetInfo & NetworkInfo
    sn_map: Dict[str, Tuple[NetworkInfo, SubnetInfo]] = {}
    for net in list(topo.tenant_networks.values()) + list(topo.external_networks.values()):
        for sn in net.subnets:
            sn_map[sn.id] = (net, sn)

    rows: List[List[str]] = []
    headers = ["Instance", "Status", "Fixed IP(s)", "Floating IP", "Network / CIDR", "Router Gateway"]
    if verbose:
        headers.extend(["MAC Address", "Security Groups"])

    for s in sorted(topo.servers.values(), key=lambda x: x.name):
        s_status = s.status + (f" ({s.task_state})" if s.task_state else "")
        if not s.ports:
            row = [s.name, s_status, "No Port", "-", "-", "None"]
            if verbose:
                row.extend(["-", "-"])
            rows.append(row)
            continue

        for p in s.ports:
            ips_by_sn: Dict[str, List[str]] = defaultdict(list)
            for fip in p.fixed_ips:
                ips_by_sn[fip.get("subnet_id", "")].append(fip.get("ip_address", ""))

            fip_val = p.floating_ip or "-"
            mac_val = p.mac_address or "-"
            sg_val = ", ".join(p.security_groups) or "-"

            if not ips_by_sn:
                row = [s.name, s_status, "No IP", fip_val, "Unassigned", "None"]
                if verbose:
                    row.extend([mac_val, sg_val])
                rows.append(row)
                continue

            for sn_id, ip_list in ips_by_sn.items():
                ip_val = ", ".join(ip_list)
                if sn_id in sn_map:
                    net_obj, sn_obj = sn_map[sn_id]
                    net_val = f"{net_obj.name} ({sn_obj.cidr})"
                else:
                    net_val = sn_id[:8] if sn_id else "-"

                r_obj = sn_to_router.get(sn_id)
                if r_obj:
                    r_val = f"{r_obj.name} (GW: {r_obj.external_gateway_ip or 'None'})"
                else:
                    r_val = "[Isolated]"

                row = [s.name, s_status, ip_val, fip_val, net_val, r_val]
                if verbose:
                    row.extend([mac_val, sg_val])
                rows.append(row)

    if not rows:
        lines.append("  (No compute instances found)")
    else:
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))
        widths = [w + 2 for w in widths]

        t_border = "+" + "+".join("-" * w for w in widths) + "+"
        t_hdr = "|" + "|".join(f" {h.center(w - 2)} " for h, w in zip(headers, widths)) + "|"

        lines.append(t_border)
        lines.append(t_hdr)
        lines.append(t_border)
        for row in rows:
            line_cells = []
            for i, cell in enumerate(row):
                if i in (1, 3):
                    line_cells.append(f" {cell.center(widths[i] - 2)} ")
                else:
                    line_cells.append(f" {cell.ljust(widths[i] - 2)} ")
            lines.append("|" + "|".join(line_cells) + "|")
        lines.append(t_border)

    lines.append("")
    return "\n".join(lines)


def render_json(topo: Topology) -> str:
    """Exports structured topology JSON (Horizon compatible)."""
    return json.dumps(asdict(topo), indent=2, default=str)


def render_dot(topo: Topology) -> str:
    """Generates Graphviz DOT diagram."""
    lines: List[str] = [
        "digraph OpenStackTopology {",
        "  rankdir=TB;",
        "  node [fontname=\"Helvetica\", shape=box, style=\"filled,rounded\", fontsize=10];",
        "  edge [fontname=\"Helvetica\", fontsize=9];",
        "",
    ]

    for ext_id, ext in topo.external_networks.items():
        node_id = _safe_id("net", ext_id)
        label = f"{_dot_escape(ext.name)}\\n[External Network]"
        lines.append(f'  "{node_id}" [label="{label}", fillcolor="#D0E1FD", color="#4285F4", shape=ellipse];')

    for r_id, r in topo.routers.items():
        node_id = _safe_id("router", r_id)
        label = f"Router: {_dot_escape(r.name)}\\nGW: {r.external_gateway_ip or 'None'}\\n[{r.status}]"
        lines.append(f'  "{node_id}" [label="{label}", fillcolor="#FFF3C4", color="#F9AB00"];')
        if r.external_network_id:
            ext_node = _safe_id("net", r.external_network_id)
            lines.append(f'  "{ext_node}" -> "{node_id}" [label="gateway"];')

    for net_id, net in topo.tenant_networks.items():
        for sn in net.subnets:
            sn_node = _safe_id("subnet", sn.id)
            label = f"Subnet: {_dot_escape(net.name)}\\n{sn.cidr}\\nGW: {sn.gateway_ip}"
            lines.append(f'  "{sn_node}" [label="{label}", fillcolor="#CEEAD6", color="#34A853"];')

    for r in topo.routers.values():
        r_node = _safe_id("router", r.id)
        for iface in r.interfaces:
            sn_node = _safe_id("subnet", iface.subnet_id)
            lines.append(f'  "{r_node}" -> "{sn_node}" [label="{iface.ip_address}"];')

    for s_id, s in topo.servers.items():
        vm_node = _safe_id("vm", s_id)
        fill = "#FFFFFF" if s.status == "ACTIVE" else "#E8EAED"
        status_label = s.status if not s.task_state else f"{s.status} ({s.task_state})"
        label = f"VM: {_dot_escape(s.name)}\\n[{status_label}]"
        lines.append(f'  "{vm_node}" [label="{label}", fillcolor="{fill}", color="#5F6368"];')
        for p in s.ports:
            for fip in p.fixed_ips:
                sn_node = _safe_id("subnet", fip.get("subnet_id", ""))
                edge_label = fip.get("ip_address", "")
                if p.floating_ip:
                    edge_label += f"\\n(FIP: {p.floating_ip})"
                lines.append(f'  "{sn_node}" -> "{vm_node}" [label="{edge_label}"];')

    lines.append("}")
    return "\n".join(lines)


def render_mermaid(topo: Topology) -> str:
    """Generates Mermaid diagram markup."""
    lines: List[str] = [
        "```mermaid",
        "graph TD",
    ]

    for ext_id, ext in topo.external_networks.items():
        ext_node = _safe_id("EXT", ext_id)
        lines.append(f'    {ext_node}["🌐 External: {_mermaid_escape(ext.name)}"]')

    declared_subnets: Set[str] = set()

    for r_id, r in topo.routers.items():
        r_node = _safe_id("RTR", r_id)
        gw = f" ({r.external_gateway_ip})" if r.external_gateway_ip else ""
        lines.append(f'    {r_node}["🔀 Router: {_mermaid_escape(r.name)}{gw}"]')
        if r.external_network_id and r.external_network_id in topo.external_networks:
            ext_node = _safe_id("EXT", r.external_network_id)
            lines.append(f"    {ext_node} --> {r_node}")

        for iface in r.interfaces:
            sn_node = _safe_id("SUB", iface.subnet_id)
            if sn_node not in declared_subnets:
                t_net = topo.tenant_networks.get(iface.network_id)
                net_name = t_net.name if t_net else iface.network_id[:8]
                sn_obj = None
                if t_net:
                    for s_candidate in t_net.subnets:
                        if s_candidate.id == iface.subnet_id:
                            sn_obj = s_candidate
                            break
                cidr_str = f" ({sn_obj.cidr})" if sn_obj else ""
                lines.append(f'    {sn_node}["🔌 Subnet: {_mermaid_escape(net_name)}{cidr_str}"]')
                declared_subnets.add(sn_node)
            lines.append(f"    {r_node} --> {sn_node}")

    # Declare any remaining subnets (e.g., isolated / unrouted networks)
    for net_id, net in topo.tenant_networks.items():
        for sn in net.subnets:
            sn_node = _safe_id("SUB", sn.id)
            if sn_node not in declared_subnets:
                lines.append(f'    {sn_node}["🔌 Subnet: {_mermaid_escape(net.name)} ({sn.cidr})"]')
                declared_subnets.add(sn_node)

    for s_id, s in topo.servers.items():
        vm_node = _safe_id("VM", s_id)
        status_str = f"{s.status}" + (f" ({s.task_state})" if s.task_state else "")
        lines.append(f'    {vm_node}["🖥️  VM: {_mermaid_escape(s.name)} ({status_str})"]')
        for p in s.ports:
            for fip in p.fixed_ips:
                sn_node = _safe_id("SUB", fip.get("subnet_id", ""))
                ip = fip.get("ip_address", "")
                fip_str = f" / FIP: {p.floating_ip}" if p.floating_ip else ""
                lines.append(f'    {sn_node} -->|"{ip}{fip_str}"| {vm_node}')

    lines.append("```")
    return "\n".join(lines)


# ==============================================================================
# Mock Data Generator (for offline verification)
# ==============================================================================

def get_mock_topology(all_external: bool = False) -> Topology:
    """Creates a realistic sample topology matching Horizon test fixtures."""
    raw_networks = [
        {
            "id": "ext-net-uuid-001",
            "name": "public",
            "status": "ACTIVE",
            "router:external": True,
            "mtu": 1500,
            "provider:network_type": "flat",
        },
        {
            "id": "tenant-net-uuid-101",
            "name": "app-internal-net",
            "status": "ACTIVE",
            "router:external": False,
            "mtu": 1442,
            "provider:network_type": "geneve",
            "provider:segmentation_id": 1024,
        },
        {
            "id": "tenant-net-uuid-102",
            "name": "db-backend-net",
            "status": "ACTIVE",
            "router:external": False,
            "mtu": 1442,
            "provider:network_type": "geneve",
            "provider:segmentation_id": 1025,
        },
        {
            "id": "tenant-net-uuid-103",
            "name": "isolated-backup-net",
            "status": "ACTIVE",
            "router:external": False,
            "mtu": 1442,
            "provider:network_type": "geneve",
            "provider:segmentation_id": 1026,
        },
        {
            "id": "ext-net-uuid-unused-002",
            "name": "public-alt-vlan200",
            "status": "ACTIVE",
            "router:external": True,
            "mtu": 1500,
            "provider:network_type": "vlan",
            "provider:segmentation_id": 200,
        },
    ]

    raw_subnets = [
        {
            "id": "ext-sn-uuid-001",
            "name": "public-subnet",
            "network_id": "ext-net-uuid-001",
            "cidr": "192.168.1.0/24",
            "gateway_ip": "192.168.1.1",
            "enable_dhcp": False,
        },
        {
            "id": "ext-sn-uuid-002",
            "name": "alt-public-subnet",
            "network_id": "ext-net-uuid-unused-002",
            "cidr": "10.200.0.0/24",
            "gateway_ip": "10.200.0.1",
            "enable_dhcp": False,
        },
        {
            "id": "app-sn-uuid-101",
            "name": "app-subnet",
            "network_id": "tenant-net-uuid-101",
            "cidr": "10.0.1.0/24",
            "gateway_ip": "10.0.1.1",
            "enable_dhcp": True,
        },
        {
            "id": "db-sn-uuid-102",
            "name": "db-subnet",
            "network_id": "tenant-net-uuid-102",
            "cidr": "10.0.2.0/24",
            "gateway_ip": "10.0.2.1",
            "enable_dhcp": True,
        },
        {
            "id": "backup-sn-uuid-103",
            "name": "backup-subnet",
            "network_id": "tenant-net-uuid-103",
            "cidr": "172.16.50.0/24",
            "gateway_ip": "172.16.50.1",
            "enable_dhcp": True,
        },
    ]

    raw_routers = [
        {
            "id": "router-uuid-001",
            "name": "tenant-gateway-router",
            "status": "ACTIVE",
            "admin_state_up": True,
            "external_gateway_info": {
                "network_id": "ext-net-uuid-001",
                "enable_snat": True,
                "external_fixed_ips": [{"ip_address": "192.168.1.105", "subnet_id": "ext-sn-uuid-001"}],
            },
        }
    ]

    raw_ports = [
        {
            "id": "port-rtr-gw-01",
            "name": "router-gw",
            "network_id": "ext-net-uuid-001",
            "device_id": "router-uuid-001",
            "device_owner": "network:router_gateway",
            "fixed_ips": [{"ip_address": "192.168.1.105", "subnet_id": "ext-sn-uuid-001"}],
            "mac_address": "fa:16:3e:00:00:01",
            "status": "ACTIVE",
        },
        {
            "id": "port-rtr-iface-01",
            "name": "rtr-iface-app",
            "network_id": "tenant-net-uuid-101",
            "device_id": "router-uuid-001",
            "device_owner": "network:router_interface",
            "fixed_ips": [{"ip_address": "10.0.1.1", "subnet_id": "app-sn-uuid-101"}],
            "mac_address": "fa:16:3e:10:01:01",
            "status": "ACTIVE",
        },
        {
            "id": "port-rtr-iface-02",
            "name": "rtr-iface-db",
            "network_id": "tenant-net-uuid-102",
            "device_id": "router-uuid-001",
            "device_owner": "network:router_interface",
            "fixed_ips": [{"ip_address": "10.0.2.1", "subnet_id": "db-sn-uuid-102"}],
            "mac_address": "fa:16:3e:10:02:01",
            "status": "ACTIVE",
        },
        {
            "id": "port-vm-01",
            "name": "web-frontend-01-port",
            "network_id": "tenant-net-uuid-101",
            "device_id": "server-uuid-001",
            "device_owner": "compute:nova",
            "fixed_ips": [{"ip_address": "10.0.1.15", "subnet_id": "app-sn-uuid-101"}],
            "mac_address": "fa:16:3e:20:01:15",
            "status": "ACTIVE",
            "security_groups": ["secgroup-web", "secgroup-default"],
        },
        {
            "id": "port-vm-02",
            "name": "web-frontend-02-port",
            "network_id": "tenant-net-uuid-101",
            "device_id": "server-uuid-002",
            "device_owner": "compute:nova",
            "fixed_ips": [{"ip_address": "10.0.1.16", "subnet_id": "app-sn-uuid-101"}],
            "mac_address": "fa:16:3e:20:01:16",
            "status": "ACTIVE",
            "security_groups": ["secgroup-web"],
        },
        {
            "id": "port-vm-03",
            "name": "db-cluster-01-port",
            "network_id": "tenant-net-uuid-102",
            "device_id": "server-uuid-003",
            "device_owner": "compute:nova",
            "fixed_ips": [{"ip_address": "10.0.2.20", "subnet_id": "db-sn-uuid-102"}],
            "mac_address": "fa:16:3e:20:02:20",
            "status": "ACTIVE",
            "security_groups": ["secgroup-db"],
        },
        {
            "id": "port-vm-04",
            "name": "backup-runner-port",
            "network_id": "tenant-net-uuid-103",
            "device_id": "server-uuid-004",
            "device_owner": "compute:nova",
            "fixed_ips": [{"ip_address": "172.16.50.5", "subnet_id": "backup-sn-uuid-103"}],
            "mac_address": "fa:16:3e:30:50:05",
            "status": "ACTIVE",
            "security_groups": ["secgroup-backup"],
        },
    ]

    raw_servers = [
        {"id": "server-uuid-001", "name": "web-frontend-01", "status": "ACTIVE", "task_state": None},
        {"id": "server-uuid-002", "name": "web-frontend-02", "status": "ACTIVE", "task_state": None},
        {"id": "server-uuid-003", "name": "db-cluster-01", "status": "ACTIVE", "task_state": None},
        {"id": "server-uuid-004", "name": "backup-runner", "status": "SHUTOFF", "task_state": "powering-off"},
    ]

    raw_floating_ips = [
        {
            "id": "fip-uuid-001",
            "floating_ip_address": "192.168.1.150",
            "fixed_ip_address": "10.0.1.15",
            "floating_network_id": "ext-net-uuid-001",
            "port_id": "port-vm-01",
            "status": "ACTIVE",
        }
    ]

    return build_topology_from_data(
        raw_networks=raw_networks,
        raw_subnets=raw_subnets,
        raw_routers=raw_routers,
        raw_ports=raw_ports,
        raw_servers=raw_servers,
        raw_floating_ips=raw_floating_ips,
        project_id="demo-tenant-4a8b-1234",
        project_name="demo",
        auth_url="https://overcloud.local:13000/v3",
        region_name="regionOne",
        all_external=all_external,
    )


# ==============================================================================
# OpenStack API Collector
# ==============================================================================

def collect_live_openstack_topology(
    cloud_name: Optional[str] = None,
    target_project: Optional[str] = None,
    insecure: bool = False,
    all_external: bool = False,
) -> Topology:
    """Connects to OpenStack via openstacksdk and extracts topology data."""
    try:
        import openstack
    except ImportError as e:
        raise OpenStackSDKMissingError(
            "'openstacksdk' is not installed.\n"
            "Install options:\n"
            "  Debian / Ubuntu:\n"
            "    sudo apt install -y python3-openstacksdk\n"
            "  OpenStack Director / RHEL:\n"
            "    sudo dnf install -y python3-openstacksdk\n"
            "  Virtual environment / pip:\n"
            "    pip install -r requirements.txt\n"
            "\nTip: You can test the script right now with: python3 ovn_tenant_topology.py --mock"
        ) from e

    env_insecure = os.environ.get("OS_INSECURE", "").lower() in ("1", "true", "yes")
    verify_ssl = not (insecure or env_insecure)

    cacert = os.environ.get("OS_CACERT")
    if cacert and verify_ssl:
        verify_ssl = cacert

    try:
        if cloud_name:
            conn = openstack.connect(cloud=cloud_name, verify=verify_ssl)
        else:
            conn = openstack.connect(verify=verify_ssl)
    except Exception as e:
        raise ConnectionConfigError(f"Error establishing OpenStack connection: {e}") from e

    current_proj_id = conn.current_project_id
    if target_project:
        try:
            proj = conn.identity.find_project(target_project, ignore_missing=False)
            project_id = proj.id
            project_name = proj.name
        except Exception as e:
            raise ProjectNotFoundError(f"Error finding project '{target_project}': {e}") from e
    else:
        project_id = current_proj_id or os.environ.get("OS_PROJECT_ID") or os.environ.get("OS_TENANT_ID") or ""
        project_name = os.environ.get("OS_PROJECT_NAME") or os.environ.get("OS_TENANT_NAME") or project_id

    auth_url = os.environ.get("OS_AUTH_URL", getattr(conn.config, "auth", {}).get("auth_url", ""))
    region_name = conn.config.region_name or os.environ.get("OS_REGION_NAME", "")

    try:
        ext_nets = list(conn.network.networks(is_router_external=True))
        tenant_nets = list(conn.network.networks(project_id=project_id))
        all_nets = {n.id: n.to_dict() for n in (ext_nets + tenant_nets)}

        # Scoped subnets: fetch tenant subnets, plus subnets for external networks
        tenant_subnets = list(conn.network.subnets(project_id=project_id))
        ext_subnets = []
        for ext_net in ext_nets:
            try:
                ext_subnets.extend(list(conn.network.subnets(network_id=ext_net.id)))
            except Exception:
                pass

        all_subnets_map = {s.id: s.to_dict() for s in (tenant_subnets + ext_subnets)}
        if not all_subnets_map:
            all_subnets_map = {s.id: s.to_dict() for s in conn.network.subnets()}
        subnets = list(all_subnets_map.values())

        routers = [r.to_dict() for r in conn.network.routers(project_id=project_id)]
        ports = [p.to_dict() for p in conn.network.ports(project_id=project_id)]
        fips = [f.to_dict() for f in conn.network.ips(project_id=project_id)]

        server_kwargs: Dict[str, Any] = {"project_id": project_id}
        if target_project:
            server_kwargs["all_projects"] = True
        try:
            servers = [s.to_dict() for s in conn.compute.servers(**server_kwargs)]
        except Exception:
            servers = [s.to_dict() for s in conn.compute.servers(project_id=project_id)]

    except Exception as e:
        raise TopologyQueryError(f"Error querying OpenStack APIs: {e}") from e

    return build_topology_from_data(
        raw_networks=list(all_nets.values()),
        raw_subnets=subnets,
        raw_routers=routers,
        raw_ports=ports,
        raw_servers=servers,
        raw_floating_ips=fips,
        project_id=project_id,
        project_name=project_name,
        auth_url=auth_url,
        region_name=region_name,
        all_external=all_external,
    )


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Horizon Tenant OVN Network Topology CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # View topology tree using active Director OS_* environment:
  python3 ovn_tenant_topology.py

  # Test offline with mock data:
  python3 ovn_tenant_topology.py --mock

  # Output formats:
  python3 ovn_tenant_topology.py --format ascii
  python3 ovn_tenant_topology.py --format table
  python3 ovn_tenant_topology.py --format json

  # Export to Graphviz DOT and generate SVG:
  python3 ovn_tenant_topology.py --format dot | dot -Tsvg -o topology.svg

  # Inspect a specific tenant (with admin credentials):
  python3 ovn_tenant_topology.py --project custom-project-name
        """,
    )

    parser.add_argument(
        "-f",
        "--format",
        choices=["tree", "ascii", "table", "json", "dot", "mermaid"],
        default="tree",
        help="Output format (default: tree)",
    )
    parser.add_argument(
        "--project",
        metavar="PROJECT",
        help="Target project name or UUID (defaults to current OS_PROJECT_NAME/ID)",
    )
    parser.add_argument(
        "--cloud",
        metavar="CLOUD_NAME",
        help="Named cloud from clouds.yaml (optional, defaults to OS_* env vars)",
    )
    parser.add_argument(
        "-k",
        "--insecure",
        action="store_true",
        help="Explicitly allow untrusted/self-signed SSL certificates",
    )
    parser.add_argument(
        "--ovn-details",
        action="store_true",
        help="Include OVN logical switch and logical router IDs in output",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in terminal output",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show additional port MAC addresses, security groups, and details",
    )
    parser.add_argument(
        "--all-external-networks",
        action="store_true",
        help="Show all cloud external networks, even if disconnected from tenant",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run using mock topology data without connecting to OpenStack",
    )

    args = parser.parse_args(argv)

    # NO_COLOR specification adherence: https://no-color.org
    no_color_env = bool(os.environ.get("NO_COLOR"))
    is_dumb_term = os.environ.get("TERM") == "dumb"
    use_colors = not args.no_color and not no_color_env and not is_dumb_term and sys.stdout.isatty()
    colors = Colors(enabled=use_colors)

    try:
        if args.mock:
            topo = get_mock_topology(all_external=args.all_external_networks)
        else:
            if not args.cloud and "OS_AUTH_URL" not in os.environ:
                print(
                    "Notice: No OS_AUTH_URL environment variable detected and no --cloud provided.\n"
                    "To connect to an OpenStack Director cloud, please run:\n"
                    "  $ source ~/overcloudrc (or your tenant rc file)\n"
                    "\nFalling back to --mock mode to show sample topology:\n",
                    file=sys.stderr,
                )
                topo = get_mock_topology(all_external=args.all_external_networks)
            else:
                topo = collect_live_openstack_topology(
                    cloud_name=args.cloud,
                    target_project=args.project,
                    insecure=args.insecure,
                    all_external=args.all_external_networks,
                )
    except OpenStackSDKMissingError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ConnectionConfigError as e:
        print(
            f"{e}\nEnsure you have sourced your Director RC file (e.g., 'source overcloudrc')",
            file=sys.stderr,
        )
        return 1
    except ProjectNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except TopologyQueryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1

    if args.format == "tree":
        output = render_tree(topo, colors, ovn_details=args.ovn_details, verbose=args.verbose)
    elif args.format == "ascii":
        output = render_ascii(topo, ovn_details=args.ovn_details, verbose=args.verbose)
    elif args.format == "table":
        output = render_table(topo, colors, verbose=args.verbose)
    elif args.format == "json":
        output = render_json(topo)
    elif args.format == "dot":
        output = render_dot(topo)
    elif args.format == "mermaid":
        output = render_mermaid(topo)
    else:
        output = render_tree(topo, colors)

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
