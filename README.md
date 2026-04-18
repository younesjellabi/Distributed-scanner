Distributed Network & Security Scanner
A network and security scanner that runs from Arista EOS devices as vantage

points across hundreds of sites, centralizing results to a single collector.
No new hardware. The network is already there — we use it.

Why this exists
Traditional network/security scanning from a central scanner has a fundamental

problem: the scanner sees the network from one vantage point. Firewalls,

NAT, segmented VRFs, and asymmetric routing hide entire subnets from a

central scanner.
The Arista EOS devices at each site already sit inside the local broadcast

domain, already have routes into every local VRF, and already have the

CPU and disk to run a small container. They are the best possible

vantage point for a local scan — we just aren't using them that way yet.
This project turns each EOS device into a distributed probe that reports

home, without adding hardware, without opening new inbound firewall holes,

and without touching the production control plane in a way the security

team would reject.

Architecture at a glance
  site 1              site 2              site N
  ┌──────┐            ┌──────┐            ┌──────┐
  │ EOS  │            │ EOS  │            │ EOS  │
  │┌────┐│            │┌────┐│            │┌────┐│
  ││scan││            ││scan││            ││scan││    <- on-device container:
  │└─┬──┘│            │└─┬──┘│            │└─┬──┘│       probe + local buffer
  └──┼───┘            └──┼───┘            └──┼───┘       + safety controls
     │                   │                   │
     │   (raw results, outbound HTTPS only)  │
     └───────────────────┼───────────────────┘
                         ▼
                    ┌─────────┐
                    │ central │    <- enrichment, correlation,
                    │collector│       reporting, long-term store
                    └─────────┘

See docs/architecture.md for the detailed design

and the reasoning behind each choice.

Repository layout
.
├── docs/             # Architecture, lab-vs-prod gaps, security notes
├── lab/              # Containerlab topology + cEOS configs for local POC
├── scanner/          # The on-device container (Python + Dockerfile)
├── collector/        # Central receiver container
├── targets/          # Fake hosts used in the lab to give the scanner something to find
└── packaging/        # Phase 3: .swix build scripts for EOS deployment


Project phases
This repo is built in three phases. We do not skip phases.
Phase	Goal	Status
1. Container lab POC	Prove on-device container → central collector pipeline works end-to-end in a containerlab simulation of cEOS.	🟡 In progress
2. Concept building	Harden the container, package as .swix, validate on a single lab-grade EOS device.	⚪ Not started
3. Production rollout	Deploy to N sites with rollout automation, monitoring, and security-team sign-off.	⚪ Not started


Prerequisites
·	Ubuntu  with a modern kernel (uname -r ≥ 5.15)
·	Docker Engine (not Docker Desktop)
·	Containerlab
·	Arista cEOS image — not redistributed with this repo, obtain from
arista.com with a valid account. Expected at
~/images/ceos/cEOS-lab-<version>.tar.xz.
·	Python 3.11+
Kernel parameters for cEOS (add to /etc/sysctl.d/99-ceos.conf, then

sudo sysctl --system):
fs.inotify.max_user_instances = 1024
fs.inotify.max_user_watches   = 1048576


Quickstart — bring up the lab
# 1. Import the cEOS image (one-time)
docker import ~/images/ceos/cEOS-lab-<version>.tar.xz ceos:<version>

# 2. Deploy the lab topology
cd lab
sudo containerlab deploy -t topology/scanner-poc.clab.yml

# 3. Verify containers are up
sudo containerlab inspect -t topology/scanner-poc.clab.yml

# 4. Tear down
sudo containerlab destroy -t topology/scanner-poc.clab.yml


The on-device container — design hypothesis
The working hypothesis driving Phase 1:
On-device runs only the probe (scan local subnet, collect raw results,

buffer locally, ship raw data to central). Central does all enrichment

(CVE lookup, NetBox correlation, report generation).
Refined to include operational safety:
On-device = probe + local buffer + safety controls.

Central = enrichment + correlation + reporting.
This hypothesis is being actively challenged as the POC is built. See

docs/architecture.md for the open questions.

Lab vs. production
The lab is a simulation, not production. Every known difference between

"works in containerlab" and "works on a real EOS device" is tracked in

docs/lab-vs-prod-gaps.md. Read that before

assuming lab behavior transfers.

Security posture
This project runs code on production network devices. That is a serious

thing to do. Security design notes, threat model, and the material

prepared for security-team review live in

docs/security-notes.md.
Short version:
·	Container runs as non-root with a read-only root filesystem.
·	Outbound-only connections (device → collector, never the reverse).
·	Resource caps (CPU, memory, I/O) via cgroups to protect the control plane.
·	Signed container images, pinned by digest.
·	No inbound exposure of the scanner from the site LAN.

Contributing
Solo project for now. Branch discipline anyway:
·	main is always deployable.
·	Work on feature branches (feat/..., fix/..., docs/...).
·	Pull requests even when self-merging — the PR description is the changelog.

License
TBD.
