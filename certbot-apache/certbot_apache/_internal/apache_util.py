""" Utility functions for certbot-apache plugin """
import binascii
import struct
import time

import six

from certbot import crypto_util
from certbot import util
from certbot.compat import os


def get_apache_ocsp_struct(ttl, ocsp_response):
    """Create Apache OCSP response structure to be used in response cache

    :param int ttl: Time-To-Live in seocnds
    :param str ocsp_response: OCSP response data

    :returns: Apache OCSP structure
    :rtype: `str`

    """
    ttl = time.time() + ttl
    # As microseconds
    ttl_struct = struct.pack('l', int(ttl*1000000))
    return b'\x01'.join([ttl_struct, ocsp_response])

def certid_sha1_hex(cert_path):
    """Hex representation of certificate SHA1 fingerprint

    :param str cert_path: File path to certificate

    :returns: Hex representation SHA1 fingerprint of certificate
    :rtype: `str`

    """
    sha1_hex = binascii.hexlify(certid_sha1(cert_path))
    if isinstance(sha1_hex, six.binary_type):
        return sha1_hex.decode('utf-8')  # pragma: no cover
    return sha1_hex  # pragma: no cover


def certid_sha1(cert_path):
    """SHA1 fingerprint of certificate

    :param str cert_path: File path to certificate

    :returns: SHA1 fingerprint bytestring
    :rtype: `str`

    """
    return crypto_util.cert_sha1_fingerprint(cert_path)

def get_mod_deps(mod_name):
    """Get known module dependencies.

    .. note:: This does not need to be accurate in order for the client to
        run.  This simply keeps things clean if the user decides to revert
        changes.
    .. warning:: If all deps are not included, it may cause incorrect parsing
        behavior, due to enable_mod's shortcut for updating the parser's
        currently defined modules (`.ApacheParser.add_mod`)
        This would only present a major problem in extremely atypical
        configs that use ifmod for the missing deps.

    """
    deps = {
        "ssl": ["setenvif", "mime"]
    }
    return deps.get(mod_name, [])


def get_file_path(vhost_path):
    """Get file path from augeas_vhost_path.

    Takes in Augeas path and returns the file name

    :param str vhost_path: Augeas virtual host path

    :returns: filename of vhost
    :rtype: str

    """
    if not vhost_path or not vhost_path.startswith("/files/"):
        return None

    return _split_aug_path(vhost_path)[0]


def get_internal_aug_path(vhost_path):
    """Get the Augeas path for a vhost with the file path removed.

    :param str vhost_path: Augeas virtual host path

    :returns: Augeas path to vhost relative to the containing file
    :rtype: str

    """
    return _split_aug_path(vhost_path)[1]


def _split_aug_path(vhost_path):
    """Splits an Augeas path into a file path and an internal path.

    After removing "/files", this function splits vhost_path into the
    file path and the remaining Augeas path.

    :param str vhost_path: Augeas virtual host path

    :returns: file path and internal Augeas path
    :rtype: `tuple` of `str`

    """
    # Strip off /files
    file_path = vhost_path[6:]
    internal_path = []

    # Remove components from the end of file_path until it becomes valid
    while not os.path.exists(file_path):
        file_path, _, internal_path_part = file_path.rpartition("/")
        internal_path.append(internal_path_part)

    return file_path, "/".join(reversed(internal_path))


def parse_define_file(filepath, varname):
    """ Parses Defines from a variable in configuration file

    :param str filepath: Path of file to parse
    :param str varname: Name of the variable

    :returns: Dict of Define:Value pairs
    :rtype: `dict`

    """
    return_vars = {}
    # Get list of words in the variable
    a_opts = util.get_var_from_file(varname, filepath).split()
    for i, v in enumerate(a_opts):
        # Handle Define statements and make sure it has an argument
        if v == "-D" and len(a_opts) >= i+2:
            var_parts = a_opts[i+1].partition("=")
            return_vars[var_parts[0]] = var_parts[2]
        elif len(v) > 2 and v.startswith("-D"):
            # Found var with no whitespace separator
            var_parts = v[2:].partition("=")
            return_vars[var_parts[0]] = var_parts[2]
    return return_vars


def unique_id():
    """ Returns an unique id to be used as a VirtualHost identifier"""
    return binascii.hexlify(os.urandom(16)).decode("utf-8")
