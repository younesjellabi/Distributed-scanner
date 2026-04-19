# Lab vs. Production Gaps

Running list of known differences between what works in the containerlab
POC and what will happen on real Arista EOS hardware. Each entry lists
the gap, why it exists, and how it will be addressed.

This file is read by future-you and, eventually, the security team.
Keep it current. Every time a new gap is discovered, add an entry
before moving on.

---

## Gap 1: Kernel environment

**Lab:** The scanner runs on the host's Ubuntu kernel, with sysctl
values tuned for cEOS (`fs.inotify.max_user_instances = 1024`,
`fs.inotify.max_user_watches = 1048576`).

**Production:** The scanner runs inside EOS's Docker runtime, using
EOS's kernel. The kernel version, defaults, and available tunables may
differ from Ubuntu's. Any assumption the scanner makes about kernel
behavior or available sysctls needs to be verified on a real device,
not inferred from the lab.

**Resolution plan:** Phase 2 — validate on a single lab-grade EOS
device before production rollout. Specifically check inotify limits,
network namespace behavior, and cgroup v1 vs. v2 (EOS has historically
used cgroup v1; newer releases have broader support).

**Risk if ignored:** Scanner behaves subtly differently in production
— silent failures of file-watching, log-rotation, or process-spawning
code paths that worked fine in the lab.

---

## Gap 2: Container hosting model

**Lab:** The scanner is a *sibling* to the cEOS container, both running
under the host's Docker daemon. They share the host kernel and the host
Docker networking.

**Production:** The scanner runs *inside* EOS's Docker runtime (EOS is
itself the host for the container). Different lifecycle management,
different resource accounting, different logging paths.

**Resolution plan:** Phase 2 — package as `.swix` and validate on EOS.
Expect differences in how the container is started, restarted, and
monitored. Production lifecycle will be managed by EOS Container
Manager (`daemon` config block), not raw `docker run`.

**Risk if ignored:** Scanner works fine in lab but fails to restart
cleanly after device reload, or logs to a location EOS doesn't collect,
or has resource limits enforced differently than expected.

---

## Gap 3: Scanner-to-data-plane attachment mechanism

**Lab:** Scanner is a sibling container to cEOS. Containerlab wires a
veth pair between the scanner's netns and a cEOS data-plane interface
(e.g., Ethernet1). The scanner gets an IP on that subnet and scans
through cEOS's routing. Attachment is trivial from the host's
perspective — just veth plumbing between two containers.

**Production:** Scanner runs inside EOS's Docker daemon. Attaching it
to a data-plane VRF requires EOS-specific mechanisms. Two supported
patterns have been identified and validated against Arista documentation:

1. **Direct namespace placement:**
   `ip netns exec ns-<VRF> docker run --network none <image>`
   followed by interface assignment inside the namespace.

2. **macvlan bridging to a VLAN/SVI:**
   `docker network create -d macvlan --subnet=<cidr> -o parent=vlan<N> <name>`
   then `docker run --network <name> <image>`.

For production lifecycle (survival across reload), the container should
be declared as an EOS `daemon` configuration block supervised by
Container Manager, rather than invoked with raw `docker run`.

**Resolution plan:** Phase 2. Validate both attachment patterns on a
single lab-grade EOS device. Document:
- Exact EOS configuration syntax (version-specific)
- Which pattern is chosen and why
- Startup sequencing (what happens during EOS boot, during VRF
  add/remove, during container restart)
- How the scanner receives its IP (static assignment? DHCP from the
  SVI? pre-configured in the EOS `daemon` block?)

The scanner container itself should not need changes — only its
attachment method differs between lab and prod.

**Risk if ignored:** Scanner deployed to prod attaches to the default
(management) namespace, scans management VRF, sees nothing useful, and
the project's core value proposition fails silently.

---

## Gap 4: Image distribution in production

**Lab:** Scanner image is built locally on the Ubuntu host and tagged.
No registry, no network pulls.

**Production:** Each EOS device's Docker daemon must obtain the scanner
image from somewhere. The Docker daemon on EOS runs in the **default
network namespace**, so any registry pull uses default-VRF routing —
regardless of which VRF the eventual container will run in.

If the container registry is only reachable from a different VRF
(common, since registries often live in management networks), the
daemon's default-VRF pull will fail.

Three possible distribution models:

**a) Registry reachable from default VRF.**
Either the registry lives in default-VRF space, or routing policy
leaks the registry's prefix into default VRF. Simplest operationally
but requires network-side coordination.

**b) Side-loaded tarball.**
Image is built centrally, exported (`docker save`), pushed to device
flash through existing EOS config-management channels (CVP, Ansible,
rsync), and imported on device (`docker load`). No runtime network
dependency on a registry. Adds a step to every image update.

**c) `.swix` extension with embedded image.**
Build a `.swix` EOS extension that bundles the scanner image as a
resource. Distribute via Arista's standard extension mechanism
(CloudVision / CVP, or manual). When the extension is installed, EOS
makes the image available to Container Manager. Matches how Arista
already distributes other agents.

**Resolution plan:** Phase 3. Lean toward **(c)** — `.swix` packaging
— because it eliminates the runtime network dependency and matches
Arista's standard distribution model. **(a)** and **(b)** are fallbacks
for environments where `.swix` lifecycle management is not desired.
Validate the chosen approach on lab hardware in Phase 2 before
committing in Phase 3.

**Risk if ignored:** Pilot devices succeed (because the test
environment typically has unrestricted reachability), production
rollout fails at site #1 because the production device cannot reach
the registry from default VRF. Discovered at the worst possible time.

---

## How to add a new gap

When building the lab, every time you notice something that is true
about the lab but will NOT be true in production (or vice versa),
add an entry here *before moving on*. Format:

```
## Gap N: <short name>

**Lab:** <what is true in the containerlab environment>

**Production:** <what will be different on a real device>

**Resolution plan:** <phase and approach>

**Risk if ignored:** <what breaks, and when>
```

Gaps discovered late are gaps that kill rollouts. Gaps documented
early are gaps that shape the design. Always add the entry.
