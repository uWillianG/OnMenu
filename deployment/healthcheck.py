import os
import urllib.request
from urllib.parse import urlsplit

domain = urlsplit(os.environ.get('BASE_URL', 'https://localhost')).netloc
request = urllib.request.Request('http://127.0.0.1:8000/health/',
    headers={'Host':domain, 'X-Forwarded-Proto':'https'})
with urllib.request.urlopen(request, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(1)
