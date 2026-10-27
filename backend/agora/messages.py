"""Backend-authored display text, in the reader's language (CR-71 slice 2).

Every sentence the backend writes **for a person to read** — a run-log note, an
attention item's title and body, a session state's reason, a refusal — lives in
``MESSAGES`` as ``{"zh": ..., "ja": ..., "en": ...}`` and is produced by
:func:`say`, in the language :func:`get_display_language` names at that moment.

**The rule this module exists to keep: text that can reach an agent's input never
goes through say().** A prompt, anything appended to a run's semantic output (that
output is the "developer's own summary" a review prompt embeds), and anything a
client folds into a prompt (an approval item's *body* is the reviewer's verdict the
fix prompt quotes) is fixed-language. A reader switching the UI to 日本語 must not
change what an agent is told — nor the length of it, which moves truncation
budgets. So the kiro truncation note in ``orchestration/provider_usage.py``, the
review prompt, kickoff prompts and the approval body's fallback stay as they are.
Before routing a new string through ``say()``, check that it cannot flow into a
prompt; if it can, it does not belong here.

**When text is rendered** — two lifetimes, stated because they differ:

* Run logs and attention items are stored **as written**: they are rendered once,
  at write time, in the language current then. Switching later does not rewrite
  them.
* Session-state reasons are stored with a ``reason_code`` + ``reason_args`` beside
  the text and rendered **at read time** (``execution/sessions.py``); rows written
  before the code existed show their stored text as written.

**The language value.** One process-wide value, default ``zh``, set from the
``project_settings`` key ``display_language`` once in the app's lifespan and by the
``PUT /api/settings/display-language`` endpoint after it has persisted. ``say()``
never reads the database itself: a lazy read would make every test that renders a
message open whatever database the process resolves — the developer's own, which
is an incident this codebase has already had (``tests/conftest.py``).

The ``zh`` entry of every key is byte-identical to the Chinese text it replaced, so
records and tests written before this module read the same.
"""
from __future__ import annotations

from string import Formatter
from typing import Any

#: The languages a reader can choose. Anything else is refused, not mapped.
LANGUAGES: tuple[str, ...] = ("zh", "ja", "en")
DEFAULT_LANGUAGE = "zh"
#: The ``project_settings`` key the choice is persisted under.
SETTING_DISPLAY_LANGUAGE = "display_language"

_language: str = DEFAULT_LANGUAGE


def get_display_language() -> str:
    return _language


def set_display_language(lang: str) -> None:
    """Switch the language every later ``say()`` renders in. Refuses an unknown one."""
    global _language
    if lang not in LANGUAGES:
        raise ValueError(f"unknown display language: {lang!r} (expected one of {', '.join(LANGUAGES)})")
    _language = lang


def say(key: str, **values: Any) -> str:
    """The message ``key`` in the current display language, with ``values`` filled in."""
    return MESSAGES[key][_language].format(**values)


def say_in(lang: str, key: str, **values: Any) -> str:
    """The message ``key`` in ``lang``, whatever the current language is."""
    return MESSAGES[key][lang].format(**values)


def variants(key: str, **values: Any) -> tuple[str, ...]:
    """The message in every language — for a lookup that must treat the same message
    written under different settings as one (an idempotency key that includes text)."""
    return tuple(say_in(lang, key, **values) for lang in LANGUAGES)


def render_code(code: str | None, args: dict[str, Any] | None, fallback: str | None) -> str | None:
    """A stored ``(code, args)`` rendered now, or ``fallback`` (the text as stored)
    when there is no code or it no longer renders — an unknown key or a missing
    argument shows what was written rather than nothing."""
    if not code:
        return fallback
    try:
        return say(code, **(args or {}))
    except (KeyError, IndexError, ValueError, TypeError):
        return fallback


def placeholders(template: str) -> set[str]:
    """The ``{field}`` names (with any ``!r`` conversion) a template uses."""
    found: set[str] = set()
    for _, field, _, conversion in Formatter().parse(template):
        if field is not None:
            found.add(field + (f"!{conversion}" if conversion else ""))
    return found


MESSAGES: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------ C. run-log notes
    # Appended to a run's transcript (a display surface only; the review prompt reads
    # ``stdout_tail``, never the run log).
    "run.job_object_failed": {
        "zh": (
            "· 这一轮没能把子进程收进进程组（Windows job object 接管失败）。"
            "如果 agent 起了后台进程，它可能在这一轮结束之后还在写工作目录——"
            "交付物快照与磁盘内容届时可能对不上。\n"
        ),
        "ja": (
            "· このターンでは子プロセスをプロセスグループに入れられませんでした（Windows job object の引き継ぎに失敗）。"
            "agent がバックグラウンドプロセスを起動していた場合、ターン終了後も作業ディレクトリに書き込み続けている可能性があります。"
            "その場合、成果物のスナップショットとディスクの内容が一致しないことがあります。\n"
        ),
        "en": (
            "· This turn could not put its child processes into a process group (taking over the Windows job object failed). "
            "If the agent started a background process, it may still be writing to the working directory after this turn ends, "
            "and the deliverables snapshot may then disagree with what is on disk.\n"
        ),
    },
    "run.drain_timeout_local": {
        "zh": (
            "\n· 这一轮的输出没能读完：进程已经结束，但还有东西占着它的管道"
            "（等了 {seconds} 秒）。上面显示的是已经读到的部分。\n"
        ),
        "ja": (
            "\n· このターンの出力を最後まで読めませんでした：プロセスは終了しましたが、まだ何かがパイプを保持しています"
            "（{seconds} 秒待ちました）。上に表示されているのは読み取れた部分です。\n"
        ),
        "en": (
            "\n· This turn's output could not be read to the end: the process has exited, but something is still holding its pipe "
            "(waited {seconds} seconds). What is shown above is the part that was read.\n"
        ),
    },
    "run.drain_timeout_remote": {
        "zh": (
            "\n· 这一轮的输出没能读完：远端进程已经结束，但通道上还有东西没读到"
            "（等了 {seconds} 秒）。上面显示的是已经读到的部分。\n"
        ),
        "ja": (
            "\n· このターンの出力を最後まで読めませんでした：リモートのプロセスは終了しましたが、チャネルにまだ読み取れていないものがあります"
            "（{seconds} 秒待ちました）。上に表示されているのは読み取れた部分です。\n"
        ),
        "en": (
            "\n· This turn's output could not be read to the end: the remote process has exited, but the channel still has unread data "
            "(waited {seconds} seconds). What is shown above is the part that was read.\n"
        ),
    },
    "run.remote_where": {
        "zh": "· 在远端 {target} 的工作树 {workspace} 里执行。\n",
        "ja": "· リモート {target} の作業ツリー {workspace} で実行します。\n",
        "en": "· Running on the remote {target}, in the worktree {workspace}.\n",
    },
    "run.merge_gap": {
        "zh": "\n· 中间的输出没有保留：这一轮打印的内容超过了缓冲上限，上下两段之间缺了一截。\n",
        "ja": "\n· 途中の出力は保存されていません：このターンの出力がバッファの上限を超えたため、前半と後半の間が欠けています。\n",
        "en": "\n· Output in the middle was not kept: this turn printed more than the buffer holds, so a stretch is missing between the two parts.\n",
    },
    "run.review_prompt_echo": {
        "zh": "（Agora 自动拼的复核请求，{chars} 字；内容是上面那一轮改动的文件）",
        "ja": "（Agora が自動で組み立てたレビュー依頼、{chars} 文字。内容は上のターンで変更されたファイルです）",
        "en": "(Review request assembled by Agora, {chars} characters; it holds the files the turn above changed)",
    },
    "runlog.truncated": {
        "zh": (
            "\n· 这一轮的记录到达了大小上限，后面的输出没有被保存下来。"
            "run 本身不受影响；结论与改动文件仍在它自己的记录里。\n"
        ),
        "ja": (
            "\n· このターンの記録がサイズ上限に達したため、これ以降の出力は保存されていません。"
            "run 自体には影響ありません。結論と変更ファイルは run 自身の記録に残っています。\n"
        ),
        "en": (
            "\n· This turn's record reached its size limit; later output was not saved. "
            "The run itself is unaffected; its conclusion and changed files are still in its own record.\n"
        ),
    },
    "transcript.list_separator": {
        "zh": "、",
        "ja": "、",
        "en": ", ",
    },
    "transcript.unread_kinds": {
        "zh": "· 本轮有 Agora 未能解读的事件类型：{kinds}（已如实记下，未当作没发生）",
        "ja": "· このターンには Agora が解釈できなかったイベント種別があります：{kinds}（そのまま記録しています。起きなかったものとしては扱っていません）",
        "en": "· This turn had event types Agora could not interpret: {kinds} (recorded as they were, not treated as if they did not happen)",
    },
    "transcript.vendor_truncated": {
        "zh": "· 供应商声明它自己的最终文本被截断了，上面显示的不是全文",
        "ja": "· プロバイダーが自身の最終テキストは切り詰められたと報告しています。上の表示は全文ではありません",
        "en": "· The provider declared its own final text truncated; what is shown above is not the whole text",
    },
    "transcript.unread_event": {
        "zh": "· 未解读的事件：{kind}",
        "ja": "· 解釈できなかったイベント：{kind}",
        "en": "· Event not interpreted: {kind}",
    },
    "transcript.thought_tokens": {
        "zh": "· 想了约 {tokens} tokens",
        "ja": "· 約 {tokens} tokens 考えました",
        "en": "· Thought for about {tokens} tokens",
    },
    "transcript.plan_update": {
        "zh": "· 收到计划更新",
        "ja": "· 計画の更新を受け取りました",
        "en": "· Plan update received",
    },
    "transcript.clipped": {
        "zh": "{head}… (截断，共 {length} 字)",
        "ja": "{head}… (切り詰め、全 {length} 文字)",
        "en": "{head}… (truncated, {length} characters in all)",
    },
    "transcript.write_lines": {
        "zh": "{tool}({subject}) {lines} 行",
        "ja": "{tool}({subject}) {lines} 行",
        "en": "{tool}({subject}) {lines} lines",
    },
    # --------------------------------------------------------- D. attention items
    # Titles and blocker bodies. NOT the approval body: that is the reviewer's verdict,
    # which the fix prompt quotes (``frontend/src/lib/auto-advance.ts``), so its
    # fallback stays fixed-language in ``dispatcher._raise_review_attention``.
    "review.owed_blocker_title": {
        "zh": "跨评审没有留下结论,需要你处理",
        "ja": "クロスレビューの結論が残っていません。対応が必要です",
        "en": "The cross-review left no conclusion; this needs you",
    },
    "review.owed_after_restart": {
        "zh": (
            "Agora 在这次实现运行结束之后、独立交叉评审被创建之前停止了(进程退出或崩溃),"
            "因此评审运行从未入队,也没有留下任何记录。"
        ),
        "ja": (
            "この実装 run が終わった後、独立クロスレビューが作られる前に Agora が停止しました（プロセスの終了またはクラッシュ）。"
            "そのためレビュー run はキューに入っておらず、記録も何も残っていません。"
        ),
        "en": (
            "Agora stopped after this implementation run ended and before the independent cross-review was created (the process exited or crashed), "
            "so the review run was never queued and left no record."
        ),
    },
    "review.owed_without_gate": {
        "zh": (
            "这次实现运行的评审运行已经结束,但没有留下任何需要你处置的条目(批准项和阻塞项"
            "都没有写成功),因此没有人被要求接受这份工作。请查看那次评审运行本身的状态与"
            "输出:它可能在真正评审之前就失败了,也可能结论已经写出来但没有保存下来。"
        ),
        "ja": (
            "この実装 run のレビュー run は終了しましたが、対応を求める項目が何も残っていません（承認項目もブロッカーも書き込めませんでした）。"
            "そのため、この作業の受け入れを誰も求められていません。そのレビュー run 自体の状態と出力を確認してください。"
            "実際にレビューする前に失敗したのかもしれませんし、結論は出たものの保存されなかったのかもしれません。"
        ),
        "en": (
            "The review run for this implementation run has ended, but it left no item for you to act on (neither the approval nor the blocker "
            "was written), so nobody has been asked to accept this work. Check that review run's own state and output: it may have failed "
            "before it actually reviewed anything, or its conclusion may have been written and not saved."
        ),
    },
    "review.owed_cancelled": {
        "zh": (
            "这次实现运行的独立交叉评审运行已被取消(可能是你手动取消的,也可能是 Agora 在评审"
            "真正开始之前退出、由停机清理自动取消的),因此这次评审没有留下任何结论 —— 它可能"
            "还没开始,也可能在中途被停下。"
        ),
        "ja": (
            "この実装 run の独立クロスレビュー run はキャンセルされました（手動でキャンセルした可能性も、レビューが実際に始まる前に "
            "Agora が終了し、停止時の後始末で自動的にキャンセルされた可能性もあります）。そのため、このレビューの結論は何も残っていません。"
            "まだ始まっていなかったのかもしれませんし、途中で止められたのかもしれません。"
        ),
        "en": (
            "The independent cross-review run for this implementation run was cancelled (perhaps by you, or automatically by shutdown cleanup "
            "when Agora exited before the review really started), so this review left no conclusion. It may not have started yet, or it may "
            "have been stopped partway."
        ),
    },
    "review.owed_blocker_body": {
        "zh": (
            "这次实现运行已完成,但没有任何独立交叉评审的结论被记录下来,因此没有人"
            "被要求接受这份工作。具体是哪一种情况,见原因:\n"
            "{reason}\n\n"
            "本条打开期间会挡住这个任务的新实现运行,所以两个出口都从处置本条"
            "(回复或取消)开始:\n"
            "1) 处置本条,不再补评审:这份工作就以“没有任何评审结论”的状态留下,"
            "任务随后可以接受新的实现运行。选这个出口,就是在没有评审结论的情况下"
            "接受这份工作。\n"
            "2) 先处理上面的原因(例如配置或登录评审运行时),处置本条,然后重新派发"
            "一次实现运行;它成功后 Agora 会自动为*那次*运行派发交叉评审。那次评审"
            "读到的不是完整的工作区,而是一份有上限的采样:只包含新运行结束时工作区里"
            "相对 HEAD 有改动的文件,每个文件最多前 {file_chars} 个字符,"
            "所有文件合计约 {budget} 个字符(超出后其余文件整个略去),"
            "最多 {max_files} 个文件;读不出来的文件会被跳过,git 给不出答案时按"
            "“没有改动”处理。所以上面这次运行的产出,只有仍留在工作区里*并且*落在"
            "这份采样之内的部分会被看到 —— 一个 2000 字符、之后没再改动的文件,只有"
            "前 {file_chars} 个字符会被看到;排在合计上限之外的文件完全不会"
            "被看到。\n"
            "Agora 没有单独派发评审运行的接口(评审只会跟在一次成功的实现运行之后),"
            "因此“再派发一次评审”不是一个出口。"
        ),
        "ja": (
            "この実装 run は完了しましたが、独立クロスレビューの結論が何も記録されていないため、この作業の受け入れを誰も求められていません。"
            "どの状況にあたるかは、次の理由を見てください：\n"
            "{reason}\n\n"
            "この項目が開いている間、このタスクの新しい実装 run は止められます。そのため、どちらの出口もこの項目への対応"
            "（返信またはキャンセル）から始まります：\n"
            "1) この項目に対応し、レビューは補わない：この作業は「レビューの結論が何もない」状態のまま残り、"
            "タスクはその後新しい実装 run を受け付けます。この出口を選ぶことは、レビューの結論がないままこの作業を受け入れることです。\n"
            "2) 先に上の理由を解決し（例：レビュー用ランタイムの設定やログイン）、この項目に対応してから、実装 run をもう一度ディスパッチする："
            "それが成功すると、Agora は*その* run に対してクロスレビューを自動でディスパッチします。そのレビューが読むのはワークスペース全体ではなく、"
            "上限付きのサンプルです：新しい run の終了時にワークスペース内で HEAD から変更のあるファイルだけを、"
            "1 ファイルにつき先頭 {file_chars} 文字まで、全ファイル合計で約 {budget} 文字まで（超えた分のファイルは丸ごと省かれます）、"
            "最大 {max_files} ファイルまで含みます。読めないファイルは飛ばされ、git が答えを返せない場合は「変更なし」として扱われます。"
            "したがって上の run の成果のうち、ワークスペースに残っていて*かつ*このサンプルに収まる部分だけがレビューされます。"
            "たとえば 2000 文字でその後変更されていないファイルは、先頭 {file_chars} 文字だけが読まれます。合計上限より後ろのファイルはまったく読まれません。\n"
            "Agora にはレビュー run を単独でディスパッチする手段がありません（レビューは成功した実装 run の後にしか行われません）。"
            "そのため「レビューをもう一度ディスパッチする」は出口ではありません。"
        ),
        "en": (
            "This implementation run has completed, but no conclusion from an independent cross-review was recorded, so nobody has been "
            "asked to accept this work. Which case this is, see the reason:\n"
            "{reason}\n\n"
            "While this item is open it blocks new implementation runs on this task, so both exits start by settling this item "
            "(reply or cancel):\n"
            "1) Settle this item and add no review: the work stays as it is, with no review conclusion, and the task can then take a new "
            "implementation run. Choosing this exit means accepting this work without a review conclusion.\n"
            "2) Fix the reason above first (for example, configure or log in to the review runtime), settle this item, then dispatch an "
            "implementation run again; when it succeeds Agora dispatches a cross-review for *that* run automatically. That review does not "
            "read the whole workspace but a bounded sample: only the files changed against HEAD in the workspace when the new run ends, "
            "at most the first {file_chars} characters of each, about {budget} characters across all files (files past that are left out "
            "whole), and at most {max_files} files; a file that cannot be read is skipped, and when git gives no answer it is treated as "
            "\"no changes\". So of the run above, only what is still in the workspace *and* falls inside that sample is seen: a "
            "2000-character file that was not changed again has only its first {file_chars} characters seen, and files past the overall "
            "limit are not seen at all.\n"
            "Agora has no way to dispatch a review run on its own (a review only follows a successful implementation run), "
            "so \"dispatch the review again\" is not an exit."
        ),
    },
    "review.project_missing": {
        "zh": "项目 {project_id} 已不在配置中,无法确定评审代理。",
        "ja": "プロジェクト {project_id} が設定にもうないため、レビュー担当の agent を決められません。",
        "en": "Project {project_id} is no longer configured, so the reviewing agent cannot be determined.",
    },
    "review.reviewer_unavailable": {
        "zh": "评审运行时 {reviewer!r} 未配置或已停用(可用:{available})。",
        "ja": "レビュー用ランタイム {reviewer!r} が設定されていないか、無効になっています（利用可能：{available}）。",
        "en": "Review runtime {reviewer!r} is not configured or is disabled (available: {available}).",
    },
    "review.no_runtime_available": {
        "zh": "无",
        "ja": "なし",
        "en": "none",
    },
    "review.environment_mismatch": {
        "zh": "评审无法派到开发所在的注册环境：{error}",
        "ja": "開発が行われた登録環境にレビューをディスパッチできません：{error}",
        "en": "The review cannot be dispatched to the registered environment the development ran in: {error}",
    },
    "review.remote_worktree_failed": {
        "zh": "评审代理 {reviewer!r} 在注册环境 {environment_id!r} 上的工作树未能建立：{error}",
        "ja": "レビュー担当 {reviewer!r} の作業ツリーを登録環境 {environment_id!r} に作れませんでした：{error}",
        "en": "The worktree for reviewer {reviewer!r} on registered environment {environment_id!r} could not be created: {error}",
    },
    "review.host_mismatch": {
        "zh": (
            "评审代理与开发代理不在同一台主机上：开发 {worker!r} 在 "
            "{worker_target}，评审 {reviewer!r} 在 "
            "{reviewer_target}。复核要读开发的工作树，两者必须配置在同一台主机。"
        ),
        "ja": (
            "レビュー担当と開発担当が同じホストにいません：開発 {worker!r} は {worker_target}、"
            "レビュー {reviewer!r} は {reviewer_target} です。レビューは開発の作業ツリーを読むため、両方を同じホストに設定する必要があります。"
        ),
        "en": (
            "The reviewer and the developer are not on the same host: developer {worker!r} is on {worker_target}, "
            "reviewer {reviewer!r} is on {reviewer_target}. The review reads the developer's worktree, so both must be configured on the same host."
        ),
    },
    "review.local_worktree_failed": {
        "zh": "评审代理 {reviewer!r} 在本机的工作树未能建立：{error}",
        "ja": "レビュー担当 {reviewer!r} の作業ツリーをこのマシンに作れませんでした：{error}",
        "en": "The local worktree for reviewer {reviewer!r} could not be created: {error}",
    },
    "review.enqueue_failed": {
        "zh": "评审运行未能入队:{error_type}: {error}",
        "ja": "レビュー run をキューに入れられませんでした：{error_type}: {error}",
        "en": "The review run could not be queued: {error_type}: {error}",
    },
    "review.approval_title": {
        "zh": "评审完成,待你批准(评审:{reviewer})",
        "ja": "レビュー完了、承認待ち（レビュー：{reviewer}）",
        "en": "Review done, awaiting your approval (reviewer: {reviewer})",
    },
    "review.approval_title_stood_in": {
        "zh": "评审完成,待你批准(评审:{reviewer},顶替 {original}:{why})",
        "ja": "レビュー完了、承認待ち（レビュー：{reviewer}、{original} の代わり：{why}）",
        "en": "Review done, awaiting your approval (reviewer: {reviewer}, standing in for {original}: {why})",
    },
    "review.failed_title": {
        "zh": "评审运行未完成(评审:{reviewer})",
        "ja": "レビュー run が完了しませんでした（レビュー：{reviewer}）",
        "en": "The review run did not complete (reviewer: {reviewer})",
    },
    "review.failed_body": {
        "zh": (
            "{reviewer} 的复核没有完成:{reason}。\n"
            "这次实现还没有任何复核结论。回复或取消本条之后任务就能继续,"
            "但那样这次实现就是没有经过复核被接受的。复核的原始输出在对话里这张复核卡上。"
        ),
        "ja": (
            "{reviewer} のレビューは完了しませんでした：{reason}。\n"
            "この実装にはまだレビューの結論がありません。この項目に返信するかキャンセルすればタスクは先に進めますが、"
            "その場合この実装はレビューを経ずに受け入れられたことになります。レビューの生の出力は、会話内のこのレビューカードにあります。"
        ),
        "en": (
            "{reviewer}'s review did not complete: {reason}.\n"
            "This implementation has no review conclusion yet. Replying to or cancelling this item lets the task continue, "
            "but then this implementation is accepted without a review. The review's raw output is on its review card in the conversation."
        ),
    },
    "review.usage_limit": {
        "zh": "{adapter} 额度用完",
        "ja": "{adapter} の利用枠を使い切りました",
        "en": "{adapter} is out of quota",
    },
    "review.usage_limit_retry_at": {
        "zh": "（它说 {when} 之后可以再用）",
        "ja": "（{when} 以降にまた使えるとのことです）",
        "en": " (it says it can be used again after {when})",
    },
    "review.worktree_missing": {
        "zh": "{adapter} 的工作目录没有建好，运行没能开始（原始错误：{reason}）",
        "ja": "{adapter} の作業ディレクトリが用意されておらず、run を開始できませんでした（元のエラー：{reason}）",
        "en": "{adapter}'s working directory was not set up, so the run could not start (original error: {reason})",
    },
    "review.no_error_detail": {
        "zh": "（没有错误详情）",
        "ja": "（エラーの詳細はありません）",
        "en": "(no error details)",
    },
    # ------------------------------------------------------ E. session-state reasons
    # Stored as a code + args on the session row and rendered at read time; also the
    # refusals ``AgentSessionStore.decide`` composes per turn.
    "session.unsupported_app_server": {
        "zh": (
            "codex app-server 桥不参与会话续接：它自己在进程内持有对话，"
            "Agora 没有实测过它的 resume 路径。这条链路上每一轮都是独立调用。"
        ),
        "ja": (
            "codex app-server ブリッジはセッションの継続に参加しません：会話をプロセス内で自分で保持しており、"
            "Agora はその resume 経路を実際に確かめていません。この経路では毎ターンが独立した呼び出しです。"
        ),
        "en": (
            "The codex app-server bridge does not take part in session continuation: it holds the conversation in its own process, "
            "and Agora has not verified its resume path. On this link every turn is an independent call."
        ),
    },
    "session.orphan_refusal": {
        "zh": (
            "这个任务在 {adapter} 上有一段旧会话（{session_id}），"
            "它记录于 role 进入主键之前，**无法判定它属于你的工作还是某次评审**——"
            "所以既不能当作你的会话续接（可能给你评审的上下文），"
            "也不能当作不存在（那会静默新开一段，agent 可能重做或覆盖它已完成的工作）。"
            "请你决定：**确认丢弃它，就显式新开一个会话**（对话窗口上的按钮）。"
            "把它认领成某一方的会话目前还做不到——那需要一个把 role 定下来的动作，"
            "Agora 还没有提供（CR-47）。在那之前，继续这段旧上下文的唯一办法是手工改库，"
            "所以这里说的是实话，而不是一个你按不到的选项。"
        ),
        "ja": (
            "このタスクには {adapter} 上に古いセッション（{session_id}）があります。"
            "role が主キーに入る前に記録されたもので、**あなたの作業のものか、あるレビューのものかを判定できません**。"
            "そのため、あなたのセッションとして継続することも（レビューの文脈を渡してしまう可能性があります）、"
            "存在しないものとして扱うこともできません（黙って新しいセッションが始まり、agent が完了済みの作業をやり直したり上書きしたりする可能性があります）。"
            "判断してください：**破棄してよければ、新しいセッションを明示的に開始してください**（会話ウィンドウのボタン）。"
            "どちらかのセッションとして引き取ることは今はできません。それには role を確定させる操作が必要ですが、"
            "Agora はまだ提供していません（CR-47）。それまでは、この古い文脈を続ける唯一の方法はデータベースを手で直すことです。"
            "押せないボタンを示すのではなく、事実をそのまま書いています。"
        ),
        "en": (
            "This task has an old session on {adapter} ({session_id}). "
            "It was recorded before role became part of the key, **so whether it belongs to your work or to a review cannot be determined**. "
            "It therefore cannot be continued as your session (it might hand you a review's context), "
            "nor treated as nonexistent (that would silently start a new one, and the agent might redo or overwrite work it already finished). "
            "Please decide: **if you accept discarding it, start a new session explicitly** (the button in the conversation window). "
            "Claiming it for one side is not possible yet: that needs an action that settles the role, "
            "which Agora does not provide (CR-47). Until then, the only way to continue this old context is to edit the database by hand, "
            "so this states the fact rather than offering an option you cannot press."
        ),
    },
    "session.lost_default": {
        "zh": "上一次续接这个会话失败，且供应商没有报出会话 id。",
        "ja": "前回このセッションの継続に失敗し、プロバイダーはセッション id を報告しませんでした。",
        "en": "The last attempt to continue this session failed, and the provider did not report a session id.",
    },
    "session.lost_refusal_suffix": {
        "zh": (
            " 这个判断是上一次真实观测的结果，不会自己变回来。"
            "解决了那次失败的原因（比如重新登录）之后，点「再试一次续接」，"
            "下一条消息就会按原来的会话 id 再试；确实要另起一段，请显式新开会话。"
        ),
        "ja": (
            " この判断は前回実際に観測した結果で、自然に元へ戻ることはありません。"
            "失敗の原因（たとえば再ログイン）を解決したら「継続を再試行」を押してください。"
            "次のメッセージで元のセッション id をもう一度試します。本当に別の会話を始める場合は、新しいセッションを明示的に開始してください。"
        ),
        "en": (
            " This judgement is what was actually observed last time, and it will not reset on its own. "
            "Once you have fixed the cause of that failure (for example, by logging in again), press \"Retry continuing\" "
            "and the next message will try the original session id again; if you really want a fresh start, start a new session explicitly."
        ),
    },
    "session.wrong_workspace": {
        "zh": (
            "这个任务的会话记录在另一个工作目录里"
            "（会话在 {recorded}，本次要用 {target}）。"
            "三家 CLI 的会话都按工作目录分域，换了目录就读不到原会话。"
            "要继续这段上下文，请用原来的工作目录；确实要另起一段，请显式新开会话。"
        ),
        "ja": (
            "このタスクのセッションは別の作業ディレクトリに記録されています"
            "（セッションは {recorded}、今回使うのは {target}）。"
            "3 つの CLI はどれもセッションを作業ディレクトリごとに分けているため、ディレクトリが変わると元のセッションを読めません。"
            "この文脈を続けるには元の作業ディレクトリを使ってください。本当に別の会話を始める場合は、新しいセッションを明示的に開始してください。"
        ),
        "en": (
            "This task's session is recorded under a different working directory "
            "(the session is in {recorded}; this turn would use {target}). "
            "All three CLIs scope sessions by working directory, so after a change of directory the original session cannot be read. "
            "To continue this context, use the original working directory; if you really want a fresh start, start a new session explicitly."
        ),
    },
    "session.no_resume_command": {
        "zh": (
            "适配器 {adapter} 没有配置续接命令，无法继续已记录的会话 "
            "{session_id}。这不是「没有会话」——上下文还在，"
            "只是这个配置读不到它。"
        ),
        "ja": (
            "アダプター {adapter} には継続用のコマンドが設定されていないため、記録済みのセッション "
            "{session_id} を続けられません。「セッションがない」のではありません。文脈は残っていますが、"
            "この設定からは読めません。"
        ),
        "en": (
            "Adapter {adapter} has no resume command configured, so the recorded session "
            "{session_id} cannot be continued. This is not \"no session\": the context is still there, "
            "this configuration just cannot read it."
        ),
    },
    "session.forked": {
        "zh": (
            "续接时供应商报出的会话 id 与请求的不是同一个"
            "（请求 {previous}，实际 {observed}）。"
            "这一轮的工作在新会话里，之前那段上下文没有被带过来。"
        ),
        "ja": (
            "継続時にプロバイダーが報告したセッション id が、要求したものと違いました"
            "（要求 {previous}、実際 {observed}）。"
            "このターンの作業は新しいセッションにあり、それまでの文脈は引き継がれていません。"
        ),
        "en": (
            "When continuing, the provider reported a different session id from the one requested "
            "(requested {previous}, actual {observed}). "
            "This turn's work is in the new session; the earlier context was not carried over."
        ),
    },
    "session.retry_requested": {
        "zh": (
            "你要求再试一次续接：下一条消息会按原来的会话 id 再试。"
            "如果它又没能接上，这里会重新变回「续接失败」。"
        ),
        "ja": (
            "継続の再試行が要求されました：次のメッセージで元のセッション id をもう一度試します。"
            "またつながらなかった場合、ここは再び「継続に失敗」に戻ります。"
        ),
        "en": (
            "You asked to retry continuing: the next message will try the original session id again. "
            "If it fails to connect again, this will go back to \"continuation failed\"."
        ),
    },
    "session.two_ids_settled": {
        "zh": (
            "这一轮的输出里出现了两个不同的会话 id（先 {first}，后 {second}）。"
            "工作很可能落在后一个里，而记录指向前一个——继续续接任何一个都可能"
            "接不上这一轮、甚至重做或覆盖它写过的文件。所以这里停下来，"
            "要继续请显式新开一个会话。"
        ),
        "ja": (
            "このターンの出力に 2 つの異なるセッション id が現れました（先に {first}、後に {second}）。"
            "作業はおそらく後者にあり、記録は前者を指しています。どちらを継続しても"
            "このターンにつながらず、書き込んだファイルをやり直したり上書きしたりする可能性があります。そのためここで止めています。"
            "続けるには新しいセッションを明示的に開始してください。"
        ),
        "en": (
            "This turn's output named two different session ids (first {first}, then {second}). "
            "The work most likely landed in the second while the record points at the first; continuing either one may "
            "not connect to this turn, or may even redo or overwrite the files it wrote. So this stops here; "
            "to continue, start a new session explicitly."
        ),
    },
    "session.two_ids_streaming": {
        "zh": (
            "这一轮的输出里出现了两个不同的会话 id（先 {first}，后 {second}）。"
            "工作很可能落在后一个里——继续续接任何一个都可能接不上这一轮，"
            "甚至重做或覆盖它写过的文件。要继续请显式新开一个会话。"
        ),
        "ja": (
            "このターンの出力に 2 つの異なるセッション id が現れました（先に {first}、後に {second}）。"
            "作業はおそらく後者にあります。どちらを継続してもこのターンにつながらず、"
            "書き込んだファイルをやり直したり上書きしたりする可能性があります。続けるには新しいセッションを明示的に開始してください。"
        ),
        "en": (
            "This turn's output named two different session ids (first {first}, then {second}). "
            "The work most likely landed in the second; continuing either one may not connect to this turn, "
            "or may even redo or overwrite the files it wrote. To continue, start a new session explicitly."
        ),
    },
    "session.unidentified": {
        "zh": (
            "这一轮跑过了，但它的输出里读不到会话 id"
            "（退出码 {exit_code}）。可能供应商那边确实有一个会话，"
            "只是 Agora 叫不出它的名字——所以下一条消息会开一段新的对话，"
            "这一轮的上下文不会被带过去。"
        ),
        "ja": (
            "このターンは実行されましたが、出力からセッション id を読み取れませんでした"
            "（終了コード {exit_code}）。プロバイダー側にはセッションが実際にあるのかもしれませんが、"
            "Agora にはその名前がわかりません。そのため次のメッセージは新しい会話を始め、"
            "このターンの文脈は引き継がれません。"
        ),
        "en": (
            "This turn ran, but no session id could be read from its output "
            "(exit code {exit_code}). The provider may well have a session; "
            "Agora just cannot name it, so the next message will start a new conversation "
            "and this turn's context will not be carried over."
        ),
    },
    "session.resume_unconfirmed": {
        "zh": (
            "这一轮续接跑完了（退出码 {exit_code}），"
            "但输出里始终没有出现会话 id——所以没法确认它接上的是同一段会话。"
            "可能是 CLI 换了事件格式（Agora 读不出来），"
            "也可能是它忽略了 resume、在另一段会话里干了这一轮的活。"
            "下一条消息仍会按记录里的 id 续接；如果你发现上下文接不上，请显式新开一个会话。"
        ),
        "ja": (
            "このターンの継続は最後まで実行されましたが（終了コード {exit_code}）、"
            "出力に一度もセッション id が現れませんでした。そのため同じ会話につながったかどうか確認できません。"
            "CLI のイベント形式が変わった（Agora が読めない）のかもしれませんし、"
            "resume を無視して別の会話でこのターンの作業をしたのかもしれません。"
            "次のメッセージも記録上の id で継続します。文脈がつながっていないと気づいたら、新しいセッションを明示的に開始してください。"
        ),
        "en": (
            "This resumed turn ran to the end (exit code {exit_code}), "
            "but no session id ever appeared in its output, so it cannot be confirmed that it continued the same session. "
            "The CLI may have changed its event format (which Agora cannot read), "
            "or it may have ignored the resume and done this turn's work in another session. "
            "The next message will still continue the recorded id; if you find the context does not connect, start a new session explicitly."
        ),
    },
    "session.resume_lost": {
        "zh": (
            "上一次续接会话失败（退出码 {exit_code}），"
            "而且这一轮里供应商始终没有报出会话 id —— 说明它没能接上这段会话。"
            "上下文可能还在，但这次没读到。"
        ),
        "ja": (
            "前回のセッション継続は失敗しました（終了コード {exit_code}）。"
            "このターンの間、プロバイダーはセッション id を一度も報告しませんでした。このセッションにつながらなかったということです。"
            "文脈は残っているかもしれませんが、今回は読めませんでした。"
        ),
        "en": (
            "The last attempt to continue the session failed (exit code {exit_code}), "
            "and the provider never reported a session id during this turn, which means it could not connect to this session. "
            "The context may still be there, but it was not read this time."
        ),
    },
    "session.clear_while_running": {
        "zh": (
            "这个适配器还有一轮在跑，现在清除会话不会生效——那个进程报出的下一条会话事件会把它写回来。"
            "先等它结束或停止它，再新开会话。"
        ),
        "ja": (
            "このアダプターではまだ 1 ターンが実行中のため、今セッションをクリアしても効きません。そのプロセスが次に報告するセッションイベントで書き戻されます。"
            "終わるのを待つか止めてから、新しいセッションを開始してください。"
        ),
        "en": (
            "A turn is still running on this adapter, so clearing the session now would not take effect: the next session event that process reports would write it back. "
            "Wait for it to finish or stop it, then start a new session."
        ),
    },
    # ------------------------------------------------------ F. request-time refusals
    "remote.tool_dir_not_absolute": {
        "zh": "claude-workspace 工具目录必须是绝对路径且不含 ..：{tool_dir}",
        "ja": "claude-workspace のツールディレクトリは .. を含まない絶対パスである必要があります：{tool_dir}",
        "en": "The claude-workspace tool directory must be an absolute path without ..: {tool_dir}",
    },
    "remote.no_such_dir": {
        "zh": "那台机器上没有这个目录：{tool_dir}",
        "ja": "そのマシンにこのディレクトリはありません：{tool_dir}",
        "en": "That machine has no such directory: {tool_dir}",
    },
    "remote.not_a_tool_dir": {
        "zh": "{tool} 不像 claude-workspace 工具目录（缺 {part}）",
        "ja": "{tool} は claude-workspace のツールディレクトリではないようです（{part} がありません）",
        "en": "{tool} does not look like a claude-workspace tool directory (missing {part})",
    },
    "remote.workroot_not_absolute": {
        "zh": "{tool}/.workroot 不是绝对路径：{marker}",
        "ja": "{tool}/.workroot が絶対パスではありません：{marker}",
        "en": "{tool}/.workroot is not an absolute path: {marker}",
    },
    "remote.workroot_missing": {
        "zh": ".workroot 指向的工作根不存在：{marker}",
        "ja": ".workroot が指す作業ルートが存在しません：{marker}",
        "en": "The work root .workroot points at does not exist: {marker}",
    },
    "remote.no_tasks_dir": {
        "zh": "工作根里没有 tasks/：{work}",
        "ja": "作業ルートに tasks/ がありません：{work}",
        "en": "The work root has no tasks/: {work}",
    },
    "remote.project_root_missing": {
        "zh": "这个环境的项目目录在那台机器上不存在：{project_root}",
        "ja": "この環境のプロジェクトディレクトリがそのマシン上に存在しません：{project_root}",
        "en": "This environment's project directory does not exist on that machine: {project_root}",
    },
    "remote.tool_dir_unregistered": {
        "zh": "这个环境没登记 claude-workspace 工具目录",
        "ja": "この環境には claude-workspace のツールディレクトリが登録されていません",
        "en": "This environment has no claude-workspace tool directory registered",
    },
    "remote.scan_timeout": {
        "zh": "读远端任务记录超过 {seconds} 秒",
        "ja": "リモートのタスク記録の読み取りが {seconds} 秒を超えました",
        "en": "Reading the remote task records took longer than {seconds} seconds",
    },
    "remote.scan_unreadable": {
        "zh": "读不到远端的任务记录：{error_type}",
        "ja": "リモートのタスク記録を読めません：{error_type}",
        "en": "Could not read the remote task records: {error_type}",
    },
    "remote.bad_task_name": {
        "zh": "任务名不合法：{task!r}",
        "ja": "タスク名が不正です：{task!r}",
        "en": "Invalid task name: {task!r}",
    },
    "remote.no_such_task": {
        "zh": "那台机器上没有这个任务：{task}",
        "ja": "そのマシンにこのタスクはありません：{task}",
        "en": "That machine has no such task: {task}",
    },
    "remote.task_yaml_unreadable": {
        "zh": "读不了 {task} 的 task.yaml：{error}",
        "ja": "{task} の task.yaml を読めません：{error}",
        "en": "Could not read {task}'s task.yaml: {error}",
    },
    "remote.task_archived": {
        "zh": "{task} 已归档",
        "ja": "{task} はアーカイブ済みです",
        "en": "{task} is archived",
    },
    "remote.task_root_unreadable": {
        "zh": "{task} 记录里的仓库路径在那台机器上读不了，确认不了它是不是这个项目",
        "ja": "{task} の記録にあるリポジトリのパスをそのマシンで読めないため、このプロジェクトのものか確認できません",
        "en": "The repository paths in {task}'s record cannot be read on that machine, so whether it is this project cannot be confirmed",
    },
    "remote.task_not_this_project": {
        "zh": "{task} 的记录没有指向这个环境的项目目录（{project_root}）",
        "ja": "{task} の記録はこの環境のプロジェクトディレクトリ（{project_root}）を指していません",
        "en": "{task}'s record does not point at this environment's project directory ({project_root})",
    },
    "connection.tool_unreadable": {
        "zh": "读不了：{error_type}",
        "ja": "読めません：{error_type}",
        "en": "Could not read: {error_type}",
    },
    "terminal.cd_failed": {
        "zh": "进不了项目目录：{root}",
        "ja": "プロジェクトディレクトリに入れません：{root}",
        "en": "Cannot enter the project directory: {root}",
    },
}
