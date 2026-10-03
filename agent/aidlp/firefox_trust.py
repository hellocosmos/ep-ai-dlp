"""Import one session CA into an application-owned Firefox profile, never Windows.

Executed in a short-lived supervised process because NSS owns global native state.
API declarations follow Mozilla NSS cert.h, certdb.h, certt.h and nss.h.
"""
from __future__ import annotations

import argparse
import ctypes as C
import os
from pathlib import Path

MARKER = 'Fastpace AI DLP dedicated Firefox profile v1'
NICKNAME = b'Fastpace AI DLP current session'


class Trust(C.Structure):
    _fields_ = [('sslFlags', C.c_uint), ('emailFlags', C.c_uint), ('objectSigningFlags', C.c_uint)]


def import_ca(browser: Path, profile: Path, ca: Path):
    profile = profile.resolve()
    if (os.name != 'nt' or profile.name != 'firefox'
            or profile.parent.name != 'browser-profiles'
            or (profile / '.aidlp-owned-profile').read_text(encoding='utf-8') != MARKER):
        raise ValueError('profile_not_owned')
    pem = ca.read_bytes()
    if len(pem) > 16384 or not pem.startswith(b'-----BEGIN CERTIFICATE-----'):
        raise ValueError('invalid_ca')
    browser = browser.resolve(strict=True)
    with os.add_dll_directory(str(browser.parent)):
        nss = C.CDLL(str(browser.parent / 'nss3.dll'))
        signatures = {
            'NSS_InitReadWrite': ([C.c_char_p], C.c_int),
            'NSS_Shutdown': ([], C.c_int),
            'CERT_GetDefaultCertDB': ([], C.c_void_p),
            'CERT_FindCertByNickname': ([C.c_void_p, C.c_char_p], C.c_void_p),
            'CERT_DecodeCertFromPackage': ([C.c_char_p, C.c_int], C.c_void_p),
            'PK11_GetInternalKeySlot': ([], C.c_void_p),
            'PK11_FreeSlot': ([C.c_void_p], None),
            'PK11_NeedUserInit': ([C.c_void_p], C.c_int),
            'PK11_InitPin': ([C.c_void_p, C.c_char_p, C.c_char_p], C.c_int),
            'PK11_Authenticate': ([C.c_void_p, C.c_int, C.c_void_p], C.c_int),
            'PK11_ImportCert': ([C.c_void_p, C.c_void_p, C.c_ulong, C.c_char_p, C.c_int], C.c_int),
            'CERT_ChangeCertTrust': ([C.c_void_p, C.c_void_p, C.POINTER(Trust)], C.c_int),
            'CERT_GetCertTrust': ([C.c_void_p, C.POINTER(Trust)], C.c_int),
            'SEC_DeletePermCertificate': ([C.c_void_p], C.c_int),
            'CERT_DestroyCertificate': ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(nss, name)
            function.argtypes, function.restype = args, result
        if nss.NSS_InitReadWrite(('sql:' + str(profile)).encode('utf-8')) != 0:
            raise ValueError('nss_init_failed')
        try:
            db = nss.CERT_GetDefaultCertDB()
            old = nss.CERT_FindCertByNickname(db, NICKNAME)
            if old:
                try:
                    if nss.SEC_DeletePermCertificate(old) != 0:
                        raise ValueError('old_session_removal_failed')
                finally:
                    nss.CERT_DestroyCertificate(old)
            cert = nss.CERT_DecodeCertFromPackage(pem, len(pem))
            if not cert:
                raise ValueError('ca_decode_failed')
            try:
                trust = Trust(24, 0, 0)  # VALID_CA | TRUSTED_CA: SSL server only.
                slot = nss.PK11_GetInternalKeySlot()
                if not slot:
                    raise ValueError('nss_slot_missing')
                try:
                    if nss.PK11_NeedUserInit(slot) and nss.PK11_InitPin(slot, None, b'') != 0:
                        raise ValueError('nss_token_init_failed')
                    if nss.PK11_Authenticate(slot, 1, None) != 0:
                        raise ValueError('nss_token_auth_failed')
                    if nss.PK11_ImportCert(slot, cert, 0, NICKNAME, 0) != 0:
                        raise ValueError('ca_import_failed')
                finally:
                    nss.PK11_FreeSlot(slot)
                if nss.CERT_ChangeCertTrust(db, cert, C.byref(trust)) != 0:
                    raise ValueError('ca_trust_failed')
            finally:
                nss.CERT_DestroyCertificate(cert)
            saved = nss.CERT_FindCertByNickname(db, NICKNAME)
            if not saved:
                raise ValueError('ca_missing_after_import')
            try:
                actual = Trust()
                if (nss.CERT_GetCertTrust(saved, C.byref(actual)) != 0
                        or actual.sslFlags & 24 != 24
                        or actual.emailFlags or actual.objectSigningFlags):
                    raise ValueError('ca_trust_verification_failed')
            finally:
                nss.CERT_DestroyCertificate(saved)
        finally:
            if nss.NSS_Shutdown() != 0:
                raise ValueError('nss_shutdown_failed')


def main():
    parser = argparse.ArgumentParser()
    for name in ('browser', 'profile', 'ca'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        import_ca(args.browser, args.profile, args.ca)
    except Exception:
        print('{"ready":false}')
        return 1
    print('{"ready":true}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
