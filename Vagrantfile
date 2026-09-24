Vagrant.configure("2") do |config|
  config.vm.box = "generic/debian12"

  config.vm.provider "virtualbox" do |vb|
    vb.memory = 1024
    vb.cpus = 1
  end

  # The private network is guest-to-guest only (lb proxies to the webs).
  # Host-to-guest SSH goes over fixed NAT forwards on 127.0.0.1: WSL runs in
  # mirrored networking mode, where localhost is shared with Windows but
  # VirtualBox's 192.168.56.x host-only subnet has no route from WSL. The
  # fixed ports also keep the addresses stable for the static Ansible
  # inventory (Vagrant's default 2222/2200 forwards shift on collisions).
  wsl = Vagrant::Util::Platform.wsl?

  config.vm.define "lb" do |lb|
    lb.vm.network "private_network", ip: "192.168.56.10"
    lb.vm.hostname = "lb"
    lb.vm.network "forwarded_port", guest: 22, host: 2210, host_ip: "127.0.0.1", auto: false
    lb.vm.network "forwarded_port", guest: 80, host: 8080, host_ip: "127.0.0.1", auto: false
    # dnsmasq — queryable from WSL with: dig @127.0.0.1 -p 5533 web1.tiket.lab
    # (5533, NOT 5353: Windows mDNS squats UDP 5353 and swallows the packets)
    lb.vm.network "forwarded_port", guest: 53, host: 5533, host_ip: "127.0.0.1", protocol: "udp", auto: false, id: "dns-udp"
    lb.vm.network "forwarded_port", guest: 53, host: 5533, host_ip: "127.0.0.1", protocol: "tcp", auto: false, id: "dns-tcp"
  end

  config.vm.define "web1" do |web1|
    web1.vm.network "private_network", ip: "192.168.56.11"
    web1.vm.hostname = "web1"
    web1.vm.network "forwarded_port", guest: 22, host: 2211, host_ip: "127.0.0.1", auto: false
    web1.vm.network "forwarded_port", guest: 80, host: 8081, host_ip: "127.0.0.1", auto: false
  end

  config.vm.define "web2" do |web2|
    web2.vm.network "private_network", ip: "192.168.56.12"
    web2.vm.hostname = "web2"
    web2.vm.network "forwarded_port", guest: 22, host: 2212, host_ip: "127.0.0.1", auto: false
    web2.vm.network "forwarded_port", guest: 80, host: 8082, host_ip: "127.0.0.1", auto: false
  end

  config.vm.define "db" do |db|
    db.vm.network "private_network", ip: "192.168.56.20"
    db.vm.hostname = "db"
    db.vm.network "forwarded_port", guest: 22, host: 2213, host_ip: "127.0.0.1", auto: false
    # psql straight from WSL/Windows through the NAT forward:
    #   psql "host=127.0.0.1 port=5433 dbname=tiketdb user=tiket password=tiket-lab"
    db.vm.network "forwarded_port", guest: 5432, host: 5433, host_ip: "127.0.0.1", auto: false
  end

  config.vm.provision "ansible" do |ansible|
    ansible.playbook = "playbook.yml"
    if wsl
      # Vagrant's auto-generated inventory points at keys on /mnt/c, which
      # can't have Linux permissions — use our static inventory instead.
      ansible.inventory_path = "ansible_hosts"
    else
      ansible.groups = {
        "loadbalancers" => ["lb"],
        "webservers"    => ["web1", "web2"],
        "dbservers"     => ["db"],
      }
    end
  end
end