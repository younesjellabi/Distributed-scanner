# Architecture

This document describes *what* we are building, *why* the pieces are
shaped the way they are, and *where the open questions still are*.

Audience: future-you, a new collaborator, and eventually the security
team. Written to be readable in that order.

---

## 1. Problem statement

We need to run periodic network and security scans across hundreds of
remote sites. The scans must:

1. See each site's local subnets from *inside* that site (not through
   the WAN, NAT, or firewalls).
2. Require **no new hardware** at any site.
3. Be approvable by the security team — meaning minimal attack surface,
   no inbound exposure, clear data flows, resource isolation from the
   production control plane.
4. Scale to hundreds of sites with one engineer maintaining it.

Each site already has at least one Arista EOS device. Modern EOS
supports on-box Docker containers. That is the leverage point this
project exploits.

---

## 2. High-level design

Two components, one data flow:

```
┌───────────────────────────── site ──────────────────────────────┐
│                                                                 │
│  ┌─────────────────────────── EOS ────────────────────────────┐ │
│  │                                                            │ │
│  │   ┌─ container (scanner) ──────────────────────────────┐   │ │
│  │   │  • probe local subnets                             │   │ │
│  │   │  • buffer results to local disk (capped)           │   │ │
│  │   │  • ship raw results over HTTPS to collector        │   │ │
│  │   │  • resource-limited (CPU, mem, I/O)                │   │ │
│  │   └────────────────────────────────────────────────────┘   │ │
│  │                                                            │ │
│  │   Control plane (BGP/OSPF/ISIS/MLAG/…) — UNTOUCHED         │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │ outbound HTTPS only
                               ▼
                    ┌──────────────────────┐
                    │  central collector   │
                    │                      │
                    │  • receives raw      │
                    │  • enriches (CVE,    │
                    │    NetBox, asset DB) │
                    │  • stores results    │
                    │  • generates reports │
                    └──────────────────────┘
```

### Split of responsibilities

| Concern                          | On-device (scanner) | Central (collector) |
|----------------------------------|:-------------------:|:-------------------:|
| ICMP / TCP probe of local subnet | ✅                  |                     |
| Banner grab / service fingerprint| ✅ *(configurable)* |                     |
| Local buffer / retry queue       | ✅                  |                     |
| Rate limiting / safety throttle  | ✅                  |                     |
| CVE enrichment                   |                     | ✅                  |
| NetBox / CMDB correlation        |                     | ✅                  |
| Long-term storage                |                     | ✅                  |
| Reporting / dashboards           |                     | ✅                  |
| Alerting                         |                     | ✅                  |

The logic: **anything requiring large data sources (CVE DB, CMDB) or
long-term state stays central. Anything requiring local network
visibility runs on-device. Everything else is pushed central.**

This is the same principle as `tcpdump` on a router vs. pcap analysis
on a workstation — capture where you must, analyze where it's cheap.

---

## 3. Network attachment model

### Core constraint

**The on-device scanner container MUST operate from the data plane of
the Arista device, not the management interface.**

The entire architectural value of running the scanner on the EOS device
depends on its visibility into the site's production network. Attaching
the container to the management interface (Management0 / Ma1) would
confine it to the management VRF — a small, isolated subnet containing
only switches and NMS endpoints. From that vantage point the scanner
would be blind to the user subnets, server VLANs, and inter-site routing
that are the actual scan targets.

### On real EOS hardware

The scanner container is attached to a **specific data-plane VRF**,
which in Linux terms is a specific network namespace scoped to that
VRF's interfaces and routing table. The container:

- Sees only the data-plane VRF's interfaces and routes.
- Does not see or use Management0.
- Cannot reach other VRFs except through explicit inter-VRF routing policy.
- Scans hosts via the EOS data plane — the same path production traffic takes.

The specific VRF choice is site-dependent. Typical patterns:
- A dedicated `SCANNER` or `OPS-TOOLS` VRF with leaks into target VRFs
- The existing `USER` / `default` data-plane VRF, if the site is single-VRF
- A management-adjacent VRF that has routing into user subnets (less common, more permissive)

### EOS-side mechanism (validated against Arista documentation)

EOS represents each configured VRF as a Linux network namespace named
`ns-<vrf-name>`. For example, VRF `USER` is visible to Linux as
network namespace `ns-USER`. This is the same mechanism Arista's own
native daemons (TerminAttr, telemetry exporters) use to operate
across VRFs.

A container can be launched into a specific VRF's namespace using
either of two documented methods:

1. **Direct namespace placement:**
   ```
   ip netns exec ns-USER docker run --network none <image>
   ```
   followed by interface assignment inside the namespace. The
   container has full L3 reachability via the VRF's routing table.

2. **macvlan bridging to a VLAN/SVI:**
   ```
   docker network create -d macvlan \
     --subnet=<user-subnet> -o parent=vlan<N> <net-name>
   docker run --network <net-name> <image>
   ```
   The container appears as an L2 host on that VLAN with the SVI as
   its default gateway.

Method 1 is preferred when the scanner needs visibility across multiple
subnets reachable through a single VRF's routing. Method 2 is preferred
when the scanner is scoped to a single VLAN.

### Docker daemon placement

The Docker daemon itself runs in the **default network namespace** on
EOS. This is a fixed behavior, not a per-container setting. The
consequence: any network activity initiated by the daemon itself —
notably pulling images from a registry — uses the default VRF's
routing, regardless of which VRF the eventual container will run in.
This constrains image distribution strategy in Phase 3
(see `lab-vs-prod-gaps.md`, Gap 4).

### Production lifecycle: Container Manager

Raw `docker run` invocations do not survive an EOS reload. For
production deployment, containers should be declared as EOS
`daemon` configuration blocks, which EOS Container Manager supervises
as native EOS agents: started on boot, restarted on crash, placed into
the declared VRF's namespace. This is the production-blessed pattern
and is what Phase 3 will target. Phase 2 will validate the exact
configuration syntax on lab-grade hardware.

### In the containerlab POC

The lab simulates this attachment model by connecting the scanner
container via a veth pair to a cEOS data-plane interface (e.g.,
Ethernet1), giving the scanner a data-plane IP and data-plane routing.
The cEOS management interface remains dedicated to lab orchestration
(SSH, containerlab control) and is explicitly not used by the scanner.

This proves the **data flow and probe logic** work correctly through a
data-plane attachment. It does NOT validate the EOS-native mechanism
for placing a container into a data-plane VRF on real hardware — that
is a Phase 2 concern. See `lab-vs-prod-gaps.md`, Gap 3.

### Out of scope for the scanner container

- No inbound reachability from the site LAN to the scanner. The
  container exposes no listening ports on the data-plane side.
- No use of Management0 by the container.
- No inter-VRF routing performed by the container itself; it relies on
  the device's routing policy for reachability.

---

## 4. The "minimum on-device container" question

This is the most important open question in Phase 1 and the single
biggest driver of whether this project is security-team-approvable.

### Hypothesis

> On-device container is **as minimal as possible**: probe, buffer,
> ship. Nothing else. No enrichment, no reporting, no lookups.

### Why minimal matters

Every byte of code on the device is:
- attack surface the security team has to accept,
- CPU/memory pressure on a device whose primary job is forwarding,
- a maintenance obligation multiplied by the number of sites,
- disk consumed on small on-device flash.

### What "minimum" actually includes

The probe-only story is *almost* right, but misses operational safety.
The true minimum is:

1. **Probe** — scan configured local subnet(s), collect raw results.
2. **Local buffer** — when the collector is unreachable, queue results
   to disk. Disk is bounded (circular buffer or size-capped queue);
   a multi-day WAN outage must not fill device flash.
3. **Retry / backoff** — resilient outbound shipping.
4. **Safety controls** — scan-rate cap, concurrency cap, CPU nice
   value, cgroup limits. These live *in* the container because the
   container author is the one who understands what "too aggressive"
   means for a scan.
5. **Config** — pulled from a known location at startup; no baked-in
   site specifics in the image.
6. **Health endpoint** — local-only, for the device itself / a
   supervisor to check liveness. **Not** exposed to the site LAN.

### What is explicitly *out*

- CVE databases (GB of data, changes daily — belongs central).
- NetBox / CMDB lookups (requires credentials the device should not hold).
- Report generation (CPU-heavy, no reason to do it per-site).
- Any inbound listener reachable from the site LAN.
- Any persistent state beyond the buffer and last-sent-watermark.

---

## 5. Open questions

These drive the remaining Phase 1 work. Each must be answered by
experiment, not assertion.

### Q1. What is "raw" data, exactly?

Ping + TCP SYN on N ports is cheap. Banner grabs, SNMP walks, TLS
certificate pulls are not. At 500 sites × /22 × top-1000 TCP with
banners, the central collector becomes the bottleneck, not the probe.

**To decide:** measure bytes-per-host-scanned for each probe type in
the lab. Build a back-of-envelope model for 500 sites before
committing to a probe set.

### Q2. How big is the local buffer?

If central is unreachable, how long can we queue locally before
losing data? Flash on a typical EOS device is 2–8 GB, of which EOS
itself uses most. Plausible budget for a scanner container: maybe
200 MB total. Buffer maybe half of that.

**To decide:** what's the per-scan result size, multiplied by scan
frequency, multiplied by acceptable outage duration? If the math
doesn't close, we need lossy buffering (drop oldest) and we need to
be honest about it.

### Q3. How do we prove we are not harming the control plane?

The security team's first question. The answer has to be quantitative:
"cgroup limits are X, and in testing under load, control-plane CPU
stayed below Y."

**To decide:** establish a baseline on a lab EOS device, run the
scanner under stress, measure deltas. This is a Phase 2 item but the
container must be *designed* for it in Phase 1 (proper cgroup
annotations, bounded worker pools, etc.).

### Q4. How does the scanner get its config?

Options:
- **Baked into image** — bad, requires rebuild per site.
- **Environment variables at container start** — reasonable, requires
  EOS-side config management.
- **Pulled from collector at startup** — elegant, but makes collector
  a hard dependency for cold-start. Also a security conversation
  (the device trusts the collector to tell it what to scan).
- **Local file on device flash** — simple, fits EOS configuration
  management patterns.

**Leaning:** local file on device flash, mounted into the container
read-only. Matches how EOS operators already think about device config.

### Q5. Authentication to the collector

A rogue device posting fake results to the collector is a real threat.
Options range from mTLS with per-device certs (best, most work) to
shared bearer tokens (worst, easiest). Phase 2 concern but flagging
now.

### Q6. Which VRF will the scanner attach to in production?

The VRF strategy is site-dependent and must be answered per target
environment before rollout:

- Are production Arista sites typically **single-VRF** (everything in
  default) or **multi-VRF** (separate VRFs for user, guest, OT,
  management)?
- If multi-VRF, is there an existing VRF that could serve as the
  scanner's home, with routing into target user VRFs? Or will this
  project need to introduce a new VRF (e.g., `OPS-SCAN`) at every
  site?
- If a new VRF is introduced, does that require coordination with
  the routing team and a change window at each site?

**To decide:** audit the target fleet's VRF topology before Phase 3
planning. This question, unanswered, can kill a rollout at site #4 of
500.

---

## 6. Networking model

### In the lab (containerlab)

- cEOS node represents the site EOS device.
- Scanner container is a **sibling** to cEOS in the lab, attached to
  the same Docker bridge that represents the site's local subnet.
- Target containers also attach to that bridge, giving the scanner
  something to find.
- Collector container is on a separate bridge representing the
  "central" network, reachable from the scanner through cEOS.

### On real hardware

- Scanner container runs **inside** EOS's Docker runtime, not as a
  sibling.
- The container is placed into a data-plane VRF's Linux namespace
  (`ns-<vrf>`), giving it L3 presence scoped to that VRF.
- Outbound traffic to the collector traverses the device's normal
  routing table for that VRF.

### Mapping to networking concepts

| Container construct     | Networking analogue                      |
|-------------------------|------------------------------------------|
| Linux net namespace     | VRF                                      |
| Docker bridge           | VLAN / broadcast domain                  |
| `veth` pair             | Point-to-point link                      |
| Container IP            | Loopback or SVI IP                       |
| `iptables` / `nftables` | ACL                                      |
| Image                   | Software image (EOS `.swi`)              |
| Registry                | Artifact server / image store            |
| `.swix` extension       | EOS-native package wrapping the container|
| Cgroup                  | QoS policer                              |

---

## 7. Data flow and data model

<!-- DECIDE: this section is a stub. Fill in once the probe + collector
contract is designed. Key questions:
  - JSON schema of a single scan result?
  - One POST per scan run, or streamed line-delimited JSON?
  - How are sites identified — hostname, device serial, assigned site-id?
  - Timestamps — UTC everywhere, obviously, but what precision?
  - Idempotency — how does central handle a duplicate submission after a retry? -->

*Stub — to be filled in during Phase 1 step 4 (scanner ↔ collector
contract).*

---

## 8. What we are explicitly NOT doing

Stating non-goals is as important as stating goals. It prevents scope
creep and makes security review cleaner.

- **Not** an IPS/IDS. No real-time threat detection, no blocking.
- **Not** a replacement for centralized vulnerability scanners; this
  is a *complement* that sees what they can't.
- **Not** a packet capture tool. The on-device container does not
  sniff traffic, only actively probes.
- **Not** collecting endpoint telemetry. We scan network services
  reachable on the wire, nothing more.
- **Not** managing EOS configuration. The scanner is a tenant of the
  device, not an operator of it.

---

## 9. Decision log

A running list of decisions with dates and rationale. Add to this as
we go; it's how future-you reconstructs *why*.

| Date       | Decision | Rationale |
|------------|----------|-----------|
| <!-- DECIDE: date --> | Start POC in containerlab, not on real EOS first. | Iteration speed; de-risk the container pipeline before touching hardware. |
| <!-- DECIDE: date --> | On-device container is probe-only + safety; enrichment is central. | Minimize attack surface, resource footprint, and per-site maintenance. |
| <!-- DECIDE: date --> | Outbound HTTPS only; no inbound listener on site LAN. | Security posture; matches how network devices already talk to management systems. |
| <!-- DECIDE: date --> | Scanner attaches to a data-plane VRF via `ns-<vrf>` namespace, not to Management0. | The project's entire value depends on data-plane visibility. Management-interface placement would leave the scanner blind to production subnets. Validated against Arista documentation (EOS VRF ↔ Linux namespace mapping). |
| <!-- DECIDE: date --> | Production lifecycle via EOS Container Manager (`daemon` config block), not raw `docker run`. | Raw invocations do not survive reload. Container Manager integrates with EOS lifecycle, startup, and VRF placement natively. |

---

## 10. Glossary

- **cEOS** — Containerized Arista EOS. Lab / test form of EOS that
  runs as a container; no ASIC, software forwarding only.
- **.swix** — Arista EOS extension package format. How we will ship
  the scanner container to production devices in Phase 3.
- **Container Manager** — EOS subsystem that supervises customer
  containers declared via `daemon` configuration blocks, handling
  startup, restart, and VRF placement.
- **Containerlab** — Tool that builds multi-node network labs from
  YAML topology files using containerized NOS images.
- **`ns-<vrf>`** — Linux network namespace name corresponding to an
  EOS VRF. EOS automatically creates and maintains this mapping.
- **Vantage point** — The network location from which a scan is run.
  The whole premise of this project is that *site-local* vantage
  points see things central vantage points cannot.
