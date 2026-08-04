# Agora

[English](README.md) | [中文](README_zh.md) | **日本語**

Agora は、Codex、Claude Code、Kiro を一つの権威あるワークフローで調整する、ローカルファーストのデリバリー・コントロールプレーンです。

```text
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
```

## 重要: 自律 AI Council は廃止されました

Agora 0.5 の Scout / Architect / Critic / Synthesizer による AI 間ディスカッションは Agora 1.0 の機能ではありません。既定の CLI、HTTP API、Web UI は自律的な討論を開始せず、モデルが作った「合意」を権威ある状態として保存しません。

`agora task consult` は AI 間の会話ではありません。ユーザーが明示的に開始し、現在の Stage に固定済みの単一 runtime だけを呼び出す、範囲限定の相談です。結果は人間が採用または却下するまで助言候補に留まります。

## 主な保証

- runtime 間の Task、Stage、Gate 状態を書き換えられるのは Agora だけです。
- Run はバージョン化された Context Pack を受け取り、Handoff Pack を返します。
- process、transport、schema、semantic result は別々に記録されます。
- Approval は repository、ref、commit、Stage、Artifact path と hash に結び付きます。
- resume と retry は冪等で、古い権威情報に対して fail closed します。

## 現在の入口

```powershell
cd backend
.\.venv\Scripts\agora.exe task --help

cd ..
make dev
```

認証済み Task コンソールは `http://localhost:8000/control-plane` です。
最新の実装状況は [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md) を参照してください。Agora 1.0 への移行は継続中です。

## License

MIT
