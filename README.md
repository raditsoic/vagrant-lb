# vagrant — nginx load balancer lab

Four Debian 12 VMs (VirtualBox, managed by Vagrant, provisioned by Ansible):

| Machine | Private IP      | Role                                  | NAT forward (127.0.0.1)             |
| ------- | --------------- | ------------------------------------- | ----------------------------------- |
| `lb`    | `192.168.56.10` | nginx LB + dnsmasq DNS (`tiket.lab`)  | SSH `2210`, HTTP `8080`, DNS `5533` |
| `web1`  | `192.168.56.11` | Flask app + gunicorn, nginx           | SSH `2211`, HTTP `8081`             |
| `web2`  | `192.168.56.12` | Flask app + gunicorn, nginx           | SSH `2212`, HTTP `8082`             |
| `db`    | `192.168.56.20` | PostgreSQL 15 (shared visits DB)      | SSH `2213`, psql `5433`             |

`lb` proxies port 80 round-robin across the two webs over the private
host-only network. Each web runs the **same** Flask app (under gunicorn),
and all webs write to **one shared PostgreSQL** on `db.tiket.lab` — the
production topology: interchangeable stateless app VMs plus a dedicated
database VM. Responses still name the node that handled them, so hitting
the load balancer visibly alternates between `web1` and `web2`, but the
visit total now climbs monotonically no matter which node answers — the
shared DB is exactly what makes the backends swappable.

`lb` also runs **dnsmasq**, which serves the `tiket.lab` zone for all
machines in the lab and forwards everything else to VirtualBox NAT's
resolver. The webs resolve through it, and lb's nginx config uses DNS
names (`web1.tiket.lab:80`, …) for its upstreams instead of IPs.

## Run it

```bash
vagrant up            # WSL vagrant — NOT vagrant.exe (see below)
./sync-keys.sh        # copy VM keys to ~/.ssh/vagrant-lab with Linux permissions
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

The db password lives vault-encrypted in `group_vars/all/vault.yml` — the
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

Rotate the password (edits the vault file, then one provision updates the
PostgreSQL role and every web's DSN and restarts the apps):

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

`playbook.yml` has five plays:

1. **All machines** — apt cache.
2. **webservers:loadbalancers** — nginx installed/started/enabled. Not on
   dbservers: db runs no web server (the db play removes the one an older
   all-hosts play left behind).
3. **dbservers** — PostgreSQL 15 listening on all interfaces, `pg_hba`
   rules admitting the app role from `192.168.56.0/24` (the webs) and
   `10.0.2.2/32` (WSL via the NAT forward), plus the `tiket` role and
   `tiketdb` database.
4. **webservers** — Flask app + gunicorn (`/opt/tiket/app.py`, systemd unit
   `tiket-app`; the app connects to `db.tiket.lab` over psycopg2), nginx
   site proxying to `127.0.0.1:8000`, resolv.conf pointed at lb's dnsmasq
   with the NAT resolver as fallback, plus a dhclient enter-hook that keeps
   resolv.conf authoritative across DHCP renewals.
5. **loadbalancers** — dnsmasq config for `tiket.lab`, resolv.conf at
   `127.0.0.1`, nginx LB config with DNS-named upstreams, same dhclient
   enter-hook.

Shared values (`lab_domain`, PG version, db name/user) live in
`group_vars/all/vars.yml` because play-level vars don't cross plays; the db
password is vault-encrypted in the same directory (see Secrets) and stitched
in as `tiket_db.password: "{{ vault_tiket_db_password }}"`.

Two ordering details that matter:

- On lb, handlers run **dnsmasq before nginx** — nginx resolves upstream
  names once at start/reload, so dnsmasq must already be answering.
- lb's dnsmasq config uses `no-resolv` + `server=10.0.2.3`, because lb's
  own resolv.conf points at `127.0.0.1` (dnsmasq itself) — without
  `no-resolv` it would loop trying to read its own forwarder config.

## Files

| File                          | Purpose                                                        |
| ----------------------------- | -------------------------------------------------------------- |
| `Vagrantfile`                 | VM definitions, port forwards, WSL-aware provisioning           |
| `playbook.yml`                | 5 plays: common, nginx (webs+lb), db (PostgreSQL), webs (Flask), lb (dnsmasq+LB) |
| `ansible_hosts`               | static inventory (WSL only): hosts at `127.0.0.1:221x`, keys at `~/.ssh/vagrant-lab/`, plus `private_ip` vars |
| `ansible.cfg`                 | host key checking off (VMs are rebuilt often)                    |
| `group_vars/all/vars.yml`     | shared plaintext values: `lab_domain`, PG version, tiket db name/user |
| `group_vars/all/vault.yml`    | ansible-vault-encrypted db password (`vault_tiket_db_password`)        |
| `templates/app.py.j2`         | Flask app: visit counter in the shared PostgreSQL on `db.tiket.lab` |
| `templates/tiket-app.service.j2` | systemd unit running gunicorn as www-data                     |
| `templates/web-site.conf.j2`  | per-web nginx site → proxy to local gunicorn                     |
| `templates/loadbalancer.conf.j2` | lb nginx config, upstreams by DNS name                        |
| `templates/dnsmasq.conf.j2`   | lab DNS zone + upstream forwarding                               |
| `sync-keys.sh`                | copies Vagrant keys from `/mnt/c` to WSL fs so chmod 600 works   |

Note: the LB upstream list uses DNS names built from each host's
`private_ip` inventory var — **not** `ansible_host`, which is `127.0.0.1`
here (and also in Vagrant's auto-generated inventory). Using `ansible_host`
would make lb proxy to itself.

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
- **gunicorn fails with 203/EXEC** — the executable isn't in
  `python3-gunicorn` on Debian 12 (libs only); the playbook installs
  `gunicorn` for the binary.
- **Webs can't resolve names while lb is halted** — their resolv.conf
  lists the NAT resolver as fallback, so apt still works, but `*.tiket.lab`
  names only exist while lb's dnsmasq is running.
- **App returns 500s** — the web can't reach or authenticate to the
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
- **RAM** — four VMs at 1 GB each; lower `vb.memory` in the Vagrantfile if
  the host is tight (the db VM is the best candidate to shrink).
