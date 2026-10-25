"""Repair the TARS LAN binding on sasmegration; never apply OS network settings."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def candidate_config(original, device, address):
    cfg = copy.deepcopy(original)
    mappings = cfg.setdefault('interfaces', {})
    for name, physical in mappings.items():
        if name.startswith('wan') and physical == device:
            raise ValueError('LAN interface is already assigned to ' + name)
    mappings['lan'] = device
    lan = cfg.setdefault('interfaces_config', {}).setdefault('lan', {})
    lan.update(proto='static', ip=str(address.ip), netmask=str(address.network.prefixlen))
    lan.setdefault('dns', '1.1.1.1,8.8.8.8')
    return cfg


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo python3')
    device = 'ens32'
    address = ipaddress.IPv4Interface('192.168.200.2/24')
    path = Path('/etc/tars/config.json')
    if Path('/etc/tars/wan-pending.json').exists():
        raise SystemExit('Wait for the pending WAN operation to finish first.')
    rows = json.loads(subprocess.check_output(
        ['ip', '-j', '-4', 'addr', 'show', 'dev', device], text=True))
    if not any(a.get('local') == str(address.ip) and a.get('prefixlen') == address.network.prefixlen
               for row in rows for a in row.get('addr_info', [])):
        raise SystemExit('ens32 does not have the expected 192.168.200.2/24. No changes made.')
    raw = path.read_bytes()
    cfg = candidate_config(json.loads(raw), device, address)
    sys.path.insert(0, '/opt/tars/cpe')
    import network_engine
    import license_manager
    lan, links = network_engine.validate(cfg)
    backup = Path('/var/backups/tars') / ('lan-ens32-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(path, backup / 'config.json')
    attrs = path.stat()
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            staged = Path(stream.name)
            os.chmod(staged, stat.S_IMODE(attrs.st_mode))
            os.chown(staged, attrs.st_uid, attrs.st_gid)
            json.dump(cfg, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != raw:
            raise RuntimeError('Configuration changed during repair; no changes made. Retry after WAN saves finish.')
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    print('LAN corrected:', lan, str(address))
    print('Backup:', backup / 'config.json')
    print('Configured WANs:', ', '.join(w['name'] + '=' + w['physical'] for w in links) or 'none')
    if links and license_manager.get_current_license_status().get('is_activated'):
        subprocess.run(['systemctl', 'enable', '--now', 'tars-network.service'], check=True)
        print('Engine enabled; refresh the dashboard after 15 seconds.')
    else:
        print('Configure WAN lines and activate this device license to enable forwarding.')
    print('No IP, gateway, Netplan, MAC address or PPP session was changed.')


if __name__ == '__main__':
    main()
