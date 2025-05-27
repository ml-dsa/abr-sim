#!/usr/bin/env python3

#   vcdparse.py
#   2024-11-21  Markku-Juhani O. Saarinen <mjos@iki.fi>

import sys

def joindot(li):
    if len(li) == 0:
        return ''
    if li[0] == 'TOP':
        s = ''
    else:
        s = li[0]
    for x in li[1:]:
        s += '.' + x
    return s

def bdist(a, b):
    d = 0
    for i in range(len(a)):
        if a[i] != b[i]:
            d += 1
    return d

dc = {}
st = {}
fn = sys.argv[1]
tm = -1
hdr = True
hd = 0
bl = 0
line = 0

step = 10
nstep = 0

with open(fn, 'r') as f:
    lev = 0
    stk = [None] * 99
    for buf in f:

        line += 1
        lv = buf.split();
        ll = len(lv)

        if ll < 1:
            continue

        if hdr:
            #   process header
            if lv[0] == '$scope' and ll >= 3:
                stk[lev] = lv[2]
                lev += 1
            elif lv[0] == '$upscope':
                lev -= 1
            elif lv[0] == '$var' and ll >= 5:
                stk[lev] = lv[4] + lv[5]
                s = joindot(stk[:lev+1])
                if lv[3] not in dc:
                    dc[lv[3]] = s
                elif len(s) < len(dc[lv[3]]):
                    dc[lv[3]] = s
                #   print(joindot(stk[:lev+1]), ':', lv[3])
            elif lv[0] == '$enddefinitions':
                hdr = False
                print(f'{fn} preamble: {line} lines, {len(dc)} ids')
        else:
            #   time steps
            if lv[0][0] == '#':
                #print(lv[0])
                tim = int(lv[0][1:])
                while nstep <= tim:
                    print(f'#{nstep}  {hd} {bl}')
                    hd = 0
                    bl = 0
                    nstep += step
            elif ll == 1:
                bit = lv[0][0]
                sig = lv[0][1:]
                if sig not in dc:
                    print(f'!{line}: {sig} not found: {buf}')
                if sig in st:
                    if bit != st[sig]:
                        hd += 1
                st[sig] = bit
                bl += 1
            elif ll >= 2:
                if lv[0][0] != 'b' and lv[0][0] != 'B':
                    print(f'{fn}:{line}: ERROR {buf}')
                bit = lv[0][1:]
                dl  = len(bit)
                bl  += dl
                sig = lv[1]
                if sig in st:
                    df = bdist(bit, st[sig])
                    hd += df

                    #print(f'[{nstep//10:5}] d:{df:6} | {dc[sig]}')
                st[sig] = bit

                """
                tt = dc[sig]
                if tt.find('dec_sec.cyc') >= 0:
                    print('xxx', int(bit, 2))
                """

                """
                if lv[1] in dc:
                    print(f'#{tim}', dc[lv[1]], '=', d)
                else:
                    print(f'#{tim}', '<undef>', lv[1], '=', d)
                """
