
The key distinction is that ML-KEM decapsulation gives the adversary chosen input into the secret multiplication, while ML-DSA signing’s strongest issue is repeated unmasked processing of fixed secret-key material. Both are serious SCA targets, but ML-KEM MLKEM_DECAPS_S +11..+14 is the cleaner attack surface.

 I’d rank them:

 1. ML-KEM MLKEM_DECAPS_S +11..+14
 MLKEM_MASKED_PWM/A(s ∘ NTT(u′))

 Highest priority. Chosen ciphertext controls u′; s is long-term secret; share split happens after unmasked SRAM/bus
 movement. Constant-time implicit rejection does not help here.

 2. ML-DSA MLDSA_SIGN_INIT_S +9..+15
 unmasked NTT(s1)
 Strongest ML-DSA target. No masking at all, repeated per signature, long-term secret.

 3. ML-DSA MLDSA_SIGN_INIT_S +16..+23 and +1..+8
 unmasked NTT(s2) and NTT(t0) 
 Same class as s1; s1 is just the cleanest first target because it enters the response equation directly.

 4. ML-KEM MLKEM_DECAPS_S +4 and +15: secret decode/load of s, then masked INTT of the secret product. Useful for localization and template-style checks.

 5. ML-DSA MLDSA_SIGN_VALID_S masked c*s1, c*s2, c*t0 chains: Still worth checking for the load gadget, but lower priority than the unmasked NTTs.

 For first TVLA sweeps, I’d do exactly what you suggest:

 ML-KEM decaps: MLKEM_DECAPS_S +11..+15, with readvcd-ntt.prm
 ML-DSA sign: MLDSA_SIGN_INIT_S +9..+15, with readvcd-ntt.prm

 Then cross-check with codec/control/memory hierarchy filters to separate arithmetic leakage from decode/load effects.
 

Two clearly-leakiest steps stand out, and they're not equivalent — ML-KEM is the much sharper target:
 
 ML-KEM: MLKEM_DECAPS_S +11..+14 — masked s ∘ NTT(u) 
 
 This is the textbook DPA/SCA target for any Kyber/ML-KEM accelerator and the Adams Bridge implementation does nothing 
 to defeat it: 
 
 - One operand is s[i], read unmasked from SRAM (DECAPS_S +4 decompresses dk into MLKEM_S0..S3_BASE; the masked PWM then loads each s[i] directly).
 - The other operand is NTT(u′)[i] where u′ is the attacker's ciphertext c_1. The adversary picks the ciphertext. 
 - Masking starts inside the PWM unit (share split via ntt_rand_bits); the SRAM read + bus transition runs before the share split — the classic load-gadget window. 
 - The implicit-reject path (decaps_valid mechanism we documented) does not short-circuit this step. Whether or not c = c′ after re-encrypt, the chosen-ciphertext PWM has already been computed in full. Constant-time wrt observable 
 timing, but the SCA leakage is unconditional.
 - Per decap: 4 polys × 256 coefficients of the secret each multiplied against attacker-chosen values. With chosen-ciphertext oracles (e.g. crafting u′ to expose coefficients one at a time, à la Pessl-Primas 2017, Ravi et al.), this is the canonical Kyber DPA gadget.
 
 Secondary on the ML-KEM side: MLKEM_DECAPS_S +4 (the bare SRAM read of s — single trace, template-attack territory) and MLKEM_DECAPS_S +15 (MLKEM_MASKED_INTT of s ∘ NTT(u) — already shared, but coefficient-level leakage from masked Kyber INTTs is documented). 
 
 ML-DSA: MLDSA_SIGN_INIT_S +9..+15 — unmasked NTT(s1) 
 
 This is the strongest ML-DSA candidate, and it's worse than the masked PWMs because there's no masking at all: 
 
 - The signing flow loads s1[0..6] from MLDSA_S1_*_BASE (set by SKDECODE at INIT_S +0) and runs each through the unmasked ABR_UOP_NTT.
 - The whole 256-coefficient butterfly network sees s1 in the clear — every register stage of the NTT toggles with secret-correlated values. 
 - Runs once per signature, on the long-term secret — fixed-key DPA collects as many traces as you can sign messages.
 - NTT-domain template attacks on lattice signatures (Primas-Pessl-Mangard 2017; Karabulut et al.) target exactly this construct. 
 
 The same logic applies to INIT_S +1..+8 (NTT(t0)) and INIT_S +16..+23 (NTT(s2)). All three are unmasked NTTs of secret-key components. t0 is also private (FIPS 204 Alg 6 lines 6, 10), despite being the "low bits" — it's part of sk and protects against forgeries. 
 
 The masked-PWM steps in MLDSA_SIGN_VALID_S (the c·s1, c·s2, c·t0 chains) have the load-gadget leak too, but the share-domain compute provides some protection. The unmasked NTTs in INIT_S have none.
 
 Why these and not other looks-suspicious places

 - MLDSA_SIGN_MAKE_W_S +0..+63 (the REJS_MASKED_PWM chain Â ∘ ŷ) is masked AND the secret-bearing operand is y (per-signature ephemeral, not long-term). Recovering y from one trace forges that one signature; recovering s1 forges all of them. Lower priority despite being the longest-running cryptographic block. 
 - MLKEM_KG_S +12..+19 (unmasked NTT(s) and NTT(e) in keygen) is a one-shot — keygen happens once per device, so DPA aggregation is impossible. Per the design's threat model, keygen is assumed trusted. 
 - MLDSA_VERIFY_* and MLKEM_ENCAPS_S use no secrets — verify/encaps are public computations.
 - MLDSA_SIGN_VALID_S +1 (MASKED_PWM(ĉ·s1[0])) is masked. Not the best target unless you've already characterized the load gadget. 
 
 Practical takeaway for your TVLA pipeline 
 
 If you're picking one sweep to run first, run a fixed-vs-random ML-KEM mlkem-decaps test windowed on the MLKEM_DECAPS_S +11..+15 cycle range. With the new flow/readvcd-ntt.prm preset you can immediately scope the toggle count to the masked NTT/PWM units and get a high-SNR signal. A spike there will confirm the load-gadget leak; the subsequent comparison against flow/readvcd-mlkem-codec.prm (compress/decompress only) tells you whether the leak is in the multiply or in the codec wrappers.

 For ML-DSA, fixed-vs-random mldsa-sign windowed on MLDSA_SIGN_INIT_S +9..+15 (the unmasked NTT(s1) block) is the equivalent first cut. Combine with flow/readvcd-ntt.prm to confirm the leak is in the NTT pipeline rather than in the SKDECODE preceding it.
 
