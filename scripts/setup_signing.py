#!/usr/bin/env python3
"""Create one persistent local code-signing identity. Never touches the API credential.

Only /usr/bin/codesign is pre-authorized for the non-exportable private key.
No system trust settings, root trust, or security protections are changed.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

NAME='DJ Jev Local Signing'


def identities():
    result=subprocess.run(['/usr/bin/security','find-identity','-p','codesigning'],capture_output=True,text=True,check=True)
    return set(re.findall(r'([0-9A-F]{40})\s+"'+re.escape(NAME)+r'"',result.stdout))


def main():
    existing=identities()
    if existing:
        print('Vaste lokale ondertekening bestaat al; niets opnieuw aangemaakt.')
        return
    # Private intermediate files are inaccessible to other users and always cleaned up.
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='dj-jev-signing-') as temporary:
        folder=Path(temporary)
        key,cert,p12=(folder/name for name in ('private.pem','certificate.pem','identity.p12'))
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','3650',
            '-subj','/CN='+NAME,'-keyout',str(key),'-out',str(cert),
            '-addext','basicConstraints=critical,CA:FALSE',
            '-addext','keyUsage=critical,digitalSignature',
            '-addext','extendedKeyUsage=critical,codeSigning'],check=True,capture_output=True)
        subprocess.run(['openssl','pkcs12','-export','-inkey',str(key),'-in',str(cert),'-out',str(p12),
            '-name',NAME,'-passout','pass:','-keypbe','PBE-SHA1-3DES','-certpbe','PBE-SHA1-3DES','-macalg','sha1'],
            check=True,capture_output=True)
        # Empty wrapping password is only for this temporary mode-0600 file, not the login keychain.
        result=subprocess.run(['/usr/bin/security','import',str(p12),'-f','pkcs12','-P','',
                               '-k',str(Path.home()/'Library/Keychains/login.keychain-db'),
                               '-T','/usr/bin/codesign','-x'],capture_output=True,text=True)
        if result.returncode:
            raise RuntimeError('Lokale ondertekening niet geïmporteerd: '+result.stderr.strip())
    if len(identities())!=1:
        raise RuntimeError('Geen unieke codeondertekening bevestigd.')
    print('Vaste lokale ondertekening aangemaakt in de login-Sleutelhanger; tijdelijke privésleutelbestanden verwijderd.')


if __name__=='__main__':main()
