from urllib.parse import urlparse
import python_socks

def parse_proxy(proxy_url: str) -> tuple | dict | None:
    """
    Parses a proxy URL into a tuple suitable for Telethon/python-socks.
    Format: socks5://user:pass@host:port or http://user:pass@host:port
    """
    if not proxy_url:
        return None
    
    try:
        parsed = urlparse(proxy_url)
        scheme = parsed.scheme.lower()
        if scheme in ('socks5', 'socks5h'):
            proxy_type = python_socks.ProxyType.SOCKS5
        elif scheme in ('socks4', 'socks4a'):
            proxy_type = python_socks.ProxyType.SOCKS4
        elif scheme in ('http', 'https'):
            proxy_type = python_socks.ProxyType.HTTP
        else:
            return None

        host = parsed.hostname
        port = parsed.port
        username = parsed.username or ''
        password = parsed.password or ''
        rdns = scheme in ('socks5h', 'socks4a')

        if not host or not port:
            return None

        # Telethon proxy tuple format: (proxy_type, host, port, rdns, username, password)
        return (proxy_type, host, port, rdns, username, password)
    except Exception:
        return None
