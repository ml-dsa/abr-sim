#   mldsa-gen.py
#   2025-01-14  Markku-Juhani O. Saarinen <mjos@iki.fi> See LICENSE.

import sys
from fips204 import ML_DSA

from Crypto.Hash import SHAKE256, SHA512

def hextolen(s, l = 32):
    if len(s) < 2 * l:
        s = '0' * (2 * l - len(s)) + s
    b = bytes.fromhex(s[-2 * l:])
    return b

#   run the test on these function
if __name__ == '__main__':
    ml_dsa = ML_DSA()

    #   message
    if len(sys.argv) > 1:
        msg = bytes(sys.argv[1], 'utf-8')
    else:
        msg = b'abc'
    print('# msg:', msg.hex())

    #   key (number)
    if len(sys.argv) > 2:
        kg_seed =   hextolen(sys.argv[2], 32)
    else:
        kg_seed =   bytes(32)
    print('# kg_seed:', kg_seed.hex())

    #   secret key seed (rho')
    if len(sys.argv) > 3:
        sk_seed =   hextolen(sys.argv[3], 64)
        print('# sk_seed:', sk_seed.hex())
    else:
        sk_seed =   None

    #   ---

    pk, sk  = ml_dsa.keygen_internal(kg_seed, 'ML-DSA-87', sk_seed)
    mhash   = SHA512.new(msg).digest()
    rnd     = bytes( [0] * 32 )

    with open("seed_in.dat", "wb") as f:
        f.write(kg_seed)
    with open("pk_in.dat", "wb") as f:
        f.write(pk)
    with open("sk_in.dat", "wb") as f:
        f.write(sk)
    with open("rnd_in.dat", "wb") as f:
        f.write(rnd)
    with open("hash_in.dat", "wb") as f:
        f.write(mhash)

    #mhash  = bytes(32)

    #   "Pure" ML-DSA.Sign()
    mp  = bytes([ 0, 0 ]) + mhash
    sig = ml_dsa.sign_internal(sk, mp, rnd)

    with open("sig_in.dat", "wb") as f:
        f.write(sig)

    #   check verification

    print('# verify', ml_dsa.verify_internal(pk, mp, sig))
