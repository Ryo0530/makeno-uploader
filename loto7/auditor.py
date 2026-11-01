#!/usr/bin/env python3
"""監査役: 予想システムを独立にチェックする。

予想ロジック (predictors.py) とは別に、次の5点を検査する。

  1. データ監査       当せんデータに欠番・重複・範囲外の数字などがないか
  2. リーク監査       予想が「未来のデータ」を使っていないか、同じ条件なら同じ結果になるか
  3. 成績監査         各予想法を過去の全回で検証し、完全ランダムより当たっているか
                      (完全ランダムを200通りの乱数で走らせた分布と比較し、Holm法で多重検定を補正)
  4. 確率監査         表示している当せん確率が正しいか (数え上げの理論値と大量シミュレーションの両方)
  5. 台帳監査         予想を出した後に書き換えていないか (ハッシュチェーン)、事前の予想の実際の成績

使い方:  python3 auditor.py   → audit_report.html を作成
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import random
import statistics
from pathlib import Path

import loto7lib
from loto7lib import fmt_small_pct, EXPECTED_HITS, N_NUM, PICK, PRICE, TIER_P, TOTAL_COMBOS, Draw, load_draws
from predictors import PREDICTORS, Predictor, Ticket

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "data" / "ledger.csv"
CACHE = HERE / "data" / "audit_cache.json"
REPORT = HERE / "audit_report.html"

BACKTEST_START = 120     # 最初の120回は学習用 (人気モデルの推定に必要)
TICKETS_PER_DRAW = 5     # バックテストで1回あたり何口買うか
NULL_RUNS = 200          # 完全ランダムを何通りの乱数で走らせるか
ALPHA = 0.05
TIER_NAMES = ["1等", "2等", "3等", "4等", "5等", "6等"]
ANY_PRIZE_P = sum(TIER_P)
AUDIT_VERSION = 1        # 予想ロジック・監査ロジックを変えたら上げる (キャッシュ無効化)


# ================================================================ 判定の基本
def tier(ticket, draw: Draw) -> int:
    """当せん等級 (1〜6)。はずれは0。"""
    s = set(ticket)
    k = len(s & set(draw.nums))
    j = len(s & set(draw.bonus))
    if k == 7:
        return 1
    if k == 6:
        return 2 if j >= 1 else 3
    if k == 5:
        return 4
    if k == 4:
        return 5
    if k == 3 and j >= 1:
        return 6
    return 0


def prize(t: int, draw: Draw) -> int:
    if t == 0:
        return 0
    p = draw.prizes[t - 1]
    if p == 0 and t == 1:
        # 1等該当なしの回に当たっていたら単独当せん → その回の繰越額をおおよその賞金とする
        return draw.carryover
    return p


# ================================================================ 1. データ監査
def audit_data(draws: list[Draw]) -> list[tuple[str, bool, str]]:
    checks = []
    rounds = [d.round for d in draws]
    checks.append(("回号が1から欠番・重複なく連続している", rounds == list(range(1, len(draws) + 1)),
                   f"第1回〜第{rounds[-1]}回"))
    bad = [d.round for d in draws if len(set(d.nums + d.bonus)) != 9 or not all(1 <= n <= N_NUM for n in d.nums + d.bonus)]
    checks.append(("本数字7個+ボーナス2個が1〜37の異なる数字", not bad, f"問題のある回: {bad[:5]}" if bad else "全回OK"))
    dates = [dt.date.fromisoformat(d.date) for d in draws]
    checks.append(("抽せん日が古い順に並んでいる", all(a < b for a, b in zip(dates, dates[1:])), f"{dates[0]}〜{dates[-1]}"))
    not_fri = [d.round for d, x in zip(draws, dates) if x.weekday() != 4]
    checks.append(("抽せん日が金曜日 (ロト7の抽せん曜日)", len(not_fri) <= 3,
                   "全回金曜" if not not_fri else f"金曜以外: 第{not_fri}回 (祝日・年末年始の変更の可能性)"))
    # 当せん口数の整合性: 6等の口数から逆算した販売口数で、5等の口数が理論どおりの比率か
    ratios = [d.winners[4] / d.winners[5] for d in draws if d.winners[5]]
    exp_ratio = TIER_P[4] / TIER_P[5]
    med = statistics.median(ratios)
    checks.append(("5等と6等の口数の比が確率の比に近い", abs(med / exp_ratio - 1) < 0.25,
                   f"実際の中央値 {med:.3f} / 理論 {exp_ratio:.3f}"))
    return checks


# ================================================================ 2. リーク監査
def audit_leakage(draws: list[Draw], sample_rounds=(150, 300, 450)) -> list[tuple[str, bool, str]]:
    checks = []
    targets = [i for i in sample_rounds if i < len(draws)] + [len(draws)]
    orig_load = loto7lib.load_draws

    def forbidden(*a, **k):
        raise RuntimeError("予想ロジックがデータファイルを直接読もうとしました")

    # (a) 予想中にデータファイルを読めないようにしても動くか = 渡された過去データしか使っていない
    loto7lib.load_draws = forbidden
    try:
        ok, err = True, ""
        first: dict[tuple[str, int], list] = {}
        for p in PREDICTORS.values():
            for i in targets:
                try:
                    first[(p.key, i)] = p.predict(draws[:i], 3, random.Random(f"leak-{p.key}-{i}"))
                except RuntimeError as e:
                    ok, err = False, f"{p.name}: {e}"
        checks.append(("予想ロジックが渡された過去データ以外を読まない", ok, err or "全予想法OK"))
    finally:
        loto7lib.load_draws = orig_load

    # (b) 未来のデータを見たあとでも、過去の回の予想が変わらない (キャッシュ経由の漏れがない)
    for p in PREDICTORS.values():
        p.predict(draws, 3, random.Random("warmup"))
    changed = [f"{p.key}@{i}" for p in PREDICTORS.values() for i in targets
               if p.predict(draws[:i], 3, random.Random(f"leak-{p.key}-{i}")) != first[(p.key, i)]]
    checks.append(("未来のデータを見た後も過去の回の予想が変わらない", not changed,
                   f"変化: {changed}" if changed else "全予想法OK"))

    # (c) 予想の形式が正しい
    bad = [k for k, ts in first.items() for t in ts
           if len(t) != PICK or len(set(t)) != PICK or list(t) != sorted(t) or not all(1 <= n <= N_NUM for n in t)]
    checks.append(("買い目が1〜37の異なる7個 (昇順)", not bad, f"不正: {bad[:3]}" if bad else "全予想法OK"))
    return checks


# ================================================================ 3. 成績監査
def run_backtest(pred: Predictor, draws: list[Draw], k: int, seed_tag: str) -> dict:
    hits = 0
    n_tickets = 0
    tiers = [0] * 7
    paid = 0
    for i in range(BACKTEST_START, len(draws)):
        d = draws[i]
        rng = random.Random(f"{seed_tag}-{pred.key}-{d.round}")
        for t in pred.predict(draws[:i], k, rng):
            n_tickets += 1
            hits += len(set(t) & set(d.nums))
            tt = tier(t, d)
            tiers[tt] += 1
            paid += prize(tt, d)
    return {
        "tickets": n_tickets,
        "mean_hits": hits / n_tickets,
        "tiers": tiers[1:],
        "any_rate": sum(tiers[1:]) / n_tickets,
        "return": paid / (n_tickets * PRICE),
    }


def null_distribution(draws: list[Draw], k: int, runs: int) -> list[dict]:
    """完全ランダムを runs 通りの乱数で走らせた成績 (偶然でどこまでばらつくか)。"""
    out = []
    period = draws[BACKTEST_START:]
    for s in range(runs):
        rng = random.Random(f"null-{s}")
        hits = anyp = paid = 0
        n = 0
        for d in period:
            main = set(d.nums)
            for _ in range(k):
                t = rng.sample(range(1, N_NUM + 1), PICK)
                n += 1
                hits += len(main.intersection(t))
                tt = tier(t, d)
                if tt:
                    anyp += 1
                    paid += prize(tt, d)
        out.append({"mean_hits": hits / n, "any_rate": anyp / n, "return": paid / (n * PRICE)})
    return out


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda x: x[1])
    m = len(items)
    adj, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adj[k] = running
    return adj


def audit_performance(draws: list[Draw], k=TICKETS_PER_DRAW, runs=NULL_RUNS, progress=print) -> dict:
    progress(f"  完全ランダムを{runs}通り実行中 (偶然のばらつきの基準)...")
    null = null_distribution(draws, k, runs)
    res = {}
    for p in PREDICTORS.values():
        progress(f"  バックテスト中: {p.name}")
        r = run_backtest(p, draws, k, "bt")
        for m in ("mean_hits", "any_rate", "return"):
            # 片側: 完全ランダムの何%以上の成績か。p値 = ランダムが同じ以上の成績を出した割合
            r[f"p_{m}"] = (1 + sum(1 for x in null if x[m] >= r[m])) / (runs + 1)
        res[p.key] = r
    # 主指標 = 平均一致数 (最も検出力が高い)。基準 (random) 以外の予想法で多重検定を補正
    adj = holm({kk: v["p_mean_hits"] for kk, v in res.items() if kk != "random"})
    for kk, v in res.items():
        v["p_adj"] = adj.get(kk)
        v["verdict"] = ("基準" if kk == "random" else
                        "ランダムより有意に良い (要・事前予想での再確認)" if adj[kk] < ALPHA else
                        "ランダムと差なし")
    band = {m: (_q([x[m] for x in null], 0.025), _q([x[m] for x in null], 0.975)) for m in ("mean_hits", "any_rate", "return")}
    return {"results": res, "null_band": band, "k": k, "runs": runs,
            "rounds": (draws[BACKTEST_START].round, draws[-1].round), "n_draws": len(draws) - BACKTEST_START}


def _q(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


# ================================================================ 4. 確率監査
def typical_prizes(draws: list[Draw], last: int = 100) -> list[float]:
    """直近の回の1口あたり当せん金の中央値 (期待値の目安用)。"""
    out = []
    for t in range(6):
        vals = [d.prizes[t] for d in draws[-last:] if d.prizes[t] > 0]
        out.append(statistics.median(vals) if vals else 0.0)
    return out


def audit_probability(tickets: list[Ticket], draws: list[Draw], sims: int = 200_000, seed: int = 0) -> dict:
    """表示する確率を、理論値と大量シミュレーションで二重チェックする。"""
    rng = random.Random(seed)
    k = len(tickets)
    single = [0] * 7          # 1口目だけの等級別回数
    any_set = 0               # いずれかの口が6等以上
    tier_set = [0] * 7        # いずれかの口がその等級
    for _ in range(sims):
        nums = rng.sample(range(1, N_NUM + 1), PICK + 2)
        d = Draw(0, "", tuple(nums[:PICK]), (nums[7], nums[8]), (), (), 0)
        ts = [tier(t, d) for t in tickets]
        single[ts[0]] += 1
        if any(ts):
            any_set += 1
        for t in set(ts):
            tier_set[t] += 1

    rows = []
    ok = True
    for t in range(1, 7):
        p = TIER_P[t - 1]
        obs = single[t] / sims
        se = math.sqrt(p * (1 - p) / sims)
        z = (obs - p) / se if se else 0.0
        good = abs(z) < 4  # 理論値から4標準誤差以内なら一致とみなす
        ok &= good
        rows.append({"tier": TIER_NAMES[t - 1], "p": p, "sim": obs, "z": z, "ok": good})

    # 異なる買い目どうしで1等は同時に当たらない → k口のどれかが1等 = k / 総組合せ数 (厳密)
    p_first_k = len(set(tickets)) / TOTAL_COMBOS
    prizes = typical_prizes(draws)
    ev = sum(p * v for p, v in zip(TIER_P, prizes))
    return {
        "per_ticket": rows, "sim_ok": ok, "sims": sims, "k": k,
        "p_first_k": p_first_k,
        "p_any_k_sim": any_set / sims,
        "p_any_k_upper": 1 - (1 - ANY_PRIZE_P) ** k,
        "tier_set_sim": [tier_set[t] / sims for t in range(1, 7)],
        "typical_prizes": prizes, "ev_per_ticket": ev,
    }


# ================================================================ 5. 台帳監査
LEDGER_FIELDS = ["seq", "created_at", "target_round", "data_through", "predictor", "ticket", "prev_hash", "hash"]


def _row_hash(row: dict) -> str:
    body = "|".join(str(row[f]) for f in LEDGER_FIELDS if f != "hash")
    return hashlib.sha256(body.encode()).hexdigest()


def read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    with LEDGER.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_ledger(target_round: int, data_through: int, entries: list[tuple[str, Ticket]]) -> int:
    """予想を台帳に追記する。各行に前の行のハッシュを含めるので、後から書き換えるとチェーンが壊れる。"""
    rows = read_ledger()
    if any(int(r["target_round"]) == target_round for r in rows):
        return 0  # 同じ回の予想は1回だけ (都合のいい予想を選び直せないように)
    prev = rows[-1]["hash"] if rows else "GENESIS"
    seq = int(rows[-1]["seq"]) if rows else 0
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    new = []
    for key, t in entries:
        seq += 1
        row = {"seq": seq, "created_at": now, "target_round": target_round, "data_through": data_through,
               "predictor": key, "ticket": " ".join(f"{x:02d}" for x in t), "prev_hash": prev}
        row["hash"] = _row_hash(row)
        prev = row["hash"]
        new.append(row)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    first = not LEDGER.exists()
    with LEDGER.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        if first:
            w.writeheader()
        w.writerows(new)
    return len(new)


def audit_ledger(draws: list[Draw]) -> dict:
    rows = read_ledger()
    by_round = {d.round: d for d in draws}
    prev = "GENESIS"
    broken = []
    late = []
    for r in rows:
        if r["prev_hash"] != prev or _row_hash(r) != r["hash"]:
            broken.append(r["seq"])
        prev = r["hash"]
        d = by_round.get(int(r["target_round"]))
        if d:
            # 抽せんは金曜18:45 (日本時間) = 09:45 UTC。これより後に作られた予想は無効
            draw_time = dt.datetime.fromisoformat(d.date + "T09:45:00+00:00")
            if dt.datetime.fromisoformat(r["created_at"]) >= draw_time:
                late.append(r["seq"])
        if int(r["data_through"]) >= int(r["target_round"]):
            late.append(r["seq"])
    settled = {}
    pending = set()
    for r in rows:
        d = by_round.get(int(r["target_round"]))
        if d is None:
            pending.add(int(r["target_round"]))
            continue
        if r["seq"] in late:
            continue
        t = tuple(int(x) for x in r["ticket"].split())
        s = settled.setdefault(r["predictor"], {"tickets": 0, "hits": 0, "tiers": [0] * 6, "paid": 0, "rounds": set()})
        s["tickets"] += 1
        s["hits"] += len(set(t) & set(d.nums))
        tt = tier(t, d)
        if tt:
            s["tiers"][tt - 1] += 1
            s["paid"] += prize(tt, d)
        s["rounds"].add(d.round)
    return {"rows": len(rows), "broken": broken, "late": sorted(set(late)), "settled": settled,
            "pending": sorted(pending)}


# ================================================================ キャッシュ付きの成績監査
def performance_cached(draws: list[Draw], progress=print) -> dict:
    key = {"version": AUDIT_VERSION, "last_round": draws[-1].round, "k": TICKETS_PER_DRAW, "runs": NULL_RUNS}
    if CACHE.exists():
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        if c.get("key") == key:
            return c["perf"]
    perf = audit_performance(draws, progress=progress)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"key": key, "perf": perf}, ensure_ascii=False, indent=1), encoding="utf-8")
    return perf


# ================================================================ HTMLレポート
def build_html(draws, data_chk, leak_chk, perf, prob, tickets, ledger) -> str:
    from analyze import CSS, JS, esc, table

    def mark(ok):
        return "✅ 合格" if ok else "❌ 不合格"

    all_ok = all(c[1] for c in data_chk + leak_chk) and prob["sim_ok"] and not ledger["broken"] and not ledger["late"]
    res = perf["results"]
    winners = [k for k, v in res.items() if k != "random" and v["p_adj"] < ALPHA]

    # 成績のドットチャート (平均一致数、グレー帯 = 完全ランダムの95%範囲)
    lo, hi = perf["null_band"]["mean_hits"]
    vals = [v["mean_hits"] for v in res.values()] + [lo, hi]
    xmin, xmax = min(vals) - 0.01, max(vals) + 0.01
    W, rowh, L, R, T = 760, 34, 200, 24, 30
    H = T + rowh * len(res) + 28
    x = lambda v: L + (W - L - R) * (v - xmin) / (xmax - xmin)
    svg = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="予想法ごとの平均一致数">',
           f'<rect class="band" x="{x(lo):.1f}" y="{T-8}" width="{x(hi)-x(lo):.1f}" height="{H-T-20}"/>',
           f'<line class="ref" x1="{x(EXPECTED_HITS):.1f}" x2="{x(EXPECTED_HITS):.1f}" y1="{T-12}" y2="{H-20}"/>',
           f'<text class="label" x="{x(EXPECTED_HITS):.1f}" y="{T-16}" text-anchor="middle">理論値 {EXPECTED_HITS:.3f}</text>']
    step = 0.01
    t = math.ceil(xmin / step) * step
    while t <= xmax:
        svg.append(f'<text class="tick" x="{x(t):.1f}" y="{H-4}" text-anchor="middle">{t:.2f}</text>')
        t += step
    for i, (kk, v) in enumerate(res.items()):
        cy = T + rowh * i + rowh / 2
        p = PREDICTORS[kk]
        tip = f"{p.name}: 平均 {v['mean_hits']:.4f}個一致 / 6等以上 {v['any_rate']*100:.2f}% / 還元率 {v['return']*100:.1f}%"
        svg.append(f'<g class="hit" data-tip="{esc(tip)}"><rect class="hitbox" x="0" y="{cy-rowh/2:.1f}" width="{W}" height="{rowh}"/>'
                   f'<text class="label" x="{L-12}" y="{cy+4:.1f}" text-anchor="end">{esc(p.name)}</text>'
                   f'<circle class="dot" cx="{x(v["mean_hits"]):.1f}" cy="{cy:.1f}" r="6"/></g>')
    svg.append("</svg>")

    perf_rows = []
    for kk, v in res.items():
        p = PREDICTORS[kk]
        perf_rows.append([
            f"<strong>{esc(p.name)}</strong><br><span style='font-size:12px'>{esc(p.desc)}</span>",
            f"{v['mean_hits']:.4f}", f"{v['any_rate']*100:.2f}%", f"{v['return']*100:.1f}%",
            "—" if v["p_adj"] is None else f"{v['p_mean_hits']:.2f} → {v['p_adj']:.2f}",
            esc(v["verdict"]),
        ])
    tier_rows = [[esc(PREDICTORS[kk].name)] + [str(c) for c in v["tiers"]] for kk, v in res.items()]
    n_t = res["random"]["tickets"]
    tier_rows.append(["<em>理論値 (期待される口数)</em>"] + [f"{n_t*p:.2f}" for p in TIER_P])

    prob_rows = [[r["tier"], f"1/{1/r['p']:,.0f}", fmt_small_pct(r['p']),
                  fmt_small_pct(r['sim']), f"{r['z']:+.2f}", "✅" if r["ok"] else "❌"] for r in prob["per_ticket"]]
    ticket_rows = [[f'<span class="ticket">{" ".join(f"{x:02d}" for x in t)}</span>'] for t in tickets]

    led_rows = []
    for kk, s in sorted(ledger["settled"].items(), key=lambda x: list(PREDICTORS).index(x[0]) if x[0] in PREDICTORS else 99):
        led_rows.append([esc(PREDICTORS[kk].name if kk in PREDICTORS else kk), len(s["rounds"]), s["tickets"],
                         f"{s['hits']/s['tickets']:.3f}", " / ".join(str(c) for c in s["tiers"]),
                         f"{s['paid']:,}円 / {s['tickets']*PRICE:,}円"])

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ロト7 予想システム 監査レポート</title>
<style>{CSS}</style></head><body><div class="viz-root"><main>
<h1>ロト7 予想システム 監査レポート</h1>
<p class="sub">データ: 第1回〜第{draws[-1].round}回 ({draws[-1].date}) ・ 監査対象: 第{draws[-1].round+1}回の予想</p>

<div class="verdict"><strong>監査役の結論:</strong>
{"検査項目はすべて合格しました。" if all_ok else "<strong>不合格の項目があります。</strong>下の表を確認してください。"}
{"ただし、<strong>どの予想法も完全ランダムより当たるとは言えません</strong>。" if not winners else
 f"{'・'.join(PREDICTORS[w].name for w in winners)} がバックテストで有意でしたが、過去データへの当てはめ過ぎの可能性があるため、台帳に記録した事前予想で確認できるまでは「当たりやすい」とは認めません。"}
1口が1等に当たる確率は、どの予想法でも <strong>1/{TOTAL_COMBOS:,}</strong> です。</div>

<h2>1. データ監査</h2>
{table(["検査項目", "結果", "詳細"], [[esc(a), mark(b), esc(c)] for a, b, c in data_chk])}

<h2>2. リーク監査 (未来のデータの混入)</h2>
<p>各予想法に「予想する回より前のデータ」だけを渡し、データファイルを直接読めない状態で動かしています。</p>
{table(["検査項目", "結果", "詳細"], [[esc(a), mark(b), esc(c)] for a, b, c in leak_chk])}

<h2>3. 成績監査 (バックテスト)</h2>
<p>第{perf['rounds'][0]}回〜第{perf['rounds'][1]}回の{perf['n_draws']}回で、各予想法に毎回{perf['k']}口ずつ買わせました
(その回より前のデータだけで予想)。比較の基準として、完全ランダムを{perf['runs']}通りの乱数で同じように走らせ、
「偶然でどこまで良い成績が出るか」を測っています。グレーの帯がその95%の範囲です。</p>
<div class="card"><div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>平均一致数 (1口あたり、本数字7個中)</span>
<span><span class="sw" style="background:var(--band);border:1px solid var(--axis)"></span>完全ランダムの95%範囲</span><span><span class="sw dash"></span>理論値</span></div>
{''.join(svg)}</div>
{table(["予想法", "平均一致数", "6等以上の当せん率", "還元率", "p値 → 多重検定補正後", "判定"], perf_rows, (1, 2, 3, 4))}
<p style="font-size:13px">理論値: 平均一致数 {EXPECTED_HITS:.4f}個 / 6等以上 {ANY_PRIZE_P*100:.2f}% ・
p値 = 完全ランダム{perf['runs']}回のうち、同じ以上の成績を出した割合。8つの予想法を同時に試しているので、Holm法で補正した値で判定しています (0.05未満なら有意)。
還元率は実際の当せん金額で計算しているため、たまたま高額当せんがあると大きく動きます。</p>
<details><summary>等級ごとの当せん口数</summary>{table(["予想法"] + TIER_NAMES, tier_rows, (1, 2, 3, 4, 5, 6))}</details>

<h2>4. 確率監査</h2>
<p>当せん確率を、組合せの数え上げで出した理論値と、{prob['sims']:,}回の模擬抽せんの両方で確かめました。</p>
{table(["等級", "確率 (理論値)", "確率 (%)", "模擬抽せんでの割合", "ずれ (標準誤差の何倍か)", "一致"], prob_rows, (1, 2, 3, 4))}
<div class="tiles">
<div class="tile"><div class="k">{prob['k']}口のどれかが1等</div><div class="v">1/{1/prob['p_first_k']:,.0f}</div><div class="s">口数に比例するだけ (厳密値)</div></div>
<div class="tile"><div class="k">{prob['k']}口のどれかが6等以上</div><div class="v">{prob['p_any_k_sim']*100:.1f}%</div><div class="s">模擬抽せん ・ 上限の目安 {prob['p_any_k_upper']*100:.1f}%</div></div>
<div class="tile"><div class="k">1口300円の期待値 (目安)</div><div class="v">{prob['ev_per_ticket']:.0f}円</div><div class="s">直近100回の当せん金の中央値で計算</div></div>
</div>

<h2>5. 台帳監査 (事前予想の記録)</h2>
<p><code>predict.py</code> を実行すると、全予想法の買い目が抽せん前に <code>data/ledger.csv</code> へ記録されます。
各行は前の行のハッシュを含むので、後から1文字でも書き換えると検出されます。同じ回の予想は1回しか記録できません。</p>
{table(["検査項目", "結果", "詳細"], [
    ["記録の改ざんがない (ハッシュチェーン)", mark(not ledger['broken']), esc(f"{ledger['rows']}行を検査" + (f" / 壊れている行: {ledger['broken']}" if ledger['broken'] else ""))],
    ["すべて抽せん前に記録されている", mark(not ledger['late']), esc("OK" if not ledger['late'] else f"抽せん後の記録 (成績から除外): {ledger['late']}")],
])}
{table(["予想法", "回数", "口数", "平均一致数", "1〜6等の口数", "払戻 / 購入額"], led_rows, (1, 2, 3, 5)) if led_rows else "<p>まだ結果の出た事前予想はありません。抽せん後に <code>update_data.py</code> → <code>auditor.py</code> を実行すると成績が出ます。</p>"}
{f"<p style='font-size:13px'>結果待ち: 第{', '.join(map(str, ledger['pending']))}回</p>" if ledger['pending'] else ""}

<h2>第{draws[-1].round+1}回の買い目 (監査済み)</h2>
<p>「人とかぶりにくい」予想法の{len(tickets)}口です。上の確率監査はこの買い目で行っています。</p>
<div class="card">{table(["買い目"], ticket_rows)}</div>
</main><div id="tip" role="tooltip"></div></div><script>{JS}</script></body></html>"""


def run_full_audit(draws: list[Draw], tickets: list[Ticket], progress=print):
    progress("[1/5] データ監査")
    data_chk = audit_data(draws)
    progress("[2/5] リーク監査")
    leak_chk = audit_leakage(draws)
    progress("[3/5] 成績監査 (初回は数分かかります。結果はキャッシュされます)")
    perf = performance_cached(draws, progress=progress)
    progress("[4/5] 確率監査")
    prob = audit_probability(tickets, draws)
    progress("[5/5] 台帳監査")
    ledger = audit_ledger(draws)
    REPORT.write_text(build_html(draws, data_chk, leak_chk, perf, prob, tickets, ledger), encoding="utf-8")
    return data_chk, leak_chk, perf, prob, ledger


def latest_tickets(draws: list[Draw], k: int = 5) -> list[Ticket]:
    """台帳にある次回の「人とかぶりにくい」予想。無ければその場で作る (記録はしない)。"""
    target = draws[-1].round + 1
    ts = [tuple(int(x) for x in r["ticket"].split()) for r in read_ledger()
          if int(r["target_round"]) == target and r["predictor"] == "unpopular"]
    if ts:
        return ts
    return PREDICTORS["unpopular"].predict(draws, k, random.Random(f"pred-unpopular-{target}"))


def main():
    draws = load_draws()
    tickets = latest_tickets(draws)
    data_chk, leak_chk, perf, prob, ledger = run_full_audit(draws, tickets)
    print()
    for a, b, c in data_chk + leak_chk:
        print(f"  {'OK ' if b else 'NG '} {a}  ({c})")
    print(f"  {'OK ' if prob['sim_ok'] else 'NG '} 表示確率が模擬抽せん{prob['sims']:,}回と一致")
    print(f"  {'OK ' if not ledger['broken'] else 'NG '} 台帳の改ざんなし ({ledger['rows']}行)")
    print("\n  予想法ごとの成績 (バックテスト):")
    for kk, v in perf["results"].items():
        padj = "" if v["p_adj"] is None else f"  補正後p={v['p_adj']:.2f}"
        print(f"    {PREDICTORS[kk].name:<14} 平均一致 {v['mean_hits']:.4f}  6等以上 {v['any_rate']*100:.2f}%"
              f"  還元率 {v['return']*100:5.1f}%{padj}  → {v['verdict']}")
    print(f"\nレポート: {REPORT}")


if __name__ == "__main__":
    main()
