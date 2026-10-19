# OpenStack OVN Tenant Network Topology CLI

A fast, terminal-based visualization tool for OpenStack tenant networking and OVN (Open Virtual Network). It reconstructs and renders complete tenant network topologies directly in your console, mirroring the visual graph found in OpenStack Horizon (**Project -> Network -> Network Topology**).

Designed for **OpenStack Director (TripleO / Undercloud / Overcloud)**, standalone OpenStack installations, CI/CD pipelines, and remote terminal troubleshooting.

---

## Features

- 🖥️ **Terminal-First Visual Graph**: Reconstructs the hierarchy of External Networks, Routers, Subnets, VM instances, and Floating IPs in an intuitive Unicode tree with ANSI color coding.
- ⚡ **OVN Integration**: Automatically correlates Neutron networks and routers with their underlying OVN logical entities (`neutron-<id>` Logical Switches and Logical Routers in OVN Northbound/Southbound databases) and shows encapsulation info (`geneve:1024`, `vlan:100`).
- 🔄 **Multiple Output Formats**:
  - `tree` *(default)*: Rich colored Unicode tree with status badges (`[ACTIVE]`, `[SHUTOFF]`, `[ERROR]`), task state, and floating IP paths.
  - `table`: Clean aligned ASCII resource summary card and workload routing path table.
  - `ascii`: Plain-text ASCII tree suitable for log files, text editors, or terminals lacking Unicode support.
  - `mermaid`: Markdown flowchart diagrams ready for GitHub, GitLab, and documentation wikis.
  - `dot`: Graphviz DOT format to generate publication-grade SVG, PNG, or PDF network diagrams.
  - `json`: Structured JSON export conforming to Horizon topology data models.
- 🔒 **OpenStack & Director Authentication**: Seamlessly works with standard `overcloudrc` / `keystonerc` environment variables, `clouds.yaml`, and supports self-signed SSL endpoints (`--insecure`).
- 👥 **Multi-Tenant Administration**: Inspect specific tenant topologies with `--project <name_or_uuid>` with proper Nova admin scoping (`all_projects=True`).
- 📦 **Isolated Network Detection**: Highlights unrouted / isolated networks and attached workloads that lack router interfaces.
- 🌐 **External Network Scoping**: Filters out unconnected cloud provider networks by default, with `--all-external-networks` to reveal all.
- 🧪 **Offline & Mock Mode**: Includes built-in realistic mock data generator (`--mock`) for local development, demos, and testing without an active OpenStack cloud.
- 📦 **Standard Packaging**: Full PEP 517/621 `pyproject.toml` support providing the `ovn-tenant-topology` CLI command.

---

## Architecture & Codebase Overview

```text
ovn-tools/
├── ovn_tenant_topology.py       # Core application (Collector, Engine, Renderers, CLI)
├── test_ovn_tenant_topology.py  # Comprehensive test suite covering all renderers & edge cases
├── pyproject.toml               # PEP 517/621 packaging & console script definition
├── requirements.txt             # Minimal pip dependencies
├── .github/workflows/test.yml   # Multi-version CI testing (Python 3.8 - 3.13)
└── README.md                    # Project documentation
```

### Internal Architecture

```text
                      ┌────────────────────────────────────────┐
                      │   OpenStack Cloud / Mock Data Source   │
                      │  (Neutron, Nova, Glance/Keystone Auth) │
                      └───────────────────┬────────────────────┘
                                          │ openstacksdk
                                          ▼
                      ┌────────────────────────────────────────┐
                      │    collect_live_openstack_topology()   │
                      │  - Scoped subnets & admin Nova query   │
                      │  - External network association        │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │       build_topology_from_data()       │
                      │    (Topology Graph Engine)             │
                      │  - Resolves network hierarchy          │
                      │  - Associates Floating IPs with ports  │
                      │  - Links Routers to External Gateways  │
                      │  - Identifies Isolated Networks        │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │             Topology Model             │
                      │  Dataclasses: SubnetInfo, PortInfo,    │
                      │  ServerInfo, NetworkInfo, RouterInfo   │
                      └───────────────────┬────────────────────┘
                                          │
      ┌────────────┬────────────┬─────────┼──────────┬────────────┬────────────┐
      ▼            ▼            ▼         ▼          ▼            ▼            ▼
 ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
 │render_tree│render_asci│render_tabl│render_json│render_dot │render_merm│
 └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘
```

---

## Installation & Requirements

### Requirements

- **Python**: 3.8 or newer
- **OpenStack SDK**: `openstacksdk` (only required when connecting to live OpenStack environments; `--mock` runs with zero third-party dependencies).
- **Graphviz** *(optional)*: For compiling DOT diagrams into images (`sudo apt install graphviz` / `brew install graphviz`).

---

### Setup Guide

#### Option 1: Install as a CLI Tool via pip (Recommended)

```bash
git clone https://github.com/martinl/ovn-tools.git
cd ovn-tools
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

After installation, the tool is directly available in your terminal:
```bash
ovn-tenant-topology --mock
```

#### Option 2: Run Directly from Source

```bash
git clone https://github.com/martinl/ovn-tools.git
cd ovn-tools
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 ovn_tenant_topology.py --mock
```

#### Option 3: System Package on Linux (No venv needed)

- **Debian / Ubuntu**:
  ```bash
  sudo apt update && sudo apt install -y python3-openstacksdk
  python3 ovn_tenant_topology.py --mock
  ```
- **RHEL / CentOS / OpenStack Director**:
  ```bash
  sudo dnf install -y python3-openstacksdk
  python3 ovn_tenant_topology.py --mock
  ```

---

## Quickstart

### 1. Test Instantly with Mock Data (No OpenStack Required)

```bash
python3 ovn_tenant_topology.py --mock
```

Sample output:
```text
==============================================================================
OpenStack Tenant Network Topology | Project: demo (demo-tenant-4a8b-1234)
Region: regionOne | Auth URL: https://overcloud.local:13000/v3
==============================================================================

🌐 External Network: public (192.168.1.0/24) [ACTIVE]
└── 🔀 Router: tenant-gateway-router [ACTIVE] (Gateway: 192.168.1.105 | SNAT: enabled)
    ├── 🔌 Subnet: app-internal-net / 10.0.1.0/24 (GW: 10.0.1.1) [geneve:1024]
    │   ├── 🖥️  web-frontend-01 [ACTIVE] [10.0.1.15] ➜ 🌐 Floating IP: 192.168.1.150
    │   └── 🖥️  web-frontend-02 [ACTIVE] [10.0.1.16]
    └── 🔌 Subnet: db-backend-net / 10.0.2.0/24 (GW: 10.0.2.1) [geneve:1025]
        └── 🖥️  db-cluster-01 [ACTIVE] [10.0.2.20]

📦 Isolated / Unrouted Networks (No Router Interface):
└── 🔌 Network: isolated-backup-net [ACTIVE] [backup-subnet (172.16.50.0/24)] [geneve:1026]
    └── 🖥️  backup-runner [SHUTOFF (powering-off)] [172.16.50.5]
```

### 2. Connect to an OpenStack Environment

Source your OpenStack RC file:

```bash
source ~/overcloudrc
# or
source ~/myprojectrc

# Run the CLI
ovn-tenant-topology
```

If your OpenStack deployment uses self-signed SSL certificates:
```bash
ovn-tenant-topology --insecure
```

---

## CLI Usage & Options

```text
usage: ovn_tenant_topology.py [-h] [-f {tree,ascii,table,json,dot,mermaid}]
                              [--project PROJECT] [--cloud CLOUD_NAME] [-k]
                              [--ovn-details] [--no-color] [-v]
                              [--all-external-networks] [--mock]
```

| Flag | Description |
|---|---|
| `-f, --format FORMAT` | Output format: `tree` (default), `ascii`, `table`, `json`, `dot`, `mermaid` |
| `--project PROJECT` | Target project name or UUID (uses `all_projects=True` for admin credentials) |
| `--cloud CLOUD_NAME` | Named cloud entry defined in `~/.config/openstack/clouds.yaml` |
| `-k, --insecure` | Explicitly trust self-signed / untrusted SSL certificates |
| `--ovn-details` | Include OVN logical switch (`neutron-<net_id>`), logical router (`neutron-<rtr_id>`), encapsulation, and port LSP identifiers |
| `-v, --verbose` | Show additional port details (MAC addresses, security groups) and VM task state |
| `--all-external-networks` | Show all cloud external networks, even if not connected to the tenant's routers |
| `--no-color` | Disable ANSI color sequences in output (also honors the standard `NO_COLOR` env var) |
| `--mock` | Run with sample topology data without querying OpenStack |

---

## Output Format Examples

### 1. Tabular Workload Inventory (`--format table`)

```bash
python3 ovn_tenant_topology.py --mock --format table
```

Output:
```text
========================================================================================
OpenStack Tenant Network Topology Overview | Project: demo (demo-tenant-4a8b-1234)
Region: regionOne | Auth URL: https://overcloud.local:13000/v3
========================================================================================

RESOURCE SUMMARY:
+----------+---------+-------------+---------------+---------+-----------+--------------+
| Ext Nets | Routers | Tenant Nets | Isolated Nets | Subnets | Instances | Floating IPs |
+----------+---------+-------------+---------------+---------+-----------+--------------+
|    1     |    1    |      3      |       1       |    3    |     4     |      1       |
+----------+---------+-------------+---------------+---------+-----------+--------------+

WORKLOAD INVENTORY & ROUTING PATH:
+-----------------+------------------------+-------------+---------------+--------------------------------------+-------------------------------------------+
|     Instance    |         Status         | Fixed IP(s) |  Floating IP  |            Network / CIDR            |               Router Gateway              |
+-----------------+------------------------+-------------+---------------+--------------------------------------+-------------------------------------------+
| backup-runner   | SHUTOFF (powering-off) | 172.16.50.5 |       -       | isolated-backup-net (172.16.50.0/24) | [Isolated]                                |
| db-cluster-01   |         ACTIVE         | 10.0.2.20   |       -       | db-backend-net (10.0.2.0/24)         | tenant-gateway-router (GW: 192.168.1.105) |
| web-frontend-01 |         ACTIVE         | 10.0.1.15   | 192.168.1.150 | app-internal-net (10.0.1.0/24)       | tenant-gateway-router (GW: 192.168.1.105) |
| web-frontend-02 |         ACTIVE         | 10.0.1.16   |       -       | app-internal-net (10.0.1.0/24)       | tenant-gateway-router (GW: 192.168.1.105) |
+-----------------+------------------------+-------------+---------------+--------------------------------------+-------------------------------------------+
```

Add `-v` to include MAC Addresses and Security Groups:
```bash
python3 ovn_tenant_topology.py --mock --format table -v
```

### 2. Inspect OVN Logical Entities

Add `--ovn-details` to view underlying OVN logical switches, logical routers, and encapsulation:

```bash
python3 ovn_tenant_topology.py --ovn-details
```

Output includes:
```text
└── 🔀 Router: tenant-gateway-router [ACTIVE] (...) [OVN LR: neutron-router-uuid-001]
    ├── 🔌 Subnet: app-internal-net / 10.0.1.0/24 (...) [geneve:1024] [OVN LS: neutron-tenant-net-uuid-101]
```

Correlate directly with OVN utilities on controller nodes:
```bash
sudo ovn-nbctl show neutron-router-uuid-001
sudo ovn-nbctl lr-nat-list neutron-router-uuid-001
sudo ovn-sbctl show
```

### 3. Generate Graphviz SVG / PNG Network Diagrams

```bash
# Render to SVG
python3 ovn_tenant_topology.py --format dot | dot -Tsvg -o topology.svg

# Render to PNG
python3 ovn_tenant_topology.py --format dot | dot -Tpng -o topology.png
```

### 4. Generate Mermaid Diagrams for Markdown

```bash
python3 ovn_tenant_topology.py --format mermaid
```

### 5. Plain ASCII for Automated Log Files

```bash
python3 ovn_tenant_topology.py --format ascii --ovn-details -v
```

Output:
```text
=== OpenStack Tenant Topology: demo (demo-tenant-4a8b-1234) ===
Region: regionOne | Auth URL: https://overcloud.local:13000/v3

[EXT: public] (192.168.1.0/24) [ACTIVE]
  \-- [ROUTER: tenant-gateway-router] [ACTIVE] (GW: 192.168.1.105, snat:on) [OVN LR: neutron-router-uuid-001]
      +-- [SUBNET: app-internal-net] (IP: 10.0.1.1) [OVN LS: neutron-tenant-net-uuid-101]
      |   +-- [VM: web-frontend-01] [ACTIVE] (IP: 10.0.1.15) (MAC: fa:16:3e:20:01:15) (SG: secgroup-web, secgroup-default) <=> [FIP: 192.168.1.150]
      |   \-- [VM: web-frontend-02] [ACTIVE] (IP: 10.0.1.16) (MAC: fa:16:3e:20:01:16) (SG: secgroup-web)
      \-- [SUBNET: db-backend-net] (IP: 10.0.2.1) [OVN LS: neutron-tenant-net-uuid-102]
          \-- [VM: db-cluster-01] [ACTIVE] (IP: 10.0.2.20) (MAC: fa:16:3e:20:02:20) (SG: secgroup-db)

[ISOLATED NETWORKS]
  +-- [NET: isolated-backup-net] (172.16.50.0/24) [OVN LS: neutron-tenant-net-uuid-103]
      +-- [VM: backup-runner] [SHUTOFF (powering-off)] (IP: 172.16.50.5) (MAC: fa:16:3e:30:50:05) (SG: secgroup-backup)
```

### 6. Export JSON for Scripting

```bash
python3 ovn_tenant_topology.py --format json | jq '.routers'
```

---

## Running Unit Tests

Run the test suite using Python's standard `unittest` module:

```bash
python3 -m unittest -v test_ovn_tenant_topology.py
```

Expected output:
```text
test_all_external_networks_toggle ... ok
test_cli_main_invocations ... ok
test_colors_and_badges ... ok
test_custom_exceptions_hierarchy ... ok
test_edge_case_topologies ... ok
test_escaping_helpers ... ok
test_mock_topology_structure ... ok
test_render_ascii_ovn_and_verbose ... ok
test_render_dot ... ok
test_render_json ... ok
test_render_mermaid ... ok
test_render_table ... ok
test_render_tree ... ok

----------------------------------------------------------------------
Ran 13 tests in 0.009s

OK
```

---

## License

Licensed under the Apache License, Version 2.0. See the file header for license details.
