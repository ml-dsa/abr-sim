#   tvla.py
#   2024-11-28  Markku-Juhani O. Saarinen <mjos@iki.fi>

import sys, math
#import numpy as np

# ---------------------------------------------------------------------------
# fdist: incremental averages, variances, max/min (with negligible storage)

class fdist:

    def __init__(self, n=0, s=0, r=0, mn=float('inf'), mx=float('-inf')):
        self.n  = n                         #   number of entries
        self.s  = s                         #   sum of values
        self.r  = r                         #   sum of squares
        self.mn = mn                        #   min
        self.mx = mx                        #   max

    def addx(self, x):                      #   add a single scalar
        self.n += 1
        self.s += x
        self.r += x*x
        if x < self.mn:
            self.mn = x
        if x > self.mx:
            self.mx = x

    def __add__(self, x):                   #   add distributions
        y = fdist()
        y.n = self.n + x.n
        y.s = self.s + x.s
        y.r = self.r + x.r
        y.mn = np.minimum(self.mn, x.mn)
        y.mx = np.maximum(self.mx, x.mx)
        return y

    #   --- statistical quantities

    def avg(self):                          #   average
        return self.s / self.n

    def var(self):                          #   variance
        t = self.avg()
        return self.r / self.n - t * t

    def std(self):                          #   standard deviation
        return self.var() ** 0.5

    #   --- welch t-test

    def __sub__(self, x):                   #   sub: t-test distance
        return welch_t(self, x)

    def welch_t(a, b):                      #   Welch t-test
        return ( ( a.avg() - b.avg() ) /
                math.sqrt( a.var()/a.n + b.var()/b.n ) )

    #   --- callable interfaces to internal variables

    def sum(self):                          #   sum
        return self.s

    def min(self):                          #   minimum element
        return self.mn

    def max(self):                          #   maximum element
        return self.mx

    #   --- conversions

    def from_scalar(x):                     #   create from scalar
        return fdist(1, x, x*x, x, x)

    def from_list(v):                       #   create from list
        y = fdist();
        for x in v:
            y.addx(x)
        return y

    def addv(self, v):                      #   iterable "vector"
        for x in v:
            self.addx(x)

    def __repr__(self):                     #   printable string
        if self.n == 0:
            return('< n= 0 >')
        else:
            return (f'< n= {self.n:.0f} | ' +
                    f'avg= {self.avg()} | std= {self.std()} | ' +
                    f'min= {self.min()} | max= {self.max()} >')

#   Welch t-test statistic

def welch_t(a, b):
    if a.n == 0 or b.n == 0:
        return 0
    c = a.var()/a.n + b.var()/b.n
    if c == 0:
        return 0
    return ( a.avg() - b.avg() ) / math.sqrt( c )


#   this is the two-tail p-value (probability of false positve) for normal
#   distribution and also for the t-test with infinite degrees of freedom.

def pvalue_t(t):
    return math.erfc(abs(t)*2**-0.5)


#   Approximate P-value for given Chi^2 sum and degrees of freedom.
#   (Right-tailed probability of the Chi-squared distribution.)

def chi2rt(x, df=1):

    #  Approxmate Q(a,x), the normalised upper incomplete gamma function
    #  CDF for Chi^2 with "df" degrees of freedom is is Q(df/2,chi2/2)

    x *= 0.5                                #   Chi^2/2

    if df % 2 == 1:                         #   odd df: 1, 3, 5, ...

        s = math.erfc(math.sqrt(x))
        if  df >= 3:
            z = math.exp(-x) / math.sqrt(x * math.pi)
            f = 1.0
            for i in range((df - 1) // 2):
                z *= x
                f *= i + 0.5
                s += z / f

    else:                                   #   even df: 2, 4, 6, ...

        s = math.exp(-x)
        if df >= 4:
            z = s
            f = 1.0
            for i in range(1, df // 2):
                z *= x
                f *= i
                s += z / f

    return s

#   read input files and create

def add_beg(beg, tag, t):
    if tag not in beg:
        beg[tag] = fdist()
    beg[tag].addx(t)

def read_trace(fn, d, beg):
    with open(fn, 'r') as f:
        rep = set()                 # the sequencer may revisit some points
        for line in f:
            if line[0] != '#':
                continue
            i = line.find('[')
            if i < 0:
                continue
            t = int(line[1:i])
            if line[i:i+6] == '[togd]':
                y   = int(line[i+6:])
                if t not in d:
                    d[t] = fdist()
                d[t].addx(y)
            else:
                vl  = line[i:].replace('[','').replace(']','').split()
                if vl[0] == 'prim':
                    tag = 'p' + vl[1].replace(':','')
                elif vl[0] == 'sec':
                    tag = 's' + vl[1].replace(':','')
                else:
                    tag = line[i:]
                while tag in rep:
                    tag += '_'
                rep.add(tag)
                add_beg(beg, tag, t)

def dist_str(d):
    return f'({d.n:5.0f}, {d.avg():8.1f}, {d.std():8.2f})'

if __name__ == '__main__':
    fix =   {}
    rnd =   {}
    beg =   {}
    n = 0
    for fn in sys.argv[1:]:
        n += 1
        print(f'[read] #{n} {fn}')
        sys.stdout.flush()
        if 'fix' in fn:
            read_trace(fn, fix, beg)
        elif 'rnd' in fn:
            read_trace(fn, rnd, beg)

    for tag in beg:
        print(f'[tag] {tag:10} {dist_str(beg[tag])}')

    for x in rnd:
        if x not in fix:
            continue
        r = rnd[x]
        f = fix[x]
        print(f'{x:5} {welch_t(f, r):9.4f} # f:{dist_str(f)} r:{dist_str(r)} [t]')

