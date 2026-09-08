# Kurage 制度ナビ（kseido）

**困りごと → 使える地域制度（市・区・県・国）＋申請先・期限・必要書類＋出典リンク＋相談記録。**
通報先ナビ（[kecnavi](https://kurage.exbridge.jp/kecnavi.php/)）の制度版で、議員事務所・相談窓口が住民の相談を受けたその場で使い、自分で維持できる道具です。名古屋市版をデモとして同梱しています。

- デモ: https://kurage.exbridge.jp/kseido.php/
- 検索は Python の絞り込みだけで **LLM は使いません**。
- 制度データは `data/programs.json` 1本。他の自治体は `data/programs.json` と `data/wards.json` の差し替えです。

## 画面

| パス | 内容 |
|---|---|
| `/` | 困りごとタイル（出産・子育て／ひとり親／学費／医療費／障害／高齢の親／住まい／収入減／物価高／空き家／災害）、期限が近い制度、区役所一覧 |
| `/s/<困りごと>` | 制度一覧。区を選ぶと申請先が区役所の課・電話に置き換わる。属性（ひとり親・非課税・65歳以上…）で絞る。期限が近い順 |
| `/p/<制度id>` | 対象・内容・期限・申請先・必要書類・出典・公式更新日。GovernmentService JSON-LD |
| `/ku/<区>` | 区役所が窓口の制度一覧 |
| `/records` | 相談記録（トークン制・SQLite・CSV書き出し） |
| `/api/match`, `/api/programs`, `/sitemap.xml`, `/llms.txt` | API・SEO/AEO |

## データの作り方（事務所の人が自分で直せる）

`data/programs.json` の 1 制度は次の形です。公式ページの本文は転載せず、**事実（窓口・電話・期限・書類）とリンク**だけを入れます。

```json
{
  "id": "jido-teate", "name": "児童手当", "level": "国", "provider": "国（名古屋市が窓口）",
  "situations": ["kosodate"], "who_tags": ["kodomo18"],
  "who": "名古屋市に住み、18歳年度末までの子どもを育てている人",
  "benefit": "子ども1人ごとの月額手当。偶数月にまとめて振込",
  "deadline": {"type": "常時", "note": "申請の翌月分から。早めに"},
  "apply": {"ward_window": true, "ward_section": "民生子ども課（支所は区民福祉課）", "tel": "052-972-2522", "how": "窓口・郵送"},
  "documents": ["認定請求書", "振込口座が分かるもの"],
  "source_url": "https://www.city.nagoya.jp/kodomo/ninshin/1008971/1008973.html", "source_name": "名古屋市 児童手当", "source_asof": "2026年5月7日"
}
```

- `deadline.type` は `常時` / `期限`（`date` 必須） / `年度` / `終了`。`期限` は日付を過ぎると自動で「受付終了」になります。
- `apply.ward_window: true` の制度は、区を選ぶと `data/wards.json` の区役所住所・電話に置き換わります。区ごとに番号が違うときは `apply.ward_tel: {"chikusa": "052-…"}`。
- `situations` は困りごとタイルの id、`who_tags` は属性チップの id（どちらも `programs.json` 先頭で定義）。

## セットアップ

```bash
uv venv .venv && uv pip install --python .venv/bin/python fastapi uvicorn jinja2 requests
echo 'KSEIDO_ADMIN_TOKEN=<相談記録のトークン>' > .env      # 空なら相談記録は無効
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18385
```

常駐は `scripts/kseido.service`（systemd user unit・Restart=always）、公開は `php/kseido.php`（透過プロキシ）を `scripts/deploy_proxy.sh` で配置。

## 出典と免責（名古屋市版）

名古屋市公式サイトの各制度ページ、名古屋市社会福祉協議会、文部科学省・こども家庭庁・厚生労働省の公式ページ。区役所の所在地は名古屋市の施設カルテ（CC BY）。要件・金額・期限は変わります。各制度の出典で確認し、申請先に電話してから動いてください。

## ライセンス

MIT（ソースコード）。制度データの権利は各出典元にあります。
