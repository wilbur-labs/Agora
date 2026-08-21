# Agora

[English](README.md) | [中文](README_zh.md) | **日本語**

**Agora は、複数のコーディング agent を一人で同時に監督するためのローカルファーストなコンソールです。**
プロジェクトにフォルダ、**開発** agent、そして**別の**レビュー agent を割り当てます。開発 agent が作り、レビュー agent がその成果物を独立してクロスレビューし、あなたが承認するまで何も先へ進みません。Agora は Codex、Claude Code、Kiro をまたいで、すべての Task・Run・意思決定を記録する唯一の権威です。

```text
プロジェクト（フォルダ + 開発 agent + レビュー agent）
   -> Task
   -> 開発 Run（隔離された git worktree）
   -> 別の agent による自動クロスレビュー
   -> あなたの承認
   -> Verified
```

要点は**トリアージ**です。各 agent を個別のターミナルで見張るのではなく、一人で複数の agent を見渡し、返信が要るもの・承認が要るもの・ブロックされているものを一目で把握します。

## できること

- **注目度優先の Portfolio。** Task は「要対応 / 進行中 / 完了」に分類され、最も重要なシグナル（回答すべき質問・与えるべき承認・ブロッカー）で色分けされるため、人間の関与が必要な作業が最初に浮かび上がります。
- **プロジェクトごとの 開発 + レビュー agent。** 各プロジェクトは既定の実装者と、それとは**別の**レビュー agent を固定します（例：Codex が開発、Claude がレビュー）。自己レビューは無意味なので、レビューは常に別の agent が行います。
- **自動クロスレビュー。** 開発 Run が成功すると、Agora はプロジェクトのレビュー agent による独立したレビュー Run を自動で発行し、開発成果物を渡し、その結果を承認項目としてあなたに提示します。承認すると Task は Verified へ進みます。レビューには実効性があり、具体的な指摘を返します——形式的な承認印ではありません。
- **隔離された成果物。** 各 agent のワークスペースはそれ自身の git リポジトリなので、Run の出力はまさにその Run が生成したファイルだけになります——UI に整然と表示され、周囲のリポジトリのノイズなしにレビューできます。
- **アプリ内の要件インタビュー。** agent がページ上で明確化のための質問をし、あなたの回答が spec になります——明確化は横のチャット窓で代行するものではなく、プロダクトの一工程です。
- **正直に伝える設定ページ。** 設定ページは各 runtime のインストール・バージョン・ログイン状態を（token を消費せずに）検出し、agent ごとに Claude の token 使用量を報告し、プロジェクトルートと実行上限を設定できます。
- **土台は永続的なコントロールプレーン。** runtime 間の Task・Gate 状態を書き換えられるのは Agora だけです。Approval は repo・ref・commit・artifact に結び付き、resume と retry は冪等で、古い権威情報に対して fail closed します。固定された runtime は暗黙に置換されず、欠けている場合はその作業をブロックします。

> Agora 0.5 の自律的な Scout / Architect / Critic / Synthesizer 討論 Council は廃止され、本プロダクトには含まれません。モデルの出力が自ら状態を進めることはありません。

## クイックスタート（Windows）

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

注目度優先のボードは `http://127.0.0.1:8000/portfolio`、認証付きの Task コンソールは `http://127.0.0.1:8000/control-plane` を開きます。Project に `agora`、bearer token に同じ秘密値を入力します。ここからプロジェクトを新規作成し（独自のフォルダと隔離された agent ワークスペースが与えられます）、その開発・レビュー agent を割り当て、Task を実行できます。

AI provider を一切呼び出さず、設定済みデータベースにも触れずに、正式なコントロールパスを検証します：

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

一時的な Git プロジェクトで決定論的なローカル子プロセスを起動し、すべての Stage と Gate を実行し、明示的な人間の承認を記録し、SQLite を開き直し、1 つの JSON レシートを出力してワークスペースを削除します。これはコントロールプレーンの受け入れであり、Codex/Claude/Kiro のネイティブ出力品質の証拠ではありません。

## ランタイム

Codex、Claude Code、Kiro は明示的に固定された CLI アダプターです。それらのサブスクリプションと可用性は外部条件であり、欠けている、またはログアウトした runtime はその作業をブロックし、暗黙に置換されません。検出とログイン状態は設定ページに表示されます。

## さらに詳しく

- [`AGENTS.md`](AGENTS.md) — アーキテクチャと規約
- [Agora 実用チュートリアル（中国語）](docs/usage/agora-1.0-tutorial.md) — インストール、最初の Task、承認、復旧、停止
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md) — コントロールプレーンのプロトコル
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md) — 実装状況

## ライセンス

MIT
