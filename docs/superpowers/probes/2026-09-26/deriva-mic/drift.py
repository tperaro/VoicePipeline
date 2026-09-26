import sys, numpy as np
for f in sys.argv[1:]:
    tb=None; pts=[]; sz=[]
    for l in open(f):
        if l.startswith('#tb'): n,d=l.split(':')[1].strip().split('/'); tb=int(n)/int(d); continue
        if l.startswith('#') or not l.strip(): continue
        p=[x.strip() for x in l.split(',')]; pts.append(int(p[2])*tb); sz.append(int(p[4]))
    pts=np.array(pts); cum=np.concatenate([[0],np.cumsum(np.array(sz)//2)[:-1]])/48000.0
    t=pts-pts[0]; r=t-cum              # >0: timestamps run ahead of sample count
    m=cum>1.0                          # skip DLL settling
    b,a=np.polyfit(cum[m],r[m],1)      # r = a + b*cum
    res=r-(a+b*cum)
    print(f"{f.split('/')[-1]}: pkts={len(pts)} dur={cum[-1]+sz[-1]/2/48000:.1f}s spp={np.median(sz)//2:.0f}")
    print(f"  drift slope = {b*1e6:+.1f} ppm  -> {b*300*1000:+.1f} ms per 5 min")
    print(f"  first-pkt stamp vs steady-state line: {(r[0]-a)*1000:+.1f} ms ; resid |max| after 1s {np.abs(res[m]).max()*1000:.1f} ms, std {res[m].std()*1000:.2f} ms")
    for s in (0,0.5,1,2,5,10,30,60,120,180,230):
        i=np.searchsorted(cum,s)
        if i<len(r): print(f"   t={s:5.1f}s  r-a={ (r[i]-a)*1000:+7.1f} ms  (line-removed {res[i]*1000:+6.1f})")
    steps=np.diff(r)
    print(f"  max pkt-to-pkt jump in r: {np.abs(steps).max()*1000:.1f} ms")
