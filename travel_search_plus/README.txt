Travel Search +

目的:
旅行検索をまとめてポン。航空券・宿泊・観光・食事などの検索と、国内/海外の旅行ニュースを
1ページにまとめたシングルページツールです。

アーキテクチャ (v0.0.3〜):
  app/main.py           ← 統合エントリポイント。output/index.html を1本生成
  app/domestic_news.py  ← 国内旅行ニュース RSS取得・DB管理
  app/global_news.py    ← 海外旅行ニュース RSS取得・DB管理・翻訳
  shared/               ← 全設定JSON（config / themes / sources / tabs ほか）
  output/index.html     ← 生成された単一出力ファイル

タブ構成:
  🔍 検索行: 移動 / 宿泊 / セット / 比較 / 観光 / 食事 / サポート / 特集
  📰 ニュース行 (国内): すべて / 最新 / イベント / チケット / 観光スポット / 交通 / 天気 / 宿泊 / グルメ / お知らせ
  📰 ニュース行 (海外): すべて / 最新 / 観光スポット / 安全・入国情報 / 航空・交通 / ホテル・宿泊 / グルメ / 旅行情報 / お知らせ

使い方:
  start_travel_search_plus.bat をダブルクリック

依存ライブラリ:
  deep-translator  (海外ニュースの英語→日本語自動翻訳、任意)

GitHub Actions:
  1日4回 (0/6/12/18時 UTC) 自動更新
