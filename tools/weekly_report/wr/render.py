"""集計結果から、note用レポート・動画台本・ショート台本・X投稿・YouTubeメタ情報を作る。

テンプレートだけで作るのでAPI費用はかからない。数字は集計結果をそのまま使い、
読めなかった値は「—」と書いて、推測で埋めない。
"""
from __future__ import annotations

import json
from datetime import date

from .model import FAILURE_LABELS, FAILURE_STAGES

DISCLAIMER = (
    "※ この内容は個人の検証記録です。将来の的中や利益を約束するものではありません。"
    "仮想購入（実際にはお金を賭けていない記録）と実際の馬券は分けて集計しています。"
    "馬券の購入は20歳になってから。無理のない範囲で楽しみましょう。"
)

PAID_LINE = "<!-- ここから有料（noteの有料ラインをこの位置に置く） -->"

# Capital Level SPEC の L1（試験購入）最低条件の一部
L1_MIN = {"buy_races": 50, "days": 5, "target_races": 100, "span_days": 14}


# ---------------------------------------------------------------- format


def yen(n: int | None) -> str:
    return "—" if n is None else f"{n:,}円"


def signed_yen(n: int | None) -> str:
    if n is None:
        return "—"
    return f"+{n:,}円" if n > 0 else f"{n:,}円"


def pct(x: float | None) -> str:
    return "—（投資0）" if x is None else f"{x * 100:.1f}%"


def md_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def period_label(m: dict) -> str:
    s = date.fromisoformat(m["week_start"])
    return f"{s.year}/{md_date(m['week_start'])}〜{md_date(m['week_end'])}"


def main_cohort(m: dict) -> dict | None:
    """今週いちばん仮想投資が多かった cohort を話の主役にする。"""
    if not m["cohorts"]:
        return None
    return max(m["cohorts"], key=lambda c: (c["week"]["stake"], c["week"]["target_races"]))


def _hits(c: dict, period: str = "week") -> str:
    if c["has_aggregate_only_days"] and period == "week":
        return "—"
    return f"{c[period]['hit_races']}R"


def headline(m: dict) -> str:
    c = main_cohort(m)
    if c is None or (c["week"]["stake"] == 0 and c["week"]["target_races"] == 0):
        return "今週は検証データがありませんでした"
    w = c["week"]
    if w["stake"] == 0:
        return f"今週は対象{w['target_races']}Rで仮想BUYは0R（見送りの週）"
    if w["profit"] >= 0:
        return f"今週はプラス{w['profit']:,}円。ただし母数はまだ小さい"
    return f"今週も負け。仮想{abs(w['profit']):,}円のマイナス（回収率{pct(w['roi'])}）"


# ---------------------------------------------------------------- pieces


def _cohort_table(c: dict) -> str:
    w, t = c["week"], c["cumulative"]
    rows = [
        ("精算済みの対象R", f"{w['target_races']}R", f"{t['target_races']}R"),
        ("仮想BUYしたR", f"{w['buy_races']}R", f"{t['buy_races']}R"),
        ("的中R", _hits(c), f"{t['hit_races']}R" if not c["has_aggregate_only_days"] else "—"),
        ("仮想投資", yen(w["stake"]), yen(t["stake"])),
        ("仮想払戻", yen(w["payout"]), yen(t["payout"])),
        ("収支", signed_yen(w["profit"]), signed_yen(t["profit"])),
        ("回収率", pct(w["roi"]), pct(t["roi"])),
        ("最大の当たり1本を除いた回収率", pct(w["roi_ex_top1"]), pct(t["roi_ex_top1"])),
        ("保留R（未精算額）", f"{w['held_races']}R（{yen(w['held_stake'])}）",
         f"{t['held_races']}R（{yen(t['held_stake'])}）"),
        ("対象外R", f"{w['excluded_races']}R", f"{t['excluded_races']}R"),
    ]
    out = ["| 項目 | 今週 | 累計 |", "| --- | --- | --- |"]
    out += [f"| {a} | {b} | {c_} |" for a, b, c_ in rows]
    return "\n".join(out)


def _gate_line(c: dict) -> str:
    g = c["week"]["gate_counts"]
    if not g:
        return ""
    order = ["CORE", "WATCH_CHAOS", "WATCH_NEAR", "PASS"]
    parts = [f"{k} {g[k]}R" for k in order if k in g] + \
            [f"{k} {v}R" for k, v in g.items() if k not in order]
    return "区分の内訳：" + " / ".join(parts)


def _failure_section(c: dict) -> list[str]:
    f = c["failures"]
    lines = []
    if f["missed_races"] == 0:
        return ["今週は外れたレースの明細がありませんでした。"]
    lines.append(f"的中しなかった {f['missed_races']}R（買っていないWATCH/PASSのRも含む）のうち、"
                 f"原因の分類が付いていたのは {f['classified']}R。")
    if f["classified"]:
        lines.append("")
        lines.append("| どこで外れたか | R数 |")
        lines.append("| --- | --- |")
        for k in FAILURE_STAGES:
            lines.append(f"| {FAILURE_LABELS[k]} | {f['stages'][k]}R |")
        top = max(FAILURE_STAGES, key=lambda k: f["stages"][k])
        lines.append("")
        lines.append(f"いちばん多かったのは「{FAILURE_LABELS[top]}」での取りこぼし（{f['stages'][top]}R）。")
    roles = f["missed_roles"]
    if any(roles.values()):
        lines.append(f"取りこぼした役割：1着（HEAD）{roles['HEAD']} / 2着（SECOND）{roles['SECOND']} / 3着（THIRD）{roles['THIRD']}")
    return lines


def _band_section(c: dict) -> list[str]:
    bands = c["bands"]
    if not bands or bands[0]["known_races"] == 0:
        return ["三連単の払戻データが無いため、高配当の集計はできませんでした。"]
    lines = [f"三連単の払戻がわかった {bands[0]['known_races']}R の内訳：", "",
             "| 三連単（100円あたり） | 発生 | 仮想BUYで的中 |", "| --- | --- | --- |"]
    for b in bands:
        lines.append(f"| {b['threshold'] // 10_000}万円以上 | {b['occurred']}R | {b['captured']}R |")
    return lines


def _highlight_section(c: dict) -> list[str]:
    h = c["highlights"]
    lines = []

    def race_label(r: dict) -> str:
        parts = [md_date(r["day"])]
        if r.get("venue") and str(r["venue"]) not in str(r["race_id"]):
            parts.append(str(r["venue"]))
        parts.append(str(r["race_id"]))
        return " ".join(parts)

    if h["best_hit"]:
        r = h["best_hit"]
        lines.append(f"- 今週いちばんの的中：{race_label(r)}（投資{yen(r['stake'])} → 払戻{yen(r['payout'])}）")
    if h["biggest_miss"]:
        r = h["biggest_miss"]
        why = FAILURE_LABELS.get(r.get("primary_failure") or "", "分類なし")
        lines.append(f"- いちばん痛かった外れ：{race_label(r)}（投資{yen(r['stake'])}、原因：{why}）")
    if h["biggest_upset"]:
        r = h["biggest_upset"]
        lines.append(f"- 今週いちばんの荒れたレース：{race_label(r)}（三連単{yen(r['trifecta_payout_per100'])}）")
    if h["worst_day"]:
        lines.append(f"- いちばん負けた日：{md_date(h['worst_day']['day'])}（{signed_yen(h['worst_day']['profit'])}）")
    return lines or ["（明細が無いため、ハイライトは出せませんでした）"]


def _actual_section(m: dict) -> list[str]:
    if not m["has_actual_bets"]:
        return ["実馬券のデータは読み込んでいません（actual_bets.csv を置くと集計します）。"]
    w, t = m["actual_week"]["total"], m["actual_cumulative"]["total"]
    lines = ["| 項目 | 今週 | 累計 |", "| --- | --- | --- |",
             f"| 購入数（確定分） | {w['bets']}件 | {t['bets']}件 |",
             f"| 購入額 | {yen(w['stake'])} | {yen(t['stake'])} |",
             f"| 払戻 | {yen(w['payout'])} | {yen(t['payout'])} |",
             f"| 収支 | {signed_yen(w['profit'])} | {signed_yen(t['profit'])} |",
             f"| 回収率 | {pct(w['roi'])} | {pct(t['roi'])} |",
             f"| 未確定 | {yen(w['pending_stake'])} | {yen(t['pending_stake'])} |"]
    by = m["actual_week"]["by_source"]
    if len(by) > 1:
        lines.append("")
        lines.append("今週の購入先別：" + " / ".join(
            f"{k} {signed_yen(v['profit'])}" for k, v in by.items()))
    return lines


def _capital_section(m: dict) -> list[str]:
    if not m["capital"]:
        return ["資金レベル（Capital Level）の判定は読み込んでいません。"]
    lines = ["| 対象 | レベル | 判定 | 確からしさ |", "| --- | --- | --- | --- |"]
    for c in m["capital"]:
        lines.append(f"| {c['cohort']} | {c['level'] or '—'} | {c['decision'] or '—'} | {c['confidence'] or '—'} |")
    lines.append("")
    lines.append("L0＝まだ研究段階（実際に賭ける額の目安は0円）。レベルが上がっても、それだけで実際に賭けることにはしません。")
    return lines


def _next_points(m: dict) -> list[str]:
    lines = []
    for c in m["cohorts"]:
        t = c["cumulative"]
        span = 0
        if c["first_day"]:
            span = (date.fromisoformat(m["week_end"]) - date.fromisoformat(c["first_day"])).days + 1
        have = {"buy_races": t["buy_races"], "days": c["cumulative_days"],
                "target_races": t["target_races"], "span_days": span}
        label = {"buy_races": ("仮想BUY", "R"), "days": ("開催日", "日"),
                 "target_races": ("対象R", "R"), "span_days": ("観測期間", "日")}
        short = [f"{label[k][0]}あと{L1_MIN[k] - have[k]}{label[k][1]}"
                 for k in L1_MIN if have[k] < L1_MIN[k]]
        if short:
            lines.append(f"- {c['system']}：次の判定（L1）の最低条件まで " + "、".join(short))
        else:
            lines.append(f"- {c['system']}：L1の母数の最低条件は到達。成績の条件はCapital判定を参照")
        if c["losing_streak"]:
            lines.append(f"  - 仮想BUYの連敗：いま{c['losing_streak']}連敗中（40連敗で安全停止のルール）")
    return lines or ["- データがそろったら、ここに来週見るべき点が出ます。"]


# ---------------------------------------------------------------- outputs


def render_note(m: dict, cfg: dict) -> str:
    ch = cfg.get("channel_name", "負けの研究室")
    out = [f"# {ch} 週報 {period_label(m)}", "",
           f"**{headline(m)}**", "",
           "AIで競馬を予想して、その結果を毎週そのまま記録しています。勝った週も負けた週も同じ形で出します。", "",
           "## 今週のまとめ", ""]
    for c in m["cohorts"]:
        w = c["week"]
        out.append(f"- **{c['system']}**（{c['market']}）：対象{w['target_races']}R・仮想BUY{w['buy_races']}R・"
                   f"的中{_hits(c)}／収支{signed_yen(w['profit'])}（回収率{pct(w['roi'])}）")
    if m["has_actual_bets"]:
        w = m["actual_week"]["total"]
        out.append(f"- **実馬券**：購入{yen(w['stake'])} → 払戻{yen(w['payout'])}（{signed_yen(w['profit'])}）")
    if not m["cohorts"] and not m["has_actual_bets"]:
        out.append("- 今週は集計できるデータがありませんでした。")
    out += ["", PAID_LINE, ""]

    for c in m["cohorts"]:
        out += [f"## 仮想成績：{c['system']}", "", _cohort_table(c), ""]
        gl = _gate_line(c)
        if gl:
            out += [gl, ""]
        if c["has_aggregate_only_days"]:
            out += ["※ 今週は一部の日がレース明細なしの日次集計だけなので、的中数・失敗分析はその日を含みません。", ""]
        out += ["### 負けの解剖", ""] + _failure_section(c) + [""]
        out += ["### 高配当はどれだけ出て、どれだけ取れたか", ""] + _band_section(c) + [""]
        out += ["### 今週のハイライト", ""] + _highlight_section(c) + [""]

    out += ["## 実馬券の収支（仮想とは別集計）", ""] + _actual_section(m) + [""]
    out += ["## 資金レベルの判定", ""] + _capital_section(m) + [""]
    out += ["## 来週の観察ポイント", ""] + _next_points(m) + [""]
    out += ["## 用語", "",
            "- 仮想BUY：実際にはお金を賭けず、事前に固定した買い目を記録して結果と照らし合わせたもの（1点100円換算）",
            "- 回収率：払戻 ÷ 投資。投資0のときは計算しません",
            "- 保留：同着・返還・取消などで自動精算できなかったレース。損にも得にも数えていません", ""]
    if m["warnings"]:
        out += ["## データ注記（自動）", ""] + [f"- {w}" for w in m["warnings"]] + [""]
    out += ["---", DISCLAIMER, ""]
    return "\n".join(out)


def render_video_script(m: dict, cfg: dict) -> str:
    ch = cfg.get("channel_name", "負けの研究室")
    c = main_cohort(m)
    out = [f"# 動画台本：{ch} 週報 {period_label(m)}", "",
           "目安：5〜7分。【】は画面に出すテロップ、それ以外はナレーション。", ""]
    out += ["## 1. つかみ（0:00〜0:20）", ""]
    if c and c["week"]["stake"] > 0:
        w = c["week"]
        verb = "勝ちました" if w["profit"] >= 0 else "負けました"
        out += [f"今週、AIの競馬予想は仮想で{abs(w['profit']):,}円{verb}。",
                f"【仮想収支 {signed_yen(w['profit'])}／回収率 {pct(w['roi'])}】",
                "なぜそうなったのか、今週も数字で解剖していきます。"]
    else:
        out += ["今週、AIは1レースも買いませんでした。",
                "買わなかったことにも理由があります。今日はそこを見ていきます。"]
    out += ["", f"どうも、{ch}です。このチャンネルでは、AIで競馬を予想して、その結果を勝ち負け関係なく全部記録しています。", ""]

    out += ["## 2. 今週の結果（0:20〜1:30）", ""]
    for co in m["cohorts"]:
        w = co["week"]
        out += [f"まず{co['system']}。精算済みの対象は{w['target_races']}レース、そのうち仮想で買ったのは{w['buy_races']}レース。",
                f"【投資 {yen(w['stake'])} → 払戻 {yen(w['payout'])}】",
                f"的中は{_hits(co)}、回収率は{pct(w['roi'])}でした。"]
        if w["held_races"]:
            out.append(f"同着や返還などで自動精算できなかった{w['held_races']}レースは、損にも得にも数えていません。")
        out.append("")
    if m["has_actual_bets"]:
        w = m["actual_week"]["total"]
        out += ["ここからは仮想ではなく、実際に買った馬券です。",
                f"【実馬券 {yen(w['stake'])} → {yen(w['payout'])}（{signed_yen(w['profit'])}）】", ""]

    out += ["## 3. 負けの解剖（1:30〜4:00）", ""]
    if c:
        f = c["failures"]
        if f["classified"]:
            top = max(FAILURE_STAGES, key=lambda k: f["stages"][k])
            out += [f"外れたレースを、どこで外れたかで分けてみます。今週いちばん多かったのは「{FAILURE_LABELS[top]}」でした。",
                    "【" + " / ".join(f"{FAILURE_LABELS[k]} {f['stages'][k]}R" for k in FAILURE_STAGES) + "】"]
            explain = {
                "selection": "そもそも買うべきレースを選べていなかった、ということです。",
                "role": "1着・2着・3着の候補に、来た馬が入っていなかった、ということです。",
                "theory": "候補の馬は合っていたのに、組み合わせの中に正解が無かった、ということです。",
                "compression": "正解は理論上の候補に入っていたのに、点数を絞るときに削ってしまった。いちばん悔しいパターンです。",
            }
            out.append(explain[top])
        else:
            out.append("今週は外れの原因の分類データがありませんでした。来週から集計できるように準備中です。")
        h = c["highlights"]
        if h["biggest_miss"]:
            r = h["biggest_miss"]
            out += ["", f"いちばん痛かったのは{md_date(r['day'])}の{r['race_id']}。投資{yen(r['stake'])}が丸ごと外れました。"]
        if h["biggest_upset"] and h["biggest_upset"]["trifecta_payout_per100"]:
            r = h["biggest_upset"]
            out.append(f"今週いちばん荒れたのは{md_date(r['day'])}の{r['race_id']}、三連単{yen(r['trifecta_payout_per100'])}でした。")
    out.append("")

    out += ["## 4. 研究メモ（4:00〜5:30）", ""]
    if c and c["cumulative"]["roi"] is not None and c["cumulative"]["roi_ex_top1"] is not None \
            and c["cumulative"]["roi"] >= 1 > c["cumulative"]["roi_ex_top1"]:
        t = c["cumulative"]
        out += [f"累計の回収率は{pct(t['roi'])}ですが、いちばん大きい当たり1本を除くと{pct(t['roi_ex_top1'])}。",
                "つまり今のプラスは、ほぼ1本の大当たりのおかげです。実力かどうかは、まだ判断できません。", ""]
    out += [line.lstrip("- ") for line in _next_points(m)]
    out += ["資金レベルの判定はまだ研究段階なので、実際に賭ける額は増やしません。数字がそろうまで記録を続けます。", ""]

    out += ["## 5. しめ（5:30〜）", ""]
    note_url = cfg.get("note_url")
    if note_url:
        out.append("レースごとの細かい数字と、外れたレースの全リストはnoteにまとめています。概要欄からどうぞ。")
    out += ["来週も、負けの理由を数字で追いかけます。チャンネル登録しておいてもらえると、記録が続きます。", "",
            f"【{DISCLAIMER}】", ""]
    return "\n".join(out)


def render_shorts(m: dict, cfg: dict) -> str:
    c = main_cohort(m)
    out = [f"# ショート台本 {period_label(m)}（30〜45秒）", ""]
    if c and c["week"]["stake"] > 0:
        w = c["week"]
        out += [f"【AI競馬、今週は{signed_yen(w['profit'])}】",
                f"仮想で{w['buy_races']}レース買って、当たったのは{_hits(c)}。",
                f"回収率は{pct(w['roi'])}。"]
        f = c["failures"]
        if f["classified"]:
            top = max(FAILURE_STAGES, key=lambda k: f["stages"][k])
            out.append(f"いちばん多かった負け方は「{FAILURE_LABELS[top]}」での取りこぼし。")
    else:
        out += ["【AI競馬、今週は1レースも買わず】", "条件を満たすレースが無かったので見送りました。"]
    out += ["負けの理由は本編で解剖しています。", "", "【馬券の購入は20歳になってから】", ""]
    return "\n".join(out)


def render_x_post(m: dict, cfg: dict) -> str:
    c = main_cohort(m)
    ch = cfg.get("channel_name", "負けの研究室")
    if c and c["week"]["stake"] > 0:
        w = c["week"]
        body = (f"【{ch} 週報 {md_date(m['week_start'])}〜{md_date(m['week_end'])}】\n"
                f"AI競馬（仮想）：{w['buy_races']}R購入・的中{_hits(c)}\n"
                f"収支 {signed_yen(w['profit'])}／回収率 {pct(w['roi'])}")
    else:
        body = f"【{ch} 週報 {md_date(m['week_start'])}〜{md_date(m['week_end'])}】\n今週のAI競馬は購入なし（見送り）"
    tail = "\n負けの理由は動画とnoteで解剖中 #競馬 #AI競馬"
    if cfg.get("note_url"):
        tail += f"\n{cfg['note_url']}"
    return body + tail + "\n"


def render_youtube_meta(m: dict, cfg: dict) -> dict:
    ch = cfg.get("channel_name", "負けの研究室")
    c = main_cohort(m)
    if c and c["week"]["stake"] > 0:
        w = c["week"]
        title = f"【AI競馬】今週は{signed_yen(w['profit'])}、回収率{pct(w['roi'])}｜{ch} 週報 {period_label(m)}"
    else:
        title = f"【AI競馬】今週は購入なし｜{ch} 週報 {period_label(m)}"
    desc = [f"{ch}の週報（{period_label(m)}）。AIで競馬を予想し、勝ち負け関係なく結果をすべて記録しています。", ""]
    for co in m["cohorts"]:
        w = co["week"]
        desc.append(f"■ {co['system']}：対象{w['target_races']}R／仮想BUY{w['buy_races']}R／"
                    f"収支{signed_yen(w['profit'])}（回収率{pct(w['roi'])}）")
    if cfg.get("note_url"):
        desc += ["", f"▼ レースごとの詳細（note）", cfg["note_url"]]
    desc += ["", DISCLAIMER]
    return {
        "title": title[:100],
        "description": "\n".join(desc)[:4900],
        "tags": ["競馬", "AI競馬", "競馬予想", "地方競馬", "回収率", ch],
        "categoryId": cfg.get("youtube_category_id", "17"),
        # 自動アップロードでいきなり公開しない。確認してから手で公開にする前提。
        "privacyStatus": "private",
        "selfDeclaredMadeForKids": False,
        "defaultLanguage": "ja",
    }


def render_all(m: dict, cfg: dict) -> dict[str, str]:
    return {
        "report_note.md": render_note(m, cfg),
        "video_script.md": render_video_script(m, cfg),
        "shorts_script.md": render_shorts(m, cfg),
        "x_post.txt": render_x_post(m, cfg),
        "youtube_meta.json": json.dumps(render_youtube_meta(m, cfg), ensure_ascii=False, indent=2) + "\n",
    }
