import numpy as np, soundfile as sf, os, json
R="/home/peras/orochi-ia-homenagem/recordings"
P=os.path.dirname(os.path.abspath(__file__))
sr=48000
x,s=sf.read(f"{R}/take_20260923_150423_boosted.wav"); assert s==sr
a=np.concatenate([np.zeros(sr), x, np.zeros(sr//2)])
sf.write(f"{P}/in/a_pad.wav", a, sr, subtype="PCM_16")
files=["take_20260923_150423_boosted.wav","take2_boosted.wav","take_20260914_220426_boosted.wav","take_novo_boosted.wav"]
parts=[]; seg=[]; t=0
for i,f in enumerate(files):
    y,s=sf.read(f"{R}/{f}"); assert s==sr and y.ndim==1
    if i: parts.append(np.zeros(int(0.3*sr))); t+=int(0.3*sr)
    seg.append((f,t/sr,(t+len(y))/sr)); parts.append(y); t+=len(y)
b=np.concatenate(parts)
sf.write(f"{P}/in/b_90s.wav", b, sr, subtype="PCM_16")
# ~300 s long test: all boosted files concatenated w/ 0.3 s gaps, looped to >=300 s
allf=sorted(f for f in os.listdir(R) if f.endswith("_boosted.wav"))
parts=[]; tot=0
while tot<300*sr:
    for f in allf:
        y,_=sf.read(f"{R}/{f}"); parts+= [y, np.zeros(int(0.3*sr))]; tot+=len(y)+int(0.3*sr)
        if tot>=300*sr: break
c=np.concatenate(parts)[:300*sr]
sf.write(f"{P}/in/c_300s.wav", c, sr, subtype="PCM_16")
print(json.dumps({"a_len_s":len(a)/sr,"b_len_s":len(b)/sr,"b_segments":seg,"c_len_s":len(c)/sr}))
