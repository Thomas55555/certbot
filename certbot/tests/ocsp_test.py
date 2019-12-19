"""Tests for ocsp.py"""
# pylint: disable=protected-access
import contextlib
import unittest
from datetime import datetime, timedelta

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes  # type: ignore
from cryptography.exceptions import UnsupportedAlgorithm, InvalidSignature
from cryptography import x509
try:
    # Only cryptography>=2.5 has ocsp module
    # and signature_hash_algorithm attribute in OCSPResponse class
    from cryptography.x509 import ocsp as ocsp_lib  # pylint: disable=import-error
    getattr(ocsp_lib.OCSPResponse, 'signature_hash_algorithm')
except (ImportError, AttributeError):  # pragma: no cover
    ocsp_lib = None  # type: ignore
import mock
import pytz

from certbot import errors
from certbot.tests import util as test_util

out = """Missing = in header key=value
ocsp: Use -help for summary.
"""


class OCSPTestOpenSSL(unittest.TestCase):
    """
    OCSP revokation tests using OpenSSL binary.
    """

    def setUp(self):
        from certbot._internal import ocsp
        with mock.patch('certbot._internal.ocsp.Popen') as mock_popen:
            with mock.patch('certbot.util.exe_exists') as mock_exists:
                mock_communicate = mock.MagicMock()
                mock_communicate.communicate.return_value = (None, out)
                mock_popen.return_value = mock_communicate
                mock_exists.return_value = True
                self.checker = ocsp.RevocationChecker(enforce_openssl_binary_usage=True)

    def tearDown(self):
        pass

    @mock.patch('certbot._internal.ocsp.logger.info')
    @mock.patch('certbot._internal.ocsp.Popen')
    @mock.patch('certbot.util.exe_exists')
    def test_init(self, mock_exists, mock_popen, mock_log):
        mock_communicate = mock.MagicMock()
        mock_communicate.communicate.return_value = (None, out)
        mock_popen.return_value = mock_communicate
        mock_exists.return_value = True

        from certbot._internal import ocsp
        checker = ocsp.RevocationChecker(enforce_openssl_binary_usage=True)
        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(checker.host_args("x"), ["Host=x"])

        mock_communicate.communicate.return_value = (None, out.partition("\n")[2])
        checker = ocsp.RevocationChecker(enforce_openssl_binary_usage=True)
        self.assertEqual(checker.host_args("x"), ["Host", "x"])
        self.assertEqual(checker.broken, False)

        mock_exists.return_value = False
        mock_popen.call_count = 0
        checker = ocsp.RevocationChecker(enforce_openssl_binary_usage=True)
        self.assertEqual(mock_popen.call_count, 0)
        self.assertEqual(mock_log.call_count, 1)
        self.assertEqual(checker.broken, True)

    @mock.patch('certbot._internal.ocsp._determine_ocsp_server')
    @mock.patch('certbot.util.run_script')
    def test_ocsp_revoked(self, mock_run, mock_determine):
        now = pytz.UTC.fromutc(datetime.utcnow())
        cert_obj = mock.MagicMock()
        cert_obj.cert = "x"
        cert_obj.chain = "y"
        cert_obj.target_expiry = now + timedelta(hours=2)

        self.checker.broken = True
        mock_determine.return_value = ("", "")
        self.assertEqual(self.checker.ocsp_revoked(cert_obj), False)

        self.checker.broken = False
        mock_run.return_value = tuple(openssl_happy[1:])
        self.assertEqual(self.checker.ocsp_revoked(cert_obj), False)
        self.assertEqual(mock_run.call_count, 0)

        mock_determine.return_value = ("http://x.co", "x.co")
        self.assertEqual(self.checker.ocsp_revoked(cert_obj), False)
        mock_run.side_effect = errors.SubprocessError("Unable to load certificate launcher")
        self.assertEqual(self.checker.ocsp_revoked(cert_obj), False)
        self.assertEqual(mock_run.call_count, 2)

        # cert expired
        cert_obj.target_expiry = now
        mock_determine.return_value = ("", "")
        count_before = mock_determine.call_count
        self.assertEqual(self.checker.ocsp_revoked(cert_obj), False)
        self.assertEqual(mock_determine.call_count, count_before)

    def test_determine_ocsp_server(self):
        cert_path = test_util.vector_path('ocsp_certificate.pem')

        from certbot._internal import ocsp
        result = ocsp._determine_ocsp_server(cert_path)
        self.assertEqual(('http://ocsp.test4.buypass.com', 'ocsp.test4.buypass.com'), result)

    @mock.patch('certbot._internal.ocsp.logger')
    @mock.patch('certbot.util.run_script')
    def test_translate_ocsp(self, mock_run, mock_log):
        # pylint: disable=protected-access
        mock_run.return_value = openssl_confused
        from certbot._internal import ocsp
        self.assertEqual(ocsp._translate_ocsp_query(*openssl_happy), False)
        self.assertEqual(ocsp._translate_ocsp_query(*openssl_confused), False)
        self.assertEqual(mock_log.debug.call_count, 1)
        self.assertEqual(mock_log.warning.call_count, 0)
        mock_log.debug.call_count = 0
        self.assertEqual(ocsp._translate_ocsp_query(*openssl_unknown), False)
        self.assertEqual(mock_log.debug.call_count, 1)
        self.assertEqual(mock_log.warning.call_count, 0)
        self.assertEqual(ocsp._translate_ocsp_query(*openssl_expired_ocsp), False)
        self.assertEqual(mock_log.debug.call_count, 2)
        self.assertEqual(ocsp._translate_ocsp_query(*openssl_broken), False)
        self.assertEqual(mock_log.warning.call_count, 1)
        mock_log.info.call_count = 0
        self.assertEqual(ocsp._translate_ocsp_query(*openssl_revoked), True)
        self.assertEqual(mock_log.info.call_count, 0)
        self.assertEqual(ocsp._translate_ocsp_query(*openssl_expired_ocsp_revoked), True)
        self.assertEqual(mock_log.info.call_count, 1)


@unittest.skipIf(not ocsp_lib,
                 reason='This class tests functionalities available only on cryptography>=2.5.0')
class OSCPTestCryptography(unittest.TestCase):
    """
    OCSP revokation tests using Cryptography >= 2.4.0
    """

    def setUp(self):
        from certbot._internal import ocsp
        self.checker = ocsp.RevocationChecker()
        self.cert_path = test_util.vector_path('ocsp_certificate.pem')
        self.chain_path = test_util.vector_path('ocsp_issuer_certificate.pem')
        self.cert_obj = mock.MagicMock()
        self.cert_obj.cert = self.cert_path
        self.cert_obj.chain = self.chain_path
        now = pytz.UTC.fromutc(datetime.utcnow())
        self.cert_obj.target_expiry = now + timedelta(hours=2)

    @mock.patch('certbot._internal.ocsp._determine_ocsp_server')
    @mock.patch('certbot._internal.ocsp._check_ocsp_cryptography')
    def test_ensure_cryptography_toggled(self, mock_revoke, mock_determine):
        mock_determine.return_value = ('http://example.com', 'example.com')
        self.checker.ocsp_revoked(self.cert_obj)

        mock_revoke.assert_called_once_with(self.cert_path, self.chain_path,
                                            'http://example.com', None)

    def test_revoke(self):
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED, ocsp_lib.OCSPResponseStatus.SUCCESSFUL):
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertTrue(revoked)

    def test_responder_is_issuer(self):
        issuer = x509.load_pem_x509_certificate(
            test_util.load_vector('ocsp_issuer_certificate.pem'), default_backend())

        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED,
                        ocsp_lib.OCSPResponseStatus.SUCCESSFUL) as mocks:
            mocks['mock_response'].return_value.responder_name = issuer.subject
            self.checker.ocsp_revoked(self.cert_obj)
        # Here responder and issuer are the same. So only the signature of the OCSP
        # response is checked (using the issuer/responder public key).
        self.assertEqual(mocks['mock_check'].call_count, 1)
        self.assertEqual(mocks['mock_check'].call_args[0][0].public_numbers(),
                         issuer.public_key().public_numbers())

    def test_responder_is_authorized_delegate(self):
        issuer = x509.load_pem_x509_certificate(
            test_util.load_vector('ocsp_issuer_certificate.pem'), default_backend())
        responder = x509.load_pem_x509_certificate(
            test_util.load_vector('ocsp_responder_certificate.pem'), default_backend())

        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED,
                        ocsp_lib.OCSPResponseStatus.SUCCESSFUL) as mocks:
            self.checker.ocsp_revoked(self.cert_obj)
        # Here responder and issuer are not the same. Two signatures will be checked then,
        # first to verify the responder cert (using the issuer public key), second to
        # to verify the OCSP response itself (using the responder public key).
        self.assertEqual(mocks['mock_check'].call_count, 2)
        self.assertEqual(mocks['mock_check'].call_args_list[0][0][0].public_numbers(),
                         issuer.public_key().public_numbers())
        self.assertEqual(mocks['mock_check'].call_args_list[1][0][0].public_numbers(),
                         responder.public_key().public_numbers())

    def test_revoke_resiliency(self):
        # Server return an invalid HTTP response
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.UNKNOWN, ocsp_lib.OCSPResponseStatus.SUCCESSFUL,
                        http_status_code=400):
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # OCSP response in invalid
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.UNKNOWN, ocsp_lib.OCSPResponseStatus.UNAUTHORIZED):
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # OCSP response is valid, but certificate status is unknown
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.UNKNOWN, ocsp_lib.OCSPResponseStatus.SUCCESSFUL):
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # The OCSP response says that the certificate is revoked, but certificate
        # does not contain the OCSP extension.
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED, ocsp_lib.OCSPResponseStatus.SUCCESSFUL):
            with mock.patch('cryptography.x509.Extensions.get_extension_for_class',
                            side_effect=x509.ExtensionNotFound(
                                'Not found', x509.AuthorityInformationAccessOID.OCSP)):
                revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # OCSP response uses an unsupported signature.
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED, ocsp_lib.OCSPResponseStatus.SUCCESSFUL,
                        check_signature_side_effect=UnsupportedAlgorithm('foo')):
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # OSCP signature response is invalid.
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED, ocsp_lib.OCSPResponseStatus.SUCCESSFUL,
                        check_signature_side_effect=InvalidSignature('foo')):
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # Assertion error on OCSP response validity
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED, ocsp_lib.OCSPResponseStatus.SUCCESSFUL,
                        check_signature_side_effect=AssertionError('foo')):
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # No responder cert in OCSP response
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED,
                        ocsp_lib.OCSPResponseStatus.SUCCESSFUL) as mocks:
            mocks['mock_response'].return_value.certificates = []
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        # Responder cert is not signed by certificate issuer
        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED,
                        ocsp_lib.OCSPResponseStatus.SUCCESSFUL) as mocks:
            cert = mocks['mock_response'].return_value.certificates[0]
            mocks['mock_response'].return_value.certificates[0] = mock.Mock(
                issuer='fake', subject=cert.subject)
            revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)

        with _ocsp_mock(ocsp_lib.OCSPCertStatus.REVOKED, ocsp_lib.OCSPResponseStatus.SUCCESSFUL):
            # This mock is necessary to avoid the first call contained in _determine_ocsp_server
            # of the method cryptography.x509.Extensions.get_extension_for_class.
            with mock.patch('certbot._internal.ocsp._determine_ocsp_server') as mock_server:
                mock_server.return_value = ('https://example.com', 'example.com')
                with mock.patch('cryptography.x509.Extensions.get_extension_for_class',
                                side_effect=x509.ExtensionNotFound(
                                    'Not found', x509.AuthorityInformationAccessOID.OCSP)):
                    revoked = self.checker.ocsp_revoked(self.cert_obj)
        self.assertFalse(revoked)


@contextlib.contextmanager
def _ocsp_mock(certificate_status, response_status,
               http_status_code=200, check_signature_side_effect=None):
    with mock.patch('certbot._internal.ocsp.ocsp.load_der_ocsp_response') as mock_response:
        mock_response.return_value = _construct_mock_ocsp_response(
            certificate_status, response_status)
        with mock.patch('certbot._internal.ocsp.requests.post') as mock_post:
            mock_post.return_value = mock.Mock(status_code=http_status_code)
            with mock.patch('certbot._internal.ocsp.crypto_util.verify_signed_payload') \
                as mock_check:
                if check_signature_side_effect:
                    mock_check.side_effect = check_signature_side_effect
                yield {
                    'mock_response': mock_response,
                    'mock_post': mock_post,
                    'mock_check': mock_check,
                }


def _construct_mock_ocsp_response(certificate_status, response_status):
    cert = x509.load_pem_x509_certificate(
        test_util.load_vector('ocsp_certificate.pem'), default_backend())
    issuer = x509.load_pem_x509_certificate(
        test_util.load_vector('ocsp_issuer_certificate.pem'), default_backend())
    responder = x509.load_pem_x509_certificate(
        test_util.load_vector('ocsp_responder_certificate.pem'), default_backend())
    builder = ocsp_lib.OCSPRequestBuilder()
    builder = builder.add_certificate(cert, issuer, hashes.SHA1())
    request = builder.build()

    return mock.Mock(
        response_status=response_status,
        certificate_status=certificate_status,
        serial_number=request.serial_number,
        issuer_key_hash=request.issuer_key_hash,
        issuer_name_hash=request.issuer_name_hash,
        responder_name=responder.subject,
        certificates=[responder],
        hash_algorithm=hashes.SHA1(),
        next_update=datetime.now() + timedelta(days=1),
        this_update=datetime.now() - timedelta(days=1),
        signature_algorithm_oid=x509.oid.SignatureAlgorithmOID.RSA_WITH_SHA1,
    )


# pylint: disable=line-too-long
openssl_confused = ("", """
/etc/letsencrypt/live/example.org/cert.pem: good
	This Update: Dec 17 00:00:00 2016 GMT
	Next Update: Dec 24 00:00:00 2016 GMT
""",
"""
Response Verify Failure
139903674214048:error:27069065:OCSP routines:OCSP_basic_verify:certificate verify error:ocsp_vfy.c:138:Verify error:unable to get local issuer certificate
""")

openssl_happy = ("blah.pem", """
blah.pem: good
	This Update: Dec 20 18:00:00 2016 GMT
	Next Update: Dec 27 18:00:00 2016 GMT
""",
"Response verify OK")

openssl_revoked = ("blah.pem", """
blah.pem: revoked
	This Update: Dec 20 01:00:00 2016 GMT
	Next Update: Dec 27 01:00:00 2016 GMT
	Revocation Time: Dec 20 01:46:34 2016 GMT
""",
"""Response verify OK""")

openssl_unknown = ("blah.pem", """
blah.pem: unknown
	This Update: Dec 20 18:00:00 2016 GMT
	Next Update: Dec 27 18:00:00 2016 GMT
""",
"Response verify OK")

openssl_broken = ("", "tentacles", "Response verify OK")

openssl_expired_ocsp = ("blah.pem", """
blah.pem: WARNING: Status times invalid.
140659132298912:error:2707307D:OCSP routines:OCSP_check_validity:status expired:ocsp_cl.c:372:
good
	This Update: Apr  6 00:00:00 2016 GMT
	Next Update: Apr 13 00:00:00 2016 GMT
""",
"""Response verify OK""")

openssl_expired_ocsp_revoked = ("blah.pem", """
blah.pem: WARNING: Status times invalid.
140659132298912:error:2707307D:OCSP routines:OCSP_check_validity:status expired:ocsp_cl.c:372:
revoked
	This Update: Apr  6 00:00:00 2016 GMT
	Next Update: Apr 13 00:00:00 2016 GMT
""",
"""Response verify OK""")


if __name__ == '__main__':
    unittest.main()  # pragma: no cover
