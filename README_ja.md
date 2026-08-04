# Agora

[English](README.md) | [中文](README_zh.md) | **日本語**

Agora は、継続的な AI 支援作業のためのローカルファーストなデリバリー・コントロールプレーンです。Codex、Claude Code、Kiro を一つの権威あるワークフローで調整します。

```text
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
```

## 重要：自律 AI ディスカッションは廃止されました

Agora 0.5 の Scout / Architect / Critic / Synthesizer Council は Agora 1.0 の機能ではありません。CLI、HTTP API、Web UI は AI 同士の自由な議論を開始せず、モデルが生成した「合意」で Task、Stage、Gate を進めません。

`agora task consult` は、ユーザーが明示的に開始する一回の限定相談です。現在の Stage に固定された単一 runtime だけを呼び出し、結果は人間が adopt または reject するまで助言候補に留まります。

## 主な保証

- runtime 間の Task、Stage、Gate 状態を書き換えられるのは Agora だけです。
- runtime はバージョン付き Context Pack を受け取り、バージョン付き Handoff Pack を返します。
- process、transport、schema、semantic result を別々に記録します。
- Approval は repository、ref、commit、Stage、Artifact path、hash に結び付きます。
- Token 予算は受付と記録の境界であり、provider のハード上限とは扱いません。
- resume と retry は冪等で、古い権威情報に対して fail closed します。

## Windows クイックスタート

```powershell
cd backend
uv sync --locked --extra dev

cd ..\frontend
corepack pnpm install --frozen-lockfile
corepack pnpm build

cd ..\backend
$env:AGORA_CONTROL_PLANE_TOKEN = "十分に長いランダムな秘密値に置き換える"
uv run uvicorn agora.api.app:app --host 127.0.0.1 --port 8000
```

`http://127.0.0.1:8000/control-plane` を開き、Project に `agora`、bearer token に同じ秘密値を入力します。コンソールは権威ある Task projection を読み、個別レビュー済みの人間向け操作だけを表示します。

モデルを一切呼び出さない正式な受け入れテスト：

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

インストール、最初の Task、consult の adopt/reject、復旧、停止方法は [Agora 1.0 実用チュートリアル（中国語）](docs/usage/agora-1.0-tutorial.md) を参照してください。

## 現在の範囲

Agora 1.0 はレビュー済みのローカル・コントロールプレーン基盤です。Codex、Claude Code、Kiro のアカウントとサービス可用性は外部条件です。固定された runtime が利用できない場合、その Stage はブロックされ、別 runtime へ暗黙に置換されません。動的ロール、任意のローカルモデル・アダプター、runtime 置換は 1.0 以降の拡張です。既存の Kiro 設定と `.kiro/` データは保持されます。

アーキテクチャと進捗の情報源：

- [`AGENTS.md`](AGENTS.md)
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md)
- [`docs/requirements/latest-transformation-requirements.md`](docs/requirements/latest-transformation-requirements.md)
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md)

## ライセンス

MIT
