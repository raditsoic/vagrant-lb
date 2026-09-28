# vagrant — k3s load balancer lab

Four Debian 12 VMs (VirtualBox, managed by Vagrant, provisioned by Ansible):

| Machine | Private IP      | Role                                  | NAT forward (127.0.0.1)             |
| ------- | --------------- | ------------------------------------- | ----------------------------------- |
| `lb`    | `192.168.56.10` | k3s server (control plane) + Traefik + dnsmasq | SSH `2210`, HTTP `8080`, DNS `5533` |
| `web1`  | `192.168.56.11` | k3s agent (worker), runs app pods  | SSH `2211`, HTTP `8081`             |
| `web2`  | `192.168.56.12` | k3s agent (worker), runs app pods  | SSH `2212`, HTTP `8082`             |
| `db`    | `192.168.56.20` | PostgreSQL 15 (shared visits DB)      | SSH `2213`, psql `5433`             |

All machines share one host-only network. The app runs as a **Kubernetes
Deployment** (2 replicas, one pod per worker node, image pulled from the
lab's own registry — see "Containerized app + registry" below), and all
pods write to **one shared PostgreSQL** on `db.tiket.lab` — the production
topology: interchangeable stateless pods plus a dedicated database VM.
Pods receive their node name via the downward API, so responses still name
the node that handled them: hitting the entry point visibly alternates
between `web1` and `web2`, while the visit total climbs monotonically no
matter which pod answers — the shared DB is exactly what makes the
backends swappable.

`lb` runs the **k3s server** (tainted `CriticalAddonsOnly`, so app pods
stay on the workers) and k3s's bundled **Traefik**, which owns port 80:
the host `8080` forward is the main entry point (ingress → service → both
pods), and every node also serves Traefik on :80 — that is what the
`8081`/`8082` forwards hit. lb also still runs **dnsmasq**, which serves
the `tiket.lab` zone for all machines: pod DNS (CoreDNS → dnsmasq) and the
nodes' resolv.conf both resolve through it.

## Run it

```bash
vagrant up            # WSL vagrant — NOT vagrant.exe (see below)
./sync-keys.sh        # copy VM keys to ~/.ssh/vagrant-lab with Linux permissions
./registry/up.sh      # start the lab registry (reads the vault; see below)
vagrant provision     # run the Ansible playbook (needs ~/.vault-tiket-lab — see Secrets)
```

Verify the app through the load balancer:

```bash
for i in 1 2 3 4; do curl -s http://127.0.0.1:8080/ | grep -o '<h1>.*</h1>'; done
# <h1>Hello from web1</h1>   (each page also shows the shared visit total)
# <h1>Hello from web2</h1>
# ...
```

Verify the shared state — the node alternates while the total keeps
climbing across nodes, and both hosts appear in the one database:

```bash
for i in $(seq 1 6); do curl -s http://127.0.0.1:8080/ | grep -E '<h1>|shared'; done

vagrant ssh db -c "sudo -u postgres psql tiketdb -c 'SELECT host, COUNT(*) FROM visits GROUP BY host;'"
#  host  | count
# -------+-------
#  web1  |     3
#  web2  |     4
```

psql straight from WSL (or Windows) through the `5433` NAT forward:

```bash
psql "host=127.0.0.1 port=5433 dbname=tiketdb user=tiket password=$(ansible-vault view group_vars/all/vault.yml --vault-password-file ~/.vault-tiket-lab | tail -1 | cut -d' ' -f2)" -c '\dt'
```

Direct access to the webs (works from WSL and from a Windows browser):

```bash
curl -s http://127.0.0.1:8081/    # web1
curl -s http://127.0.0.1:8082/    # web2
```

`http://localhost:8080` / `:8081` / `:8082` also work from a Windows
browser.

DNS — query dnsmasq from WSL (needs `dnsutils`; note the non-standard
port, browsers/curl won't use it automatically):

```bash
dig @127.0.0.1 -p 5533 web1.tiket.lab        # → 192.168.56.11
```

Inside the lab, names resolve normally via resolv.conf:

```bash
vagrant ssh web1 -c 'curl -s http://web2.tiket.lab/ | grep h1'
```

And curl from WSL can use the hostname with `--resolve` (no /etc/hosts
edit needed):

```bash
curl --resolve web1.tiket.lab:8081:127.0.0.1 http://web1.tiket.lab:8081/
```

## Containerized app + registry

The app ships as a container image built from the app repo
`github.com/Raditsoic/tiket-app` (Flask + gunicorn + psycopg2; the DSN
arrives via env vars, so **the image contains no credentials** and is
safe to push). A registry container runs on the WSL/Windows side, and
the cluster pulls from it. Two modes:

- **Tunnel mode (normal)** — `./registry/up.sh --profile tunnel` with a
  named Cloudflare tunnel (token in `registry/.env`) publishes the registry
  at a real TLS hostname. Set `registry_host` in
  `group_vars/all/vars.yml` to that hostname; `registry_insecure_addresses`
  stays empty and no containerd exceptions exist.
- **Offline/NAT mode (no Cloudflare account)** — the guests reach the
  Windows host's loopback registry through the VirtualBox NAT gateway at
  `10.0.2.2:5000`. Plain HTTP, so `registry_insecure_addresses` must list
  it — the playbook writes `/etc/rancher/k3s/registries.yaml` (a
  containerd mirror + auth entry) on every node accordingly. This is the
  current default in `vars.yml`.

Build and publish a version (anywhere docker works):
```bash
docker build -t localhost:5000/tiket-app:v1 /path/to/tiket-app/
printf '%s' "$(ansible-vault view group_vars/all/vault.yml --vault-password-file ~/.vault-tiket-lab | sed -n 's/^vault_registry_password: //p')" \
  | docker login localhost:5000 -u tiket --password-stdin
docker push localhost:5000/tiket-app:v1
```

(Pushing as `localhost:5000/…` and pulling as `10.0.2.2:5000/…` hits the
same registry — the repo path is just `tiket-app`; only the transport
address differs. For tunnel mode, tag/push with the tunnel hostname.)

Deploy: bump `app_version` in `group_vars/all/vars.yml`, then
`vagrant provision` — the control plane re-applies the manifests and
containerd pulls the tag (`imagePullPolicy: Always`, so a re-pushed
`latest` lands on the next rollout). Pod DNS goes through CoreDNS → lb's
dnsmasq, so `db.tiket.lab` resolves inside pods; pod egress is SNAT'ed to
the node's `192.168.56.x` address, which the db's `pg_hba` rule already
covers. There are no liveness/readiness probes on purpose — the app's `/`
writes a `visits` row per request, so probes would fabricate rows;
rollout health is `kubectl rollout status` (what CI uses).

### Lifecycle — `vagrant up` to `vagrant destroy`

```bash
# one-time prerequisite: vault password at ~/.vault-tiket-lab (see Secrets).
# It lives on the WSL filesystem, outside the repo — destroy never touches it.

vagrant up              # create + start the VMs
./sync-keys.sh          # after every up that (re)created machines: refresh ~/.ssh/vagrant-lab
vagrant provision       # run/re-run the playbook on running VMs (needs the vault password)

# day to day
vagrant halt            # shut down, disks kept; plain `vagrant up` boots without re-provisioning
vagrant suspend         # or freeze VM state; `vagrant resume` (or `vagrant up`) continues
vagrant destroy -f      # delete VMs + disks — this is what wipes state (the visits table)
```

Rebuilding from scratch: recreated VMs get new SSH keys, so skip the
auto-provision on `up` (it would fail auth against the stale synced keys),
resync, then provision:

```bash
vagrant destroy -f
vagrant up --no-provision
./sync-keys.sh
vagrant provision
```

Notes:

- `~/.vault-tiket-lab` is machine-independent: it survives destroy/rebuild,
  and every provision needs it.
- Destroy drops the database with the VM. The playbook recreates the role
  and database, and the app recreates the `visits` table (`CREATE TABLE IF
  NOT EXISTS`) — the counter starts over at 0.
- Recreated VMs also have new SSH host keys. Ansible doesn't care (Vagrant
  runs it with host-key checking off), but plain `ssh -p 2210
  vagrant@127.0.0.1` may need `ssh-keygen -R '[127.0.0.1]:2210'` first
  (2210–2213, one per VM).

## Secrets

The db and registry passwords live vault-encrypted in `group_vars/all/vault.yml` — the
repo contains no plaintext credential. The vault password itself is
deliberately **not** in the repo: it sits at `~/.vault-tiket-lab` (mode
0600, on the WSL filesystem). It can't live under `/mnt/c`: files there are
always marked executable, and ansible-vault treats an executable password
file as a script to execute rather than a password to read.

`vagrant provision` picks the file up via the Vagrantfile provisioner
(`ansible.vault_password_file`); manual ansible runs must pass it:

```bash
ansible-playbook -i ansible_hosts playbook.yml --vault-password-file ~/.vault-tiket-lab
```

Rotate a password (edits the vault file; the next provision updates the
PostgreSQL role and every web's container DSN — for the registry password,
`./registry/up.sh` regenerates the htpasswd and a provision re-logs-in):

```bash
ansible-vault edit group_vars/all/vault.yml --vault-password-file ~/.vault-tiket-lab
vagrant provision
```

Honest caveat: the old value remains in this repo's git history — it was
committed in plaintext (in `group_vars/all.yml`, and printed in this README)
before vaulting. Vaulting keeps it out of future commits; if that history
matters, rotate as shown above.

## WSL + VirtualBox: why it's set up this way

This lab is driven from **WSL2 in mirrored networking mode**
(`wslinfo --networking-mode` → `mirrored`), with Windows-side VirtualBox.
That combination dictates everything unusual in the Vagrantfile:

- **Mirrored mode shares `127.0.0.1` between Windows and WSL**, so
  VirtualBox's NAT port forwards (bound on Windows localhost) are reachable
  from WSL. SSH/Ansible therefore go through fixed forwards `2210–2212`.
- **VirtualBox's `192.168.56.x` host-only subnet is NOT reachable from
  WSL** — the mirroring driver doesn't include Oracle's host-only adapter,
  and Windows doesn't route WSL packets into it. So the private network is
  used only guest-to-guest (lb → webs); hosts never contact it directly.
  (In WSL's default NAT mode it's the exact opposite: localhost forwards
  are unreachable from WSL, host-only works.)
- **Ports are pinned with `auto: false`** because Vagrant's default SSH
  forwards (2222, 2200, …) shift around on collisions, which would break
  the static inventory.
- **WSL Vagrant drives Windows VirtualBox** via `VBoxManage.exe`. This
  requires `VAGRANT_WSL_ENABLE_WINDOWS_ACCESS=1` in the WSL environment.
  Linux VirtualBox can't run inside WSL (no kernel modules), so this is the
  intended pattern.

### Use `vagrant`, never `vagrant.exe`

Ansible lives in WSL, and `vagrant.exe` can't use it (provisioning fails
with "The Ansible software could not be found"). Both binaries are on PATH
here, so be explicit. Mixing them also makes `.vagrant/` record conflicting
machine paths — the "machine used to live in …" warning — which is noisy
but harmless.

## How provisioning is structured

`playbook.yml` has four plays:

1. **All machines** — apt cache.
2. **dbservers** — PostgreSQL 15 listening on all interfaces, `pg_hba`
   rules admitting the app role from `192.168.56.0/24` (pod traffic to db
   arrives SNAT'd to node IPs) and `10.0.2.2/32` (WSL via the NAT
   forward), plus the `tiket` role and `tiketdb` database.
3. **loadbalancers (control plane)** — dnsmasq config, resolv.conf at the
   host-only IP (**not** 127.0.0.1: k3s bakes this file into CoreDNS's
   upstream, and CoreDNS may run on any node — the host-only address is
   reachable cluster-wide), nginx removed, containerd registry config,
   k3s server install (`--node-ip`/`--flannel-iface` pinned to the
   host-only interface, `CriticalAddonsOnly` taint, kubeconfig mode 644
   for the CI deploy user), then the app Secret/Deployment/Service/Ingress
   applied via `k3s kubectl`. Exports the agent join token to the next
   play.
4. **webservers (workers)** — docker/nginx teardown, resolv.conf at lb's
   dnsmasq with the NAT resolver as fallback, same dhclient enter-hook,
   same registry config and interface pinning, then the k3s agent join.

Shared values (`lab_domain`, PG version, db name/user) live in
`group_vars/all/vars.yml` because play-level vars don't cross plays; the db
password is vault-encrypted in the same directory (see Secrets) and stitched
in as `tiket_db.password: "{{ vault_tiket_db_password }}"`.

Two ordering details that matter:

- The **control-plane play runs before the workers**: the agent install
  consumes the join token read off lb.
- lb's dnsmasq config uses `no-resolv` + `server=10.0.2.3`, because lb's
  own resolv.conf points at dnsmasq (via the host-only IP) — without
  `no-resolv` it would loop trying to read its own forwarder config.

## Files

| File                          | Purpose                                                        |
| ----------------------------- | -------------------------------------------------------------- |
| `Vagrantfile`                 | VM definitions, port forwards, WSL-aware provisioning           |
| `playbook.yml`                | 4 plays: common, db (PostgreSQL), control plane (k3s server + dnsmasq + manifests), workers (k3s agents) |
| `ansible_hosts`               | static inventory (WSL only): hosts at `127.0.0.1:221x`, keys at `~/.ssh/vagrant-lab/`, plus `private_ip` vars |
| `ansible.cfg`                 | host key checking off (VMs are rebuilt often)                    |
| `group_vars/all/vars.yml`     | shared values: `lab_domain`, PG version, db name/user, registry/image vars |
| `group_vars/all/vault.yml`    | ansible-vault-encrypted db + registry passwords                        |
| `templates/registries.yaml.j2` | containerd registry config (mirror + auth) written to every node |
| `registry/compose.yaml`, `registry/up.sh` | lab image registry (+ cloudflared tunnel profile) and its bootstrap |
| `requirements.yml`            | Ansible collections (`community.postgresql`)                            |
| `templates/tiket-k8s/*.yaml.j2` | app workload manifests (Secret, Deployment, Service, Ingress) applied on lb |
| `templates/dnsmasq.conf.j2`   | lab DNS zone + upstream forwarding                               |
| `sync-keys.sh`                | copies Vagrant keys from `/mnt/c` to WSL fs so chmod 600 works   |

Note: every cross-node reference (agent→server join URL, CoreDNS's DNS
upstream, resolv.conf entries) is built from each host's `private_ip`
inventory var — **not** `ansible_host`, which is `127.0.0.1` here (and
also in Vagrant's auto-generated inventory) and only means something
through the WSL NAT forwards.

## CI/CD (Jenkins)

- App repo: `github.com/Raditsoic/tiket-app` (moved out of this repo; this
  repo's playbook owns the cluster and the workload manifests).
- Every push: GitHub webhook → Jenkins (`jenkins` container on host 8085,
  fronted by the `jenkins-tunnel` cloudflared container) builds and pushes
  `localhost:5000/tiket-app:<branch>-<build>`; on `main` it also pushes
  `latest` and rolls the cluster onto the exact `main-N` tag over SSH:
  `kubectl set image deployment/tiket-app …` + `kubectl rollout status` on
  lb (port 2210), then a health curl through the 8080 forward. Rollback =
  `set image` back to an older `main-N`.
- Registry auth for pulls lives in every node's
  `/etc/rancher/k3s/registries.yaml` (written by the playbook from the
  vault). The deploy SSH key goes into **lb's** root `authorized_keys`;
  plain `kubectl` works for it because the server is installed with
  `--write-kubeconfig-mode 644`. A lb rebuild needs the plays re-run plus
  the deploy pubkey `~/.ssh/tiket-deploy-jenkins.pub` re-installed.
- UI: http://127.0.0.1:8085 (host) / https://jenkins.spacetrek.xyz (tunnel).
- `app_version` in `group_vars/all/vars.yml` stays `latest` for
  provision-time deploys; CI deploys pin exact `main-N` tags.

## Troubleshooting

- **`Permission denied (publickey)` from Ansible** — a new machine was
  created (new keypair) since the last sync. Re-run `./sync-keys.sh`.
  Keys under `/mnt/c` can't hold Linux permissions, hence the WSL copies.
- **Ansible times out on all hosts** — if WSL is switched back to NAT
  networking, `127.0.0.1` no longer reaches Windows' port forwards and this
  whole layout needs the host-only IPs instead.
- **`UNPROTECTED PRIVATE KEY FILE` for `.vagrant/machines/.../private_key`**
  — same /mnt/c permissions issue; the fix is the same `sync-keys.sh`.
- **Port 221x/808x already in use** — something else on Windows grabbed it;
  the `auto: false` forwards will error rather than silently move.
- **Pod not serving / app 502s** — `vagrant ssh lb -c 'sudo k3s kubectl
  get pods -o wide'`, then `sudo k3s kubectl logs deploy/tiket-app`: the
  logs show the psycopg2 error (DNS? pg_hba? password?). `kubectl
  describe pod` shows scheduling/pull problems.
- **`pull access denied` / 401 from the registry** — the vault's registry
  password and the registry's htpasswd disagree; re-run `./registry/up.sh`
  (regenerates the hash) then `vagrant provision`.
- **`http: server gave HTTP response to HTTPS client`** — pulling the
  plain-HTTP registry without the containerd mirror: `registry_insecure_addresses`
  in `group_vars/all/vars.yml` must list it (offline/NAT mode), or front
  the registry with the tunnel for real TLS.
- **Webs can't resolve names while lb is halted** — their resolv.conf
  lists the NAT resolver as fallback, so apt still works, but `*.tiket.lab`
  names only exist while lb's dnsmasq is running.
- **App returns 500s** — a pod can't reach or authenticate to the
  database. Check in order: `dig db.tiket.lab` from a web (dnsmasq up?),
  `systemctl status postgresql` on db, and the `pg_hba` rules in
  `/etc/postgresql/15/main/pg_hba.conf` (the app's IP must match a `host
  tiketdb tiket` line).
- **App 500s "out of nowhere" after hours of uptime** — DHCP lease renewal
  rewrites the webs' resolv.conf with the NAT resolver, dropping the lab
  nameservers. The playbook installs a no-op hook at
  `/etc/dhcp/dhclient-enter-hooks.d/tiket-dns` (`make_resolv_conf() { :; }`)
  on lb and the webs so resolv.conf stays exactly as Ansible wrote it.
- **`Attempting to decrypt but no vault password found` / provision
  aborts on `group_vars/all/vault.yml`** — `~/.vault-tiket-lab` is missing
  (fresh clone, new machine). Recreate it with the same content, or
  re-encrypt the vault file with a new password (see Secrets). A
  vault-password file under `/mnt/c` will never work — see Secrets for why.
- **RAM** — lb runs at 2 GB (k3s server + Traefik + CoreDNS), the webs at
  1 GB each; lower `vb.memory` in the Vagrantfile or shrink lb to 1.5 GB
  if the host is tight (the db VM is the other candidate).
