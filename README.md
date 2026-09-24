# vagrant — nginx load balancer lab

Three Debian 12 VMs (VirtualBox, managed by Vagrant, provisioned by Ansible):

| Machine | Private IP      | Role        | NAT forward (127.0.0.1) |
| ------- | --------------- | ----------- | ----------------------- |
| `lb`    | `192.168.56.10` | nginx LB    | SSH `2210`, HTTP `8080` |
| `web1`  | `192.168.56.11` | nginx web   | SSH `2211`              |
| `web2`  | `192.168.56.12` | nginx web   | SSH `2212`              |

`lb` proxies port 80 round-robin across the two webs over the private
host-only network. Each web serves an index page identifying itself, so
hitting the load balancer alternates between `web1` and `web2`.

## Run it

```bash
vagrant up            # WSL vagrant — NOT vagrant.exe (see below)
./sync-keys.sh        # copy VM keys to ~/.ssh/vagrant-lab with Linux permissions
vagrant provision     # run the Ansible playbook
```

Verify:

```bash
for i in 1 2 3 4; do curl -s http://127.0.0.1:8080/ | grep -o '<h1>.*</h1>'; done
# <h1>Hello from web1</h1>
# <h1>Hello from web2</h1>
# ...
```

`http://localhost:8080` also works from a Windows browser.

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

## Files

| File                          | Purpose                                                        |
| ----------------------------- | -------------------------------------------------------------- |
| `Vagrantfile`                 | VM definitions, port forwards, WSL-aware provisioning           |
| `playbook.yml`                | installs nginx everywhere, configures webs + lb                  |
| `ansible_hosts`               | static inventory (WSL only): hosts at `127.0.0.1:221x`, keys at `~/.ssh/vagrant-lab/`, plus `private_ip` vars |
| `ansible.cfg`                 | host key checking off (VMs are rebuilt often)                    |
| `templates/`                  | `index.html.j2`, `loadbalancer.conf.j2`                           |
| `sync-keys.sh`                | copies Vagrant keys from `/mnt/c` to WSL fs so chmod 600 works   |

Note: `loadbalancer.conf.j2` builds its upstream from each webserver's
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
- **Port 221x/8080 already in use** — something else on Windows grabbed it;
  the `auto: false` forwards will error rather than silently move.
