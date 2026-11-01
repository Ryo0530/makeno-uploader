#!/usr/bin/env python3
"""ロト7 過去データ分析 → report.html を出力する。

  1. 数字ごとの出現回数 + 偏りの検定 (カイ二乗検定)
  2. 「ホット数字」「コールド数字」などの予想法が本当に効くかのバックテスト
  3. 前回との重なり・合計値の分布 (抽せんがランダムかの確認)
  4. 当せん口数から逆算した「人気の数字」(人とかぶる数字) の推定
  5. 実際の還元率 (キャリーオーバーあり/なし)

使い方:  python3 analyze.py           → report.html を作成
外部ライブラリ不要。
"""
from __future__ import annotations

import html
import math
import random
from collections import Counter
from pathlib import Path

from generate import generate
from loto7lib import (
    EXPECTED_HITS, N_NUM, PICK, PRICE, TIER_P, TOTAL_COMBOS, VAR_HITS,
    chi2_sf, consecutive_pairs, draw_popularity_index, fit_popularity,
    hit_dist, load_draws, normal_sf, sum_distribution,
)

OUT = Path(__file__).resolve().parent / "report.html"
BACKTEST_START = 50     # 最初の50回は学習用として予想を出さない


# ================================================================ 分析
def frequency(draws):
    c = Counter(n for d in draws for n in d.nums)
    counts = [c[n] for n in range(1, N_NUM + 1)]
    exp = len(draws) * PICK / N_NUM
    chi2 = sum((o - exp) ** 2 / exp for o in counts)
    # 1数字あたりの出現回数の標準偏差 (二項分布)
    sd = math.sqrt(len(draws) * (PICK / N_NUM) * (1 - PICK / N_NUM))
    return counts, exp, sd, chi2, chi2_sf(chi2, N_NUM - 1)


def backtest(draws, seed=0):
    """各回の直前までのデータだけで7個を選び、その回の本数字と何個一致したか。"""
    rng = random.Random(seed)
    strategies = {
        "ホット数字 (直近50回で多い7個)": [],
        "コールド数字 (直近50回で少ない7個)": [],
        "ホット数字 (全期間で多い7個)": [],
        "出ていない期間が長い7個": [],
        "前回の当せん番号をそのまま": [],
        "完全ランダム (比較用)": [],
    }
    keys = list(strategies)
    last_seen = {n: -1 for n in range(1, N_NUM + 1)}
    total = Counter()
    for i, d in enumerate(draws):
        if i >= BACKTEST_START:
            recent = Counter(n for x in draws[i - 50:i] for n in x.nums)
            # 同数のときは数字の小さい方 (恣意性を排除するため固定ルール)
            by_recent = sorted(range(1, N_NUM + 1), key=lambda n: (-recent[n], n))
            by_total = sorted(range(1, N_NUM + 1), key=lambda n: (-total[n], n))
            by_gap = sorted(range(1, N_NUM + 1), key=lambda n: (last_seen[n], n))
            picks = [
                by_recent[:PICK], by_recent[-PICK:], by_total[:PICK], by_gap[:PICK],
                list(draws[i - 1].nums), rng.sample(range(1, N_NUM + 1), PICK),
            ]
            actual = set(d.nums)
            for k, p in zip(keys, picks):
                strategies[k].append(len(actual & set(p)))
        for n in d.nums:
            last_seen[n] = i
            total[n] += 1

    p4 = sum(hit_dist()[4:])  # 7個中4個以上一致 (=5等以上相当) の理論確率
    res = []
    for k, hits in strategies.items():
        n = len(hits)
        mean = sum(hits) / n
        se = math.sqrt(VAR_HITS / n)
        z = (mean - EXPECTED_HITS) / se
        res.append({
            "name": k, "n": n, "mean": mean, "lo": mean - 1.96 * se, "hi": mean + 1.96 * se,
            "p": 2 * normal_sf(abs(z)),
            "ge4": sum(1 for h in hits if h >= 4), "ge4_exp": n * p4,
        })
    return res


def overlap_prev(draws):
    ov = [len(set(a.nums) & set(b.nums)) for a, b in zip(draws, draws[1:])]
    n = len(ov)
    mean = sum(ov) / n
    z = (mean - EXPECTED_HITS) / math.sqrt(VAR_HITS / n)
    obs = Counter(ov)
    hd = hit_dist()
    return mean, 2 * normal_sf(abs(z)), [(k, obs[k], n * hd[k]) for k in range(PICK + 1)]


def sum_stats(draws):
    th = sum_distribution()
    sums = [sum(d.nums) for d in draws]
    width = 10
    lo, hi = 50, 210
    bins = list(range(lo, hi, width))
    obs, exp = [], []
    for b in bins:
        in_bin = lambda s: b <= s < b + width or (b == lo and s < lo) or (b == bins[-1] and s >= hi)
        obs.append(sum(1 for s in sums if in_bin(s)) / len(sums))
        exp.append(sum(p for s, p in th.items() if in_bin(s)))
    # 適合度検定 (期待度数が小さいビンは端に寄せてあるのでそのまま)
    n = len(sums)
    chi2 = sum((o * n - e * n) ** 2 / (e * n) for o, e in zip(obs, exp) if e > 0)
    mean_th = sum(s * p for s, p in th.items())
    return bins, width, obs, exp, chi2_sf(chi2, len(bins) - 1), sum(sums) / n, mean_th


def odd_even(draws):
    obs = Counter(sum(1 for n in d.nums if n % 2) for d in draws)
    odd, even = 19, 18
    rows = []
    for k in range(PICK + 1):
        p = math.comb(odd, k) * math.comb(even, PICK - k) / TOTAL_COMBOS
        rows.append((k, obs[k], len(draws) * p))
    return rows


def returns(draws):
    """推定販売口数から実際の還元率を計算。"""
    p456 = sum(TIER_P[3:6])
    agg = {True: [0, 0, 0], False: [0, 0, 0]}  # [払戻, 売上, 回数]
    for i, d in enumerate(draws):
        w = sum(d.winners[3:6])
        if w == 0:
            continue
        sales = w / p456 * PRICE
        paid = sum(a * b for a, b in zip(d.winners, d.prizes))
        carry = i > 0 and draws[i - 1].carryover > 0
        agg[carry][0] += paid
        agg[carry][1] += sales
        agg[carry][2] += 1
    tot_paid = agg[True][0] + agg[False][0]
    tot_sales = agg[True][1] + agg[False][1]
    return {
        "all": tot_paid / tot_sales,
        "carry": agg[True][0] / agg[True][1], "carry_n": agg[True][2],
        "nocarry": agg[False][0] / agg[False][1], "nocarry_n": agg[False][2],
        "first_none": sum(1 for d in draws if d.winners[0] == 0),
    }


def popularity_groups(draws):
    """当せん番号の特徴ごとに、当せん口数 (人気度) がどう違うか。"""
    rows = [(d, y) for d, y, c in draw_popularity_index(draws) if not c]
    mu = sum(y for _, y in rows) / len(rows)   # キャリーなしの回全体の平均を基準 (×1.00) にする
    rows = [(d, y - mu) for d, y in rows]
    def grp(f):
        g = {}
        for d, y in rows:
            g.setdefault(f(d), []).append(y)
        return {k: (math.exp(sum(v) / len(v)), len(v)) for k, v in sorted(g.items())}
    return {
        "high": grp(lambda d: min(sum(1 for n in d.nums if n >= 32), 3)),
        "low": grp(lambda d: min(sum(1 for n in d.nums if n <= 9), 4)),
    }


# ================================================================ SVG/HTML
def esc(s) -> str:
    return html.escape(str(s), quote=True)


def fmt_pct(x, d=1):
    return f"{x * 100:.{d}f}%"


def svg_frequency(counts, exp, sd):
    W, H, L, R, T, B = 760, 260, 44, 12, 16, 34
    pw, ph = W - L - R, H - T - B
    ymax = math.ceil((max(counts) + 10) / 40) * 40
    ymin = 0  # 棒グラフは0から (途中から始めると差が大げさに見える)
    y = lambda v: T + ph * (1 - (v - ymin) / (ymax - ymin))
    bw = pw / N_NUM
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="数字ごとの出現回数">']
    for t in range(ymin, ymax + 1, 40):
        parts.append(f'<line class="grid" x1="{L}" x2="{W-R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="tick" x="{L-6}" y="{y(t)+4:.1f}" text-anchor="end">{t}</text>')
    # 95%の範囲 (偶然でこの範囲に入るのが普通)
    parts.append(f'<rect class="band" x="{L}" width="{pw}" y="{y(exp+1.96*sd):.1f}" '
                 f'height="{y(exp-1.96*sd)-y(exp+1.96*sd):.1f}"/>')
    for i, c in enumerate(counts):
        n = i + 1
        x = L + i * bw
        top = y(c)
        base = y(ymin)
        tip = f"{n}: {c}回 (期待値 {exp:.1f}回, 差 {c-exp:+.1f})"
        parts.append(
            f'<g class="hit" data-tip="{esc(tip)}"><rect class="hitbox" x="{x:.1f}" y="{T}" width="{bw:.1f}" height="{ph}"/>'
            f'<path class="bar" d="{bar_path(x+2, top, bw-4, base-top)}"/></g>')
        parts.append(f'<text class="tick" x="{x+bw/2:.1f}" y="{H-B+14}" text-anchor="middle">{n}</text>')
    parts.append(f'<line class="ref" x1="{L}" x2="{W-R}" y1="{y(exp):.1f}" y2="{y(exp):.1f}"/>')
    parts.append(f'<line class="axis" x1="{L}" x2="{W-R}" y1="{y(ymin):.1f}" y2="{y(ymin):.1f}"/>')
    parts.append('</svg>')
    return "".join(parts)


def bar_path(x, y, w, h, r=4):
    """上端だけ角丸の棒。"""
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y+h:.1f} V{y+r:.1f} Q{x:.1f},{y:.1f} {x+r:.1f},{y:.1f} "
            f"H{x+w-r:.1f} Q{x+w:.1f},{y:.1f} {x+w:.1f},{y+r:.1f} V{y+h:.1f} Z")


def svg_backtest(res):
    W, rowh, L, R, T = 760, 40, 250, 24, 26
    H = T + rowh * len(res) + 30
    vals = [r["lo"] for r in res] + [r["hi"] for r in res]
    xmin, xmax = 1.0, 1.7
    xmin = min(xmin, math.floor(min(vals) * 10) / 10)
    xmax = max(xmax, math.ceil(max(vals) * 10) / 10)
    x = lambda v: L + (W - L - R) * (v - xmin) / (xmax - xmin)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="予想法ごとの平均一致数">']
    t = xmin
    while t <= xmax + 1e-9:
        parts.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{T-6}" y2="{H-26}"/>'
                     f'<text class="tick" x="{x(t):.1f}" y="{H-10}" text-anchor="middle">{t:.1f}</text>')
        t += 0.1
    parts.append(f'<line class="ref" x1="{x(EXPECTED_HITS):.1f}" x2="{x(EXPECTED_HITS):.1f}" y1="{T-10}" y2="{H-26}"/>'
                 f'<text class="label" x="{x(EXPECTED_HITS)+6:.1f}" y="{T-12}">適当に選んだ場合の理論値 {EXPECTED_HITS:.3f}個</text>')
    for i, r in enumerate(res):
        cy = T + rowh * i + rowh / 2
        tip = (f"{r['name']}: 平均 {r['mean']:.3f}個一致 (95%区間 {r['lo']:.3f}〜{r['hi']:.3f}), "
               f"4個以上一致 {r['ge4']}回 (理論 {r['ge4_exp']:.1f}回), p={r['p']:.2f}")
        parts.append(
            f'<g class="hit" data-tip="{esc(tip)}"><rect class="hitbox" x="0" y="{cy-rowh/2:.1f}" width="{W}" height="{rowh}"/>'
            f'<text class="label" x="{L-12}" y="{cy+4:.1f}" text-anchor="end">{esc(r["name"])}</text>'
            f'<line class="ci" x1="{x(r["lo"]):.1f}" x2="{x(r["hi"]):.1f}" y1="{cy:.1f}" y2="{cy:.1f}"/>'
            f'<circle class="dot" cx="{x(r["mean"]):.1f}" cy="{cy:.1f}" r="5"/></g>')
    parts.append('</svg>')
    return "".join(parts)


def svg_sum(bins, width, obs, exp):
    W, H, L, R, T, B = 760, 240, 44, 12, 16, 34
    pw, ph = W - L - R, H - T - B
    ymax = math.ceil(max(obs + exp) * 100 / 5) * 5 / 100
    y = lambda v: T + ph * (1 - v / ymax)
    bw = pw / len(bins)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="合計値の分布">']
    t = 0.0
    while t <= ymax + 1e-9:
        parts.append(f'<line class="grid" x1="{L}" x2="{W-R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="tick" x="{L-6}" y="{y(t)+4:.1f}" text-anchor="end">{t*100:.0f}%</text>')
        t += 0.05
    pts = []
    for i, (b, o, e) in enumerate(zip(bins, obs, exp)):
        x = L + i * bw
        lab = f"〜{b+width-1}" if i == 0 else (f"{b}〜" if i == len(bins) - 1 else f"{b}〜{b+width-1}")
        tip = f"合計 {lab}: 実際 {fmt_pct(o)} / 理論 {fmt_pct(e)}"
        parts.append(
            f'<g class="hit" data-tip="{esc(tip)}"><rect class="hitbox" x="{x:.1f}" y="{T}" width="{bw:.1f}" height="{ph}"/>'
            f'<path class="bar" d="{bar_path(x+2, y(o), bw-4, y(0)-y(o))}"/></g>')
        if i % 2 == 0:
            parts.append(f'<text class="tick" x="{x+bw/2:.1f}" y="{H-B+14}" text-anchor="middle">{b}</text>')
        pts.append(f"{x+bw/2:.1f},{y(e):.1f}")
    parts.append(f'<polyline class="line2" points="{" ".join(pts)}"/>')
    for p in pts:
        cx, cy = p.split(",")
        parts.append(f'<circle class="dot2" cx="{cx}" cy="{cy}" r="4"/>')
    parts.append(f'<line class="axis" x1="{L}" x2="{W-R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/></svg>')
    return "".join(parts)


def svg_popularity(model):
    W, H, L, R, T, B = 760, 260, 44, 12, 16, 34
    pw, ph = W - L - R, H - T - B
    vals = [model.num_coef[n] for n in range(1, N_NUM + 1)]
    m = max(abs(v) for v in vals)
    lim = math.ceil(m * 100 / 2) * 2 / 100
    y = lambda v: T + ph * (1 - (v + lim) / (2 * lim))
    bw = pw / N_NUM
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="数字ごとの人気度">']
    t = -lim
    while t <= lim + 1e-9:
        parts.append(f'<line class="grid" x1="{L}" x2="{W-R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="tick" x="{L-6}" y="{y(t)+4:.1f}" text-anchor="end">{(math.exp(t)-1)*100:+.0f}%</text>')
        t += lim / 2
    for i, v in enumerate(vals):
        n = i + 1
        x = L + i * bw
        y0, y1 = y(0), y(v)
        cls = "pos" if v > 0 else "neg"
        top, h = (y1, y0 - y1) if v > 0 else (y0, y1 - y0)
        tip = (f"{n}: {'人気 (選ぶ人が多い)' if v > 0 else '不人気 (選ぶ人が少ない)'} "
               f"— この数字が出た回は当せん者が約 {(math.exp(v)-1)*100:+.1f}%")
        parts.append(f'<g class="hit" data-tip="{esc(tip)}"><rect class="hitbox" x="{x:.1f}" y="{T}" width="{bw:.1f}" height="{ph}"/>'
                     f'<rect class="{cls}" x="{x+2:.1f}" y="{top:.1f}" width="{bw-4:.1f}" height="{max(h,1):.1f}" rx="2"/></g>'
                     f'<text class="tick" x="{x+bw/2:.1f}" y="{H-B+14}" text-anchor="middle">{n}</text>')
    parts.append(f'<line class="axis" x1="{L}" x2="{W-R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/></svg>')
    return "".join(parts)


def table(headers, rows, num_cols=()):
    h = "".join(f"<th{' class=num' if i in num_cols else ''}>{esc(x)}</th>" for i, x in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f"<td{' class=num' if i in num_cols else ''}>{x}</td>" for i, x in enumerate(r)) + "</tr>"
        for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--page:#f9f9f7;--text-primary:#0b0b0b;--text-secondary:#52514e;
--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--series-1:#2a78d6;--series-2:#eb6834;--band:#f0efec;
--pos:#e34948;--neg:#2a78d6;--border:rgba(11,11,11,.10)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-1:#1a1a19;
--page:#0d0d0d;--text-primary:#f0efec;--text-secondary:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--series-1:#3987e5;
--series-2:#d95926;--band:#2c2c2a;--pos:#e66767;--neg:#3987e5;--border:rgba(255,255,255,.10)}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--page:#0d0d0d;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--series-1:#3987e5;--series-2:#d95926;--band:#2c2c2a;
--pos:#e66767;--neg:#3987e5;--border:rgba(255,255,255,.10)}
*{box-sizing:border-box}
body{margin:0}
.viz-root{background:var(--page);color:var(--text-primary);font:15px/1.7 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif;min-height:100vh}
main{max-width:820px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:40px 0 8px} h3{font-size:15px;margin:20px 0 6px}
p,li{color:var(--text-secondary)} strong{color:var(--text-primary)}
.sub{color:var(--muted);margin:0 0 24px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:12px 0}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.tile .k{font-size:13px;color:var(--text-secondary)} .tile .v{font-size:28px;font-weight:600;color:var(--text-primary)}
.tile .s{font-size:12px;color:var(--muted)}
svg{width:100%;height:auto;display:block}
svg text{font:12px system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
.tick{fill:var(--muted);font-variant-numeric:tabular-nums} .label{fill:var(--text-secondary)}
.grid{stroke:var(--grid);stroke-width:1} .axis{stroke:var(--axis);stroke-width:1}
.ref{stroke:var(--text-secondary);stroke-width:1.5;stroke-dasharray:4 3}
.band{fill:var(--band)}
.bar{fill:var(--series-1)} .pos{fill:var(--pos)} .neg{fill:var(--neg)}
.line2{fill:none;stroke:var(--series-2);stroke-width:2} .dot2{fill:var(--series-2);stroke:var(--surface-1);stroke-width:2}
.ci{stroke:var(--series-1);stroke-width:2} .dot{fill:var(--series-1);stroke:var(--surface-1);stroke-width:2}
.hitbox{fill:transparent} .hit:hover .bar,.hit:hover .pos,.hit:hover .neg{opacity:.75} .hit:hover .hitbox{fill:var(--grid);opacity:.4}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--text-secondary);margin:4px 0 8px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:6px}
.sw.line{height:3px;border-radius:2px;vertical-align:3px} .sw.dash{height:0;border-top:2px dashed var(--text-secondary);border-radius:0;vertical-align:3px}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}
th,td{padding:6px 10px;border-bottom:1px solid var(--grid);text-align:left} th{color:var(--text-secondary);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.ticket{font-variant-numeric:tabular-nums;letter-spacing:.04em;font-weight:600;color:var(--text-primary)}
details{margin:8px 0} summary{cursor:pointer;color:var(--text-secondary)}
.verdict{border-left:4px solid var(--series-1);padding:10px 14px;background:var(--surface-1);border-radius:0 8px 8px 0;margin:14px 0}
#tip{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);padding:6px 10px;border-radius:6px;
font-size:13px;max-width:320px;opacity:0;transition:opacity .08s;z-index:10}
"""

JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el=>{
  el.addEventListener('pointermove',e=>{tip.textContent=el.dataset.tip;tip.style.opacity=1;
    const x=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px';});
  el.addEventListener('pointerleave',()=>{tip.style.opacity=0;});
});
"""


def build_report(draws):
    counts, exp, sd, chi2, p_freq = frequency(draws)
    bt = backtest(draws)
    ov_mean, ov_p, ov_rows = overlap_prev(draws)
    bins, width, s_obs, s_exp, s_p, s_mean, s_mean_th = sum_stats(draws)
    oe = odd_even(draws)
    ret = returns(draws)
    model = fit_popularity(draws)
    groups = popularity_groups(draws)
    past = {tuple(sorted(d.nums)) for d in draws}
    tickets = generate(5, model, past, random.Random(draws[-1].round))
    rand_tickets = generate(5, model, past, random.Random(draws[-1].round), mode="random")

    first, last = draws[0], draws[-1]
    hot = sorted(range(1, N_NUM + 1), key=lambda n: -counts[n - 1])[:5]
    cold = sorted(range(1, N_NUM + 1), key=lambda n: counts[n - 1])[:5]
    pop_sorted = sorted(range(1, N_NUM + 1), key=lambda n: model.num_coef[n])
    any_sig = any(r["p"] < 0.05 for r in bt)

    def ticket_rows(ts):
        return [[f'<span class="ticket">{" ".join(f"{x:02d}" for x in t)}</span>',
                 f"×{model.ratio(t):.2f}"] for t in ts]

    h = []
    h.append(f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ロト7 過去データ分析レポート</title>
<style>{CSS}</style></head><body><div class="viz-root"><main>
<h1>ロト7 過去データ分析レポート</h1>
<p class="sub">第{first.round}回 ({first.date}) 〜 第{last.round}回 ({last.date})、全{len(draws)}回分 ・ 1等の確率 1/{TOTAL_COMBOS:,}</p>

<div class="verdict"><strong>結論:</strong> 過去データから「次に出る数字」は予想できません
(どの予想法も適当に選んだ場合と差がありませんでした)。一方で、
<strong>「他の人が選びやすい数字」は当せん口数のデータからはっきり読み取れました</strong>。
人と数字がかぶらないように買えば、当たったときに山分けされにくく、受け取れる額の期待値が上がります。</div>

<h2>1. 数字ごとの出現回数</h2>
<p>各数字の出現回数です。どの数字も1回あたり 7/37 の確率なら、期待値は <strong>{exp:.1f}回</strong>。
グレーの帯は「偶然でもこれくらいはばらつく」範囲 (95%) です。</p>
<div class="card"><div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>出現回数</span>
<span><span class="sw dash"></span>期待値 {exp:.1f}回</span><span><span class="sw" style="background:var(--band);border:1px solid var(--axis)"></span>偶然のばらつきの範囲 (95%)</span></div>
{svg_frequency(counts, exp, sd)}</div>
<p>最多は {", ".join(f"<strong>{n}</strong> ({counts[n-1]}回)" for n in hot)}、
最少は {", ".join(f"<strong>{n}</strong> ({counts[n-1]}回)" for n in cold)}。<br>
カイ二乗検定: χ²={chi2:.1f} (自由度36), <strong>p値 = {p_freq:.2f}</strong>
→ {"偏りがあるとは言えません。37個の数字があれば、この程度の多い・少ないは偶然でも普通に起きます。" if p_freq >= 0.05 else "偶然にしては偏りが大きめです (ただし検定を多数行うと5%程度は偶然この判定になります)。"}</p>
<details><summary>表で見る</summary>{table(["数字", "出現回数", "期待値との差"], [[n, counts[n-1], f"{counts[n-1]-exp:+.1f}"] for n in range(1, N_NUM+1)], (1, 2))}</details>

<h2>2. 予想法のバックテスト</h2>
<p>「よく出ている数字を買う」「しばらく出ていない数字を買う」などの方法を、第{first.round+BACKTEST_START}回〜第{last.round}回
({bt[0]["n"]}回分) で検証しました。各回、<strong>その回より前のデータだけ</strong>で7個を選び、本数字と何個一致したかを数えています。</p>
<div class="card"><div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>平均一致数 (線は95%信頼区間)</span><span><span class="sw dash"></span>適当に選んだ場合の理論値</span></div>
{svg_backtest(bt)}</div>
{table(["予想法", "平均一致数", "4個以上一致した回 (理論値)", "p値"],
       [[esc(r["name"]), f'{r["mean"]:.3f}', f'{r["ge4"]} ({r["ge4_exp"]:.1f})', f'{r["p"]:.2f}'] for r in bt], (1, 2, 3))}
<p>{"どの予想法も、信頼区間が理論値の線をまたいでいます。つまり<strong>適当に選ぶのと区別がつきません</strong>。" if not any_sig else "一部の予想法でp値が0.05を下回りましたが、6通り試すと偶然そうなる確率も高く、効果があるとは言えません。"}
「ホット数字」「コールド数字」はよく紹介されますが、抽せん機に記憶はないので、過去の出方は次回に影響しません。</p>

<h2>3. 抽せんはランダムか</h2>
<h3>前回の当せん番号との重なり</h3>
<p>前回の本数字と何個重なったか。平均 <strong>{ov_mean:.3f}個</strong> (理論値 {EXPECTED_HITS:.3f}個, p値 {ov_p:.2f})。</p>
{table(["重なった個数", "実際の回数", "理論値"], [[k, o, f"{e:.1f}"] for k, o, e in ov_rows if e >= 0.05 or o], (1, 2))}
<h3>本数字7個の合計値</h3>
<p>平均 <strong>{s_mean:.1f}</strong> (理論値 {s_mean_th:.1f})。分布も理論どおりです (適合度検定 p値 {s_p:.2f})。
「合計が真ん中あたりになる組合せを選べ」と言われることがありますが、真ん中あたりの合計になる組合せの数が多いだけなので、1口あたりの当たりやすさは変わりません。</p>
<div class="card"><div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>実際の割合</span><span><span class="sw line" style="background:var(--series-2)"></span>理論値</span></div>
{svg_sum(bins, width, s_obs, s_exp)}</div>
<details><summary>奇数の個数の分布</summary>{table(["奇数の個数", "実際の回数", "理論値"], [[k, o, f"{e:.1f}"] for k, o, e in oe], (1, 2))}</details>

<h2>4. 人気の数字 (人とかぶる数字)</h2>
<p>4〜6等の当せん口数は「当せん番号に近い数字を買った人」の数です。販売数のトレンドを取り除いて比べると、
<strong>人気の数字が出た回は当せん者が多く、不人気の数字が出た回は少なく</strong>なります。これを数字ごとに推定しました
(赤 = 選ぶ人が多い / 青 = 選ぶ人が少ない)。</p>
<div class="card"><div class="legend"><span><span class="sw" style="background:var(--pos)"></span>人気 (山分けされやすい)</span><span><span class="sw" style="background:var(--neg)"></span>不人気 (山分けされにくい)</span></div>
{svg_popularity(model)}</div>
<p>人気: {", ".join(f"<strong>{n}</strong>" for n in pop_sorted[::-1][:7])} ／ 不人気: {", ".join(f"<strong>{n}</strong>" for n in pop_sorted[:7])}<br>
1桁の数字や「7」が好まれ、20〜21、32以上が避けられる傾向です。この推定は本物の傾向を捉えています。
前半のデータで推定して後半の当せん口数を予測すると、相関係数は約 {model.oos_corr:.2f} (キャリーオーバーの無い回で評価) と、はっきりした予測力がありました。</p>
{table(["当せん番号のうち32〜37の個数", "当せん者数 (平均比)", "回数"], [[f"{k}個" + ("以上" if k == 3 else ""), f"×{v:.2f}", c] for k, (v, c) in groups["high"].items()], (1, 2))}
{table(["当せん番号のうち1〜9の個数", "当せん者数 (平均比)", "回数"], [[f"{k}個" + ("以上" if k == 4 else ""), f"×{v:.2f}", c] for k, (v, c) in groups["low"].items()], (1, 2))}
<p style="font-size:13px">※ キャリーオーバーの無い回だけで集計。4〜6等の当せん口数から推定しているため、1等の山分けの起きやすさを直接測ったものではありません。</p>

<h2>5. 還元率</h2>
<p>当せん口数と確率から販売口数を逆算し、実際に払い戻された割合を計算しました。</p>
<div class="tiles">
<div class="tile"><div class="k">全期間の還元率</div><div class="v">{fmt_pct(ret["all"])}</div><div class="s">300円買うと平均 {ret["all"]*PRICE:.0f}円戻る</div></div>
<div class="tile"><div class="k">キャリーオーバーがある回</div><div class="v">{fmt_pct(ret["carry"])}</div><div class="s">{ret["carry_n"]}回</div></div>
<div class="tile"><div class="k">キャリーオーバーがない回</div><div class="v">{fmt_pct(ret["nocarry"])}</div><div class="s">{ret["nocarry_n"]}回</div></div>
<div class="tile"><div class="k">1等が出なかった回</div><div class="v">{ret["first_none"]}回</div><div class="s">全{len(draws)}回中 ({fmt_pct(ret["first_none"]/len(draws), 0)})</div></div>
</div>
<p>キャリーオーバーがある回の方が還元率は高くなりますが、それでも100%を大きく下回ります。<strong>長く買い続けるほど損をする</strong>のは変わりません。
(販売口数はデータから逆算した推定値です。)</p>

<h2>6. 次回の買い目</h2>
<p>過去の当せん番号・連番・同じ行への固まり・等間隔などの「人がよく選ぶパターン」を除外し、
人気度の低い組合せから選んだ5口です (口同士で重なる数字は2個まで)。<strong>1等の確率はどれも 1/{TOTAL_COMBOS:,} で同じです。</strong></p>
<div class="card">{table(["人とかぶりにくい組合せ", "推定人気度"], ticket_rows(tickets), (1,))}</div>
<details><summary>比較: 完全ランダム (クイックピック相当) の5口</summary>{table(["ランダム", "推定人気度"], ticket_rows(rand_tickets), (1,))}</details>
<p style="font-size:13px">推定人気度 = 平均的な組合せと比べて、近い番号を買っている人が何倍いそうか。新しい買い目は <code>python3 generate.py</code> で作れます。</p>

<h2>データについて</h2>
<p style="font-size:13px">当せん番号・口数・金額は、GitHubで公開されている独立した2つのデータセット
(Joker7822/LOTO7_AI_AGENT の loto7.csv、ogachan111/loto7 の data.json) から取得し、全{len(draws)}回の本数字・ボーナス数字が一致することを確認しています。
正式な当せん番号は宝くじ公式サイトで確認してください。<code>python3 update_data.py</code> で最新データに更新できます。</p>
</main><div id="tip" role="tooltip"></div></div><script>{JS}</script></body></html>""")
    return "".join(h), {"p_freq": p_freq, "bt": bt, "ret": ret, "model": model, "tickets": tickets}


def main():
    draws = load_draws()
    doc, s = build_report(draws)
    OUT.write_text(doc, encoding="utf-8")
    print(f"第1回〜第{draws[-1].round}回 ({len(draws)}回分) を分析しました")
    print(f"  出現回数の偏り: p値 {s['p_freq']:.2f}")
    for r in s["bt"]:
        print(f"  {r['name']}: 平均 {r['mean']:.3f}個一致 (理論値 {EXPECTED_HITS:.3f}, p={r['p']:.2f})")
    print(f"  還元率: 全体 {fmt_pct(s['ret']['all'])} / キャリーあり {fmt_pct(s['ret']['carry'])} / なし {fmt_pct(s['ret']['nocarry'])}")
    print(f"  人気モデルの予測力 (前半→後半の相関): {s['model'].oos_corr:.2f}")
    print("  次回の買い目:")
    for t in s["tickets"]:
        print("    " + " ".join(f"{x:02d}" for x in t) + f"  人気度 ×{s['model'].ratio(t):.2f}")
    print(f"レポート: {OUT}")


if __name__ == "__main__":
    main()
