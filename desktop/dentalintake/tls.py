"""The office's own HTTPS certificates.

On first start the server creates a small certificate authority (CA) for this
office and uses it to issue the HTTPS certificate for the office PC. The
installer adds the CA to the "Trusted Root" store on every office PC, so
browsers show a normal padlock without warnings.

Defense in depth: the CA carries X.509 *name constraints*, so it can only ever
vouch for this PC's names and private (office network) IP addresses. Even if
its key leaked, it could not be used to impersonate a bank or any public site.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_NETS = [ipaddress.ip_network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16")]
SERVER_CERT_DAYS = 825
RENEW_BEFORE_DAYS = 60


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def lan_ip() -> str | None:
    """This PC's primary IPv4 address on the office network (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


def local_names(extra: list[str] | None = None) -> tuple[list[str], list[str]]:
    host = socket.gethostname().split(".")[0]
    dns = [host, host.lower(), f"{host.lower()}.local", "localhost", *(extra or [])]
    ips = {"127.0.0.1"}
    if (ip := lan_ip()):
        ips.add(ip)
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    dns_clean = sorted({d for d in dns if d and not _is_ip(d)})
    ips |= {d for d in (extra or []) if _is_ip(d)}
    # Only addresses the CA is permitted to vouch for (office/private networks).
    return dns_clean, sorted(i for i in ips if _in_private_nets(i))


def _in_private_nets(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return any(addr in n for n in PRIVATE_NETS)


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def fingerprint(path: Path) -> str:
    """Short security code staff compare when installing on another PC."""
    h = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return f"{h[:4]}-{h[4:8]}-{h[8:12]}"


def ensure_ca(tls_dir: Path, office_name: str, dns_names: list[str]) -> tuple[Path, Path]:
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_p, key_p = tls_dir / "office-ca.crt", tls_dir / "office-ca.key"
    if cert_p.exists() and key_p.exists():
        return cert_p, key_p
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{office_name[:40]} Dental Intake CA"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, office_name[:64])])
    permitted = [x509.DNSName(d) for d in sorted(set(dns_names))] + [x509.IPAddress(n) for n in PRIVATE_NETS]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(days=1))
        .not_valid_after(_now() + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p


def _write_private(p: Path, data: bytes) -> None:
    import os

    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _ca_permits(ca: x509.Certificate, dns: list[str]) -> list[str]:
    try:
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return dns
    allowed = {d.value.lower() for d in nc.permitted_subtrees or [] if isinstance(d, x509.DNSName)}
    return [d for d in dns if any(d.lower() == a or d.lower().endswith("." + a) for a in allowed)]


def ensure_server_cert(tls_dir: Path, office_name: str, extra: list[str] | None = None) -> tuple[Path, Path]:
    """(Re)issue the HTTPS certificate when missing, expiring, or the PC's names/IPs changed."""
    dns, ips = local_names(extra)
    ca_cert_p, ca_key_p = ensure_ca(tls_dir, office_name, dns)
    ca = x509.load_pem_x509_certificate(ca_cert_p.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_p.read_bytes(), password=None)
    dns = _ca_permits(ca, dns)  # names added after the CA was made need a new CA; skip them
    cert_p, key_p = tls_dir / "server.crt", tls_dir / "server.key"
    if cert_p.exists() and key_p.exists():
        cur = x509.load_pem_x509_certificate(cert_p.read_bytes())
        san = cur.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = set(san.get_values_for_type(x509.DNSName)) | {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        fresh = cur.not_valid_after_utc - _now() > dt.timedelta(days=RENEW_BEFORE_DAYS)
        if fresh and set(dns) | set(ips) <= have:
            return cert_p, key_p
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns[0] if dns else "localhost")]))
        .issuer_name(ca.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(days=1))
        .not_valid_after(_now() + dt.timedelta(days=SERVER_CERT_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p
