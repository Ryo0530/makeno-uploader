# 週次レポート・動画台本の自動生成（負けの研究室）

ケイバ進化録の検証データを読み込んで、毎週次のファイルを作ります。

| ファイル | 使いみち |
| --- | --- |
| `report_note.md` | noteの記事。無料部分と有料部分の区切り（`<!-- ここから有料 -->`）入り |
| `video_script.md` | YouTube本編の台本（5〜7分、テロップ指定つき） |
| `shorts_script.md` | ショート動画の台本（30〜45秒） |
| `x_post.txt` | Xの投稿文 |
| `youtube_meta.json` | タイトル・概要欄・タグ。makeno-uploaderに渡す想定（**公開設定は必ず private**） |
| `summary.json` | 集計結果の全データ（機械用） |

外部ライブラリもAPIキーも不要です（Python 3.10以上）。

## 守っているルール

- **仮想購入と実馬券は別々に集計し、合算しない。** JRAとLocal、CONTROLとSHADOWなどのcohortも混ぜない
- 回収率は精算済みのレースだけで計算。同着・返還・取消などの**保留は損にも得にも数えず**、未精算額として別に出す
- 投資0のときの回収率は「—」。読めなかった値は推測で埋めず「—」にして、レポート末尾の「データ注記」に理由を書く
- 「最大の当たり1本を除いた回収率」も並べて出す（1本の大当たりで良く見えるのを防ぐ）
- 「必ず儲かる」「必勝」などの表現が混ざったら、**何も書き出さずに止まる**（`wr/lint.py`）
- すべての出力に注意書き（個人の検証記録であること、20歳未満の購入禁止）を入れる

## はじめての設定（PCで1回だけ）

### 1. データの形を確認する

実際の `report.json` の形は、こちらからはまだ確認できていません。仕様書に出てくる項目名と表記ゆれに合わせて読むようにしてありますが、まず構造を確認してください。

```bat
cd tools\weekly_report
python weekly_report.py inspect "C:\Users\PC\Documents\Codex\2026-09-07\referenced-chatgpt-conversation-this-is-an-10\outputs\Local_Prospective\runs"
python weekly_report.py inspect "C:\Users\PC\Documents\Codex\2026-09-07\referenced-chatgpt-conversation-this-is-an-16\outputs\Capital_Level\reports"
```

金額などの値は伏せて、キー名と型だけが出ます（区分名などの短い文字列だけは表示されます）。
この出力をClaudeに貼れば、読み込み部分（`wr/adapters.py`）を実データに合わせて直せます。

### 2. config.json を作る

`config.example.json` を `config.json` という名前でコピーして、`root` を自分のPCのパスに書き換えます。

- `local_set01`：`Local_Prospective` フォルダ（中に `runs\prospective\<日付>\evaluation\report.json` がある）
- `capital`：`Capital_Level` フォルダ（中に `reports\latest.json` がある）
- `normalized`：自分で用意するCSVのフォルダ（下記）。無ければ行ごと消してOK
- `start_date`：累計の起点。`note_url`：noteのURL（入れると台本・概要欄・Xに載る）

### 3. 実馬券の記録（任意）

`my_data\actual_bets.csv` を置くと、実馬券の収支も集計します。

```csv
date,source,race_id,bet_type,stake,payout,status
2026-10-27,楽天競馬,20261027-園田-09R,三連複,1500,4820,SETTLED
2026-11-01,JRA,20261101-東京-11R,馬連,1000,,PENDING
```

`status` は `SETTLED`（確定）／`PENDING`（未確定）／`REFUND`（返還）。payoutが空の確定は未確定として扱います。

仮想成績を別のシステムから出したい場合は、同じフォルダに `races.csv` を置けば読めます。
列は `date,market,system,race_id,status,gate,venue,stake,payout,trifecta_payout_per100,primary_failure,missed_roles` です（`missed_roles` は `HEAD;THIRD` のように `;` 区切り）。

### 4. 動かしてみる

```bat
python weekly_report.py build --config config.json
python weekly_report.py build --config config.json --week 2026-10-26
```

`weekly_out\<週の月曜日>\` に出力されます。`--week` を省くと先週（月〜日）の分になります。

### 5. 毎週自動で回す

`run_weekly.cmd` をタスクスケジューラに登録します（例：毎週月曜 7:40）。

```bat
schtasks /Create /TN "MakenoLab_WeeklyReport" /SC WEEKLY /D MON /ST 07:40 /TR "\"%CD%\run_weekly.cmd\""
```

ログは `logs\weekly_report.log` に残ります。既存の競馬タスク（FREEZE・evening・Capital）には一切触りません。読み込むだけで、元のファイルは書き換えません。

## サンプルで試す

`sample_data/` は**動作確認用の架空データ**です（実績ではありません）。

```bash
python weekly_report.py build --config config.sample.json --today 2026-11-02
python -m unittest discover -s tests
```

## まだやっていないこと

- 実データの `report.json` に合わせた確認（上の「1.」の結果待ち）
- JRA Set04の読み込み（`Set04_*` の出力の形がわかれば追加します）
- 動画ファイルの生成と、makeno-uploaderからの自動アップロードとの接続
- note・Xへの自動投稿（どちらも、最初は手で確認してから出す運用をおすすめします）
