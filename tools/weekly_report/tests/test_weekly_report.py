import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import weekly_report  # noqa: E402
from wr.adapters import (  # noqa: E402
    load_sources, parse_capital, parse_local_report, skeleton, to_status,
)
from wr.lint import find_violations  # noqa: E402
from wr.metrics import compute, last_completed_week, week_bounds  # noqa: E402
from wr.model import EXCLUDED, HELD, SETTLED, ActualBet, Dataset, RaceRecord  # noqa: E402
from wr.render import render_all  # noqa: E402

D = date(2026, 10, 27)


def race(rid, stake, payout, status=SETTLED, day=D, **kw):
    return RaceRecord(day=day, market="Local", system="S", race_id=rid, status=status,
                      stake=stake, payout=payout, **kw)


class StatusTest(unittest.TestCase):
    def test_order(self):
        self.assertEqual(to_status("UNSETTLED"), HELD)
        self.assertEqual(to_status("DAILY_BUDGET_SKIP"), SETTLED)
        self.assertEqual(to_status("EXCLUDED_MISSING_ODDS"), EXCLUDED)
        self.assertEqual(to_status("settled"), SETTLED)
        self.assertEqual(to_status("保留"), HELD)
        self.assertEqual(to_status(None), SETTLED)


class WeekTest(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(week_bounds(date(2026, 11, 1)), (date(2026, 10, 26), date(2026, 11, 1)))
        self.assertEqual(last_completed_week(date(2026, 11, 2)), (date(2026, 10, 26), date(2026, 11, 1)))
        self.assertEqual(last_completed_week(date(2026, 11, 8)), (date(2026, 10, 26), date(2026, 11, 1)))


class LocalReportTest(unittest.TestCase):
    def test_per_race_with_aliases(self):
        report = {"Race_Results": [
            {"raceId": "R1", "Status": "SETTLED", "category": "core", "points": 30, "payout": 0,
             "primary_failure": "圧縮", "missed_roles": "HEAD;THIRD"},
            {"race_id": "R2", "status": "PENDING", "investment": "1,200", "payout": None},
        ]}
        recs, warns = parse_local_report(report, day=D, market="Local", system="S", source_file="x")
        self.assertEqual(warns, [])
        self.assertEqual(recs[0].race_id, "R1")
        self.assertEqual(recs[0].gate, "CORE")
        self.assertEqual(recs[0].stake, 3000)  # 30点×100円
        self.assertEqual(recs[0].primary_failure, "compression")
        self.assertEqual(recs[0].missed_roles, ["HEAD", "THIRD"])
        self.assertEqual(recs[1].status, HELD)
        self.assertEqual(recs[1].stake, 1200)

    def test_dict_keyed_races(self):
        report = {"races": {"R9": {"stake": 100, "payout": 500}}}
        recs, _ = parse_local_report(report, day=D, market="Local", system="S", source_file="x")
        self.assertEqual((recs[0].race_id, recs[0].payout), ("R9", 500))

    def test_summary_fallback(self):
        report = {"summary": {"investment": 5000, "payout": 2000, "settled_races": 12,
                              "buy_races": 3, "held_races": 1, "unsettled_amount": 800}}
        recs, warns = parse_local_report(report, day=D, market="Local", system="S", source_file="x")
        self.assertEqual(len(recs), 2)
        self.assertTrue(all(r.is_aggregate for r in recs))
        self.assertEqual(len(warns), 1)
        m = compute(Dataset(races=recs), *week_bounds(D))
        w = m["cohorts"][0]["week"]
        self.assertEqual((w["target_races"], w["buy_races"], w["stake"], w["held_stake"]), (12, 3, 5000, 800))

    def test_unknown_shape(self):
        recs, warns = parse_local_report({"foo": 1}, day=D, market="Local", system="S", source_file="x")
        self.assertEqual(recs, [])
        self.assertIn("読み飛ばしました", warns[0])


class MetricsTest(unittest.TestCase):
    def test_held_and_excluded_not_in_roi(self):
        ds = Dataset(races=[race("a", 1000, 0), race("b", 1000, 3000),
                            race("c", 5000, None, status=HELD), race("d", 0, 0, status=EXCLUDED)])
        w = compute(ds, *week_bounds(D))["cohorts"][0]["week"]
        self.assertEqual((w["stake"], w["payout"], w["held_stake"], w["excluded_races"]), (2000, 3000, 5000, 1))
        self.assertAlmostEqual(w["roi"], 1.5)
        self.assertAlmostEqual(w["roi_ex_top1"], 0.0)

    def test_roi_none_without_stake(self):
        ds = Dataset(races=[race("a", 0, 0, gate="PASS")])
        w = compute(ds, *week_bounds(D))["cohorts"][0]["week"]
        self.assertIsNone(w["roi"])
        self.assertEqual(w["target_races"], 1)

    def test_cohorts_not_merged(self):
        r1 = race("a", 1000, 0)
        r2 = RaceRecord(day=D, market="JRA", system="Set04", race_id="a", stake=500, payout=0)
        m = compute(Dataset(races=[r1, r2]), *week_bounds(D))
        self.assertEqual(len(m["cohorts"]), 2)

    def test_actual_bets_separate(self):
        ds = Dataset(races=[race("a", 1000, 0)], actual_bets=[
            ActualBet(D, "JRA", "x", "単勝", 1000, 2500),
            ActualBet(D, "JRA", "y", "単勝", 500, None, status="PENDING"),
            ActualBet(D, "SPAT4", "z", "馬連", 300, None, status="REFUND"),
        ])
        m = compute(ds, *week_bounds(D))
        a = m["actual_week"]["total"]
        self.assertEqual((a["stake"], a["payout"], a["pending_stake"], a["refunds"]), (1300, 2800, 500, 1))
        self.assertEqual(m["cohorts"][0]["week"]["stake"], 1000)

    def test_losing_streak(self):
        ds = Dataset(races=[race("1", 100, 500), race("2", 100, 0), race("3", 100, 0)])
        self.assertEqual(compute(ds, *week_bounds(D))["cohorts"][0]["losing_streak"], 2)

    def test_dedupe_same_race(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "races.csv").write_text(
                "date,market,system,race_id,status,stake,payout\n"
                "2026-10-27,Local,S,R1,SETTLED,100,0\n"
                "2026-10-27,Local,S,R1,SETTLED,100,900\n", encoding="utf-8")
            ds = load_sources([{"type": "normalized", "dir": str(d)}], d)
        self.assertEqual(len(ds.races), 1)
        self.assertEqual(ds.races[0].payout, 900)
        self.assertTrue(any("重複" in w for w in ds.warnings))


class CapitalTest(unittest.TestCase):
    def test_list_and_dict(self):
        a = parse_capital({"cohorts": [{"cohort": "JRA", "level": "L0", "decision": "HOLD", "confidence": "LOW"}]})
        b = parse_capital({"jra": {"recommended_level": "L1", "state": "ELIGIBLE"}, "meta": {"x": 1}})
        self.assertEqual((a[0].cohort, a[0].level), ("JRA", "L0"))
        self.assertEqual((b[0].cohort, b[0].level, b[0].decision), ("jra", "L1", "ELIGIBLE"))
        self.assertEqual(len(b), 1)


class LintTest(unittest.TestCase):
    def test_banned(self):
        self.assertTrue(find_violations("このAIなら必ず儲かる"))
        self.assertTrue(find_violations("必勝法"))
        self.assertEqual(find_violations("将来の的中や利益を約束するものではありません"), [])

    def test_rendered_outputs_are_clean(self):
        ds = Dataset(races=[race("a", 1000, 0, primary_failure="role"), race("b", 1000, 30000)])
        for name, text in render_all(compute(ds, *week_bounds(D)), {}).items():
            self.assertEqual(find_violations(text), [], name)

    def test_empty_dataset_renders(self):
        files = render_all(compute(Dataset(), *week_bounds(D)), {})
        self.assertIn("データがありませんでした", files["report_note.md"])
        json.loads(files["youtube_meta.json"])


class InspectTest(unittest.TestCase):
    def test_values_masked(self):
        sk = skeleton({"stake": 1200, "gate": "CORE", "races": [{"payout": 5000}, {"payout": 1}],
                       "long": "x" * 40})
        self.assertEqual(sk["stake"], "int")
        self.assertEqual(sk["gate"], "str:CORE")
        self.assertEqual(sk["races"], ["list[2]", {"payout": "int"}])
        self.assertEqual(sk["long"], "str(40)")


class EndToEndTest(unittest.TestCase):
    def test_build_sample(self):
        with tempfile.TemporaryDirectory() as t:
            rc = weekly_report.main(["build", "--config", str(HERE / "config.sample.json"),
                                     "--today", "2026-11-02", "--out", t])
            self.assertEqual(rc, 0)
            out = Path(t) / "2026-10-26"
            for name in ("report_note.md", "video_script.md", "shorts_script.md",
                         "x_post.txt", "youtube_meta.json", "summary.json"):
                self.assertTrue((out / name).is_file(), name)
            meta = json.loads((out / "youtube_meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["privacyStatus"], "private")
            self.assertLessEqual(len(meta["title"]), 100)
            note = (out / "report_note.md").read_text(encoding="utf-8")
            self.assertIn("ここから有料", note)
            self.assertIn("20歳", note)


if __name__ == "__main__":
    unittest.main()
