import json, os, sys
sys.path.insert(0,'.')
with open(os.path.join('bench','data','wr_cases.json'),encoding='utf-8') as f:
    cases=json.load(f)['cases']
with open(os.path.join('bench','data','wr_before.json'),encoding='utf-8') as f:
    gold=json.load(f)['results']

# safety 用 clicks2p（存盘的是排序前插入序 → 按坐标查）
print("="*90)
print("判定：safety 最高格 vs win_rate 最高格（全部用户用例）")
print("="*90)
print(f"{'idx':>3} {'F':>3} {'U':>3} {'world':>6} | {'safety最高格':>14} {'safety':>8} | {'win_rate最高格':>15} {'wr':>8} | 一致")
n_diff=0; n_tot=0
for i,(c,g) in enumerate(zip(cases,gold)):
    c2p={(int(k.split(',')[0]),int(k.split(',')[1])):float(v) for k,v in g['clicks2p'].items()}
    if not c2p: continue
    best_s=max(c2p.items(), key=lambda kv: kv[1])
    res=[float(x) for x in g['res']]; clk=[tuple(p) for p in g['clicks']]
    mp={xy:res[k] for k,xy in enumerate(clk) if k<len(res)}
    if not mp: continue
    best_w=max(mp.items(), key=lambda kv: kv[1])
    same = best_s[0]==best_w[0]
    n_tot+=1
    if not same: n_diff+=1
    print(f"{i:>3} {c['F']:>3} {c['U']:>3} {g['total']:>6} | {str(best_s[0]):>14} {best_s[1]:>8.4f} | "
          f"{str(best_w[0]):>15} {best_w[1]:>8.4f} | {'✓' if same else '✗ 不同'}")
print("-"*90)
print(f"不一致 {n_diff}/{n_tot} = {n_diff/n_tot*100:.1f}%  ← win_rate 选了与最大安全率不同的格")
print()
print("读法：不一致即证明 'safety=1.0 不等于好点击'；")
print("      win_rate 用 P(继续获胜) 捕捉到 safety 看不到的信息。")
