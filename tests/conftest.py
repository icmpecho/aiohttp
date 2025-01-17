import os
import socket
import ssl
import sys
from hashlib import md5, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from uuid import uuid4

import pytest

try:
    import trustme
    TRUSTME = True
except ImportError:
    TRUSTME = False

pytest_plugins = ['aiohttp.pytest_plugin', 'pytester']

IS_HPUX = sys.platform.startswith('hp-ux')
# Specifies whether the current runtime is HP-UX.
IS_LINUX = sys.platform.startswith('linux')
# Specifies whether the current runtime is HP-UX.
IS_UNIX = hasattr(socket, 'AF_UNIX')
# Specifies whether the current runtime is *NIX.

needs_unix = pytest.mark.skipif(not IS_UNIX, reason='requires UNIX sockets')


@pytest.fixture
def tls_certificate_authority():
    if not TRUSTME:
        pytest.xfail("trustme fails on 32bit Linux")
    return trustme.CA()


@pytest.fixture
def tls_certificate(tls_certificate_authority):
    return tls_certificate_authority.issue_server_cert(
        'localhost',
        '127.0.0.1',
        '::1',
    )


@pytest.fixture
def ssl_ctx(tls_certificate):
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
    tls_certificate.configure_cert(ssl_ctx)
    return ssl_ctx


@pytest.fixture
def client_ssl_ctx(tls_certificate_authority):
    ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    tls_certificate_authority.configure_trust(ssl_ctx)
    return ssl_ctx


@pytest.fixture
def tls_ca_certificate_pem_path(tls_certificate_authority):
    with tls_certificate_authority.cert_pem.tempfile() as ca_cert_pem:
        yield ca_cert_pem


@pytest.fixture
def tls_certificate_pem_path(tls_certificate):
    with tls_certificate.private_key_and_cert_chain_pem.tempfile() as cert_pem:
        yield cert_pem


@pytest.fixture
def tls_certificate_pem_bytes(tls_certificate):
    return tls_certificate.cert_chain_pems[0].bytes()


@pytest.fixture
def tls_certificate_fingerprint_sha256(tls_certificate_pem_bytes):
    tls_cert_der = ssl.PEM_cert_to_DER_cert(tls_certificate_pem_bytes.decode())
    return sha256(tls_cert_der).digest()


@pytest.fixture
def pipe_name():
    name = r'\\.\pipe\{}'.format(uuid4().hex)
    return name


@pytest.fixture
def create_mocked_conn(loop):
    def _proto_factory(conn_closing_result=None, **kwargs):
        proto = mock.Mock(**kwargs)
        proto.closed = loop.create_future()
        proto.closed.set_result(conn_closing_result)
        return proto

    yield _proto_factory


@pytest.fixture
def unix_sockname(tmp_path, tmp_path_factory):
    # Generate an fs path to the UNIX domain socket for testing.

    # N.B. Different OS kernels have different fs path length limitations
    # for it. For Linux, it's 108, for HP-UX it's 92 (or higher) depending
    # on its version. For for most of the BSDs (Open, Free, macOS) it's
    # mostly 104 but sometimes it can be down to 100.

    # Ref: https://github.com/aio-libs/aiohttp/issues/3572
    if not IS_UNIX:
        pytest.skip('requires UNIX sockets')

    max_sock_len = 92 if IS_HPUX else 108 if IS_LINUX else 100
    # Amount of bytes allocated for the UNIX socket path by OS kernel.
    # Ref: https://unix.stackexchange.com/a/367012/27133

    sock_file_name = 'unix.sock'
    unique_prefix = '{!s}-'.format(uuid4())
    unique_prefix_len = len(unique_prefix.encode())

    root_tmp_dir = Path('/tmp').resolve()
    os_tmp_dir = Path(os.getenv('TMPDIR', '/tmp')).resolve()
    original_base_tmp_path = Path(
        str(tmp_path_factory.getbasetemp()),
    ).resolve()

    original_base_tmp_path_hash = md5(
        str(original_base_tmp_path).encode(),
    ).hexdigest()

    def make_tmp_dir(base_tmp_dir):
        return TemporaryDirectory(
            dir=str(base_tmp_dir),
            prefix='pt-',
            suffix='-{!s}'.format(original_base_tmp_path_hash),
        )

    def assert_sock_fits(sock_path):
        sock_path_len = len(sock_path.encode())
        # exit-check to verify that it's correct and simplify debugging
        # in the future
        assert sock_path_len <= max_sock_len, (
            'Suggested UNIX socket ({sock_path}) is {sock_path_len} bytes '
            'long but the current kernel only has {max_sock_len} bytes '
            'allocated to hold it so it must be shorter. '
            'See https://github.com/aio-libs/aiohttp/issues/3572 '
            'for more info.'
        ).format_map(locals())

    paths = original_base_tmp_path, os_tmp_dir, root_tmp_dir
    unique_paths = [p for n, p in enumerate(paths) if p not in paths[:n]]
    paths_num = len(unique_paths)

    for num, tmp_dir_path in enumerate(paths, 1):
        with make_tmp_dir(tmp_dir_path) as tmpd:
            tmpd = Path(tmpd).resolve()
            sock_path = str(tmpd / sock_file_name)
            sock_path_len = len(sock_path.encode())

            if num >= paths_num:
                # exit-check to verify that it's correct and simplify
                # debugging in the future
                assert_sock_fits(sock_path)

            if sock_path_len <= max_sock_len:
                if max_sock_len - sock_path_len >= unique_prefix_len:
                    # If we're lucky to have extra space in the path,
                    # let's also make it more unique
                    sock_path = str(
                        tmpd / ''.join((unique_prefix, sock_file_name))
                    )
                    # Double-checking it:
                    assert_sock_fits(sock_path)
                yield sock_path
                return
