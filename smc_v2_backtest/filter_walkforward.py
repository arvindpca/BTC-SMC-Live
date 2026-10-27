import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd, paxg_intraday_v3 as m
def build(path):
    d=m.load(path); P=m.prep(d)
    o,h,l,c,mm,dates=P["o"],P["h"],P["l"],P["c"],P["m"],P["dates"]; vol=d.volume.values
    pc=np.r_[c[0],c[:-1]]; tr=np.maximum(h-l,np.maximum(abs(h-pc),abs(l-pc))); a=lambda x: pd.Series(x).ewm(alpha=1/14,adjust=False).mean().values
    atr=a(tr); up_=h-np.r_[h[0],h[:-1]]; dn_=np.r_[l[0],l[:-1]]-l
    pdm=np.where((up_>dn_)&(up_>0),up_,0.0); ndm=np.where((dn_>up_)&(dn_>0),dn_,0.0)
    pdi=100*a(pdm)/atr; ndi=100*a(ndm)/atr; adx=a(100*abs(pdi-ndi)/(pdi+ndi+1e-9)); vma=pd.Series(vol).rolling(20).mean().values
    rows=[]
    for s,e in zip(P["starts"],P["ends"]):
        if P["dow"][s]>=5: continue
        msk=mm[s:e]; rm=(msk>=0)&(msk<420)
        if rm.sum()<int(0.8*420/15): continue
        rh,rl=h[s:e][rm].max(),l[s:e][rm].min()
        wm=np.flatnonzero((msk>=420)&(msk<720))
        if len(wm)==0: continue
        idxs=s+wm; U,D=c[idxs]>rh,c[idxs]<rl
        tv=P["trend"].get(int(dates[s]),0); U,D=U&(tv>0),D&(tv<0)
        hit=np.flatnonzero(U|D)
        if len(hit)==0: continue
        k=hit[0]; side=1 if U[k] else -1; j=idxs[k]; i0=j+1
        if i0>=e or mm[i0]>=960: continue
        entry=o[i0]; stop=rl if side==1 else rh; dist=side*(entry-stop)
        if not dist>0 or not (0.003<=dist/entry<=0.03): continue
        tp=entry+side*1.5*dist; jend=i0+np.flatnonzero(mm[i0:e]<960)[-1]
        sl_,sh_=l[i0:jend+1],h[i0:jend+1]
        hs,ht=(sl_<=stop,sh_>=tp) if side==1 else (sh_>=stop,sl_<=tp)
        fs=np.argmax(hs) if hs.any() else 10**9; ft=np.argmax(ht) if ht.any() else 10**9
        if fs<=ft and fs<10**9: px=stop
        elif ft<10**9: px=tp
        else: px=c[jend]
        rows.append(dict(date=pd.to_datetime(int(dates[s]),unit="D"),ret=side*(px-entry)/entry*100,
                         vol_ratio=vol[j]/vma[j] if vma[j]>0 else np.nan, adx=adx[j]))
    return pd.DataFrame(rows)
COST=0.11   # % round trip: maker 0.05 + slippage 0.06
def stat(x):
    if len(x)==0: return "n=0"
    g=x[x>0].sum(); ls=-x[x<=0].sum(); pf=g/ls if ls>0 else np.inf
    return f"n={len(x):3d} win={np.mean(x>0)*100:3.0f}% net/trade={x.mean():+.3f}% PF={pf:.2f}"
CAND={"base (no filter)":lambda f:np.ones(len(f),bool),
 "vol<=1.5":lambda f:f.vol_ratio<=1.5,"vol<=2.0":lambda f:f.vol_ratio<=2.0,
 "adx>=20":lambda f:f.adx>=20,"vol<=2.0 & adx>=20":lambda f:(f.vol_ratio<=2.0)&(f.adx>=20)}
for name,path in [("PAXG","results/data/PAXGUSDT_15m_cache.csv"),("BTC","results/data/BTCUSDT_15m_cache.csv")]:
    f=build(path); f["net"]=f.ret-COST; n=len(f); k=int(n*0.6)
    print(f"\n=== {name}: {n} trades {f.date.min().date()} -> {f.date.max().date()} (maker+slip {COST}% round trip) ===")
    for cn,fn in CAND.items():
        mk=np.asarray(fn(f))
        print(f"{cn:22s} TRAIN {stat(f.net[:k][mk[:k]].values)} | TEST {stat(f.net[k:][mk[k:]].values)}")
