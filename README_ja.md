# 🏛 Agora

[English](README.md) | [中文](README_zh.md) | **日本語**

**マルチパースペクティブAIカウンシル — 議論、設計、実行、進化。**

Agoraは**フルスタックAIエージェントプラットフォーム**です。複数のAIアドバイザーが異なる視点からアイデアを議論し、計画を実行し、すべてのインタラクションから学習します。

## 特徴

| | ChatGPT/Claude | Cursor/Windsurf | DeerFlow | **Agora** |
|---|---|---|---|---|
| マルチパースペクティブ議論 | ❌ | ❌ | ❌ | ✅ |
| フルスタック実行 | サンドボックス | エディタのみ | ✅ | ✅ |
| 自己学習 | ❌ | ❌ | 部分的 | ✅ |
| カスタムエージェント | ❌ | ❌ | ❌ | ✅ |
| スタンドアロンデプロイ | ❌ | ❌ | ✅ | ✅ |

## クイックスタート

### Docker（推奨）

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd agora
cp .env.example .env
# .envを編集 — APIキーを追加
docker compose up -d
# http://localhost:8000 を開く
```

### ローカル開発

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd agora
cp .env.example .env
make install
make dev      # APIサーバー http://localhost:8000
make dev-ui   # フロントエンド http://localhost:3000
```

## Web UI

- **チャット** (`/chat`) — マルチエージェントストリーミング議論、Markdownレンダリング
- **エージェント設定** (`/agents`) — エージェントの表示/編集/作成/テスト
- **スキル** (`/skills`) — 学習済みスキルの表示
- **設定** (`/settings`) — メモリとユーザープロファイル管理
- **セッション履歴** — 自動保存、サイドバーで切り替え
- **エクスポート/共有** — Markdownエクスポート + 共有リンク

## カウンシルエージェント

| エージェント | 役割 | タイミング |
|-------------|------|-----------|
| **Moderator** | リクエストルーティング | 常に最初 |
| **Scout** | リサーチ＆エビデンス | 議論フェーズ |
| **Architect** | 設計＆ソリューション | 議論フェーズ |
| **Critic** | レビュー＆チャレンジ | 議論フェーズ |
| **Sentinel** | セキュリティレビュー | オプション |
| **Synthesizer** | 結論の要約 | 議論終了時 |
| **Executor** | ツール実行 | 実行フェーズ |

## ワークフロー

```
ユーザー入力
  → Moderator ルーティング: QUICK / DISCUSS / EXECUTE
    → DISCUSS:
        Scout → Architect → Critic → Synthesizer
        → ユーザーがアクションアイテムを確認
        → Executor が実行
        → スキルを学習
    → EXECUTE:
        → Executor ツール呼び出しループ
        → スキルを学習
```

## ライセンス

MIT

## 謝辞

Agoraは以下の優れたオープンソースプロジェクトから多大なインスピレーションを受けました。心より感謝申し上げます：

- **[DeerFlow](https://github.com/bytedance/deer-flow)** — ByteDanceの長期タスクSuperAgentフレームワーク。サンドボックス実行、メモリシステム、エージェントオーケストレーションの重要な参考。
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — Nous Researchの自己進化AIエージェント。スキル学習ループのインスピレーション——自律的なスキル作成、使用中の自己改善、セッション間の永続メモリ。

## お問い合わせ

ご質問、ご提案、コラボレーションのご相談はお気軽にどうぞ：

- 📧 Email: `[your-email@example.com]`
- 🐛 Issues: [GitHub Issues](https://github.com/wilbur-labs/Agora/issues)
