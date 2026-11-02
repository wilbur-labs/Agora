// Shared UI copy: navigation, shell chrome, and cross-page primitives.
// Protocol terms keep their English original in parentheses on first/section
// use so the domain stays legible to operators familiar with the API.

export const common = {
  en: {
    brand: {
      name: "Agora",
      tagline: "Delivery Control Plane",
    },
    nav: {
      today: "Today",
      work: "Work",
    projects: "Projects",
      workbench: "Workbench",
      environments: "Environments",
      skills: "Skills",
      settings: "Settings",
      // Kept for the pages that are still reachable by URL but no longer
      // presented as the product (see the note on `links` in delivery-shell):
      // each one still titles its own page.
      portfolio: "Portfolio",
      controlPlane: "Control Plane",
      requirements: "Requirements",
      runs: "Runs",
      workflows: "Workflows",
      attention: "Attention",
    },
    textSize: { label: "Text size", normal: "A", large: "A+", xlarge: "A++" },
    shell: {
      tabs: "Screens",
      // Runs execute on this machine until remote execution (W3-a) lands; the platform
      // name beside it comes from the same probe as the CLI versions.
      instance: {
        stable: "v{version} · stable",
        dev: "v{version} · DEVELOPMENT",
        unknown: "v{version} · release not identified",
        failed: "Agora version could not be read",
        stableTitle: "The stable install: this code is exactly a release tag. Upgrades happen only when you approve them.",
        devTitle: "A development checkout, not a release. It can change or break at any time; your daily Agora is the stable one.",
        unknownTitle: "This server did not say which installation it is. Treat it as not a release.",
      },
      environmentLocal: "This machine",
      waiting: "Waiting for you: {count}",
      waitingUnknown: "Waiting: unread",
      cliChip: "CLI",
      cliProblem: "CLI !",
      environmentTitle: "Every run executes on this machine. Choosing an environment comes with remote execution (W3-a).",
      versions: {
        pending: "probing the CLIs…",
        failed: "CLI versions could not be read",
        notFound: "not installed",
        uninspectable: "could not be probed",
        unknownVersion: "version unknown",
      },
    },
    action: {
      toggleTheme: "Toggle theme",
      toggleLanguage: "Switch language",
      refresh: "Refresh",
      retry: "Retry",
      dismiss: "Dismiss",
    },
    language: {
      en: "English",
      zh: "中文",
      ja: "日本語",
    },
    languageSync: {
      next: "Switch to {language}. Run logs and items written before a switch stay in the language they were written in.",
      saving: "Saving the language setting…",
      saveFailed: "The language setting could not be saved, so the display language was not changed. Press again to retry.",
      unread: "Agora's language setting could not be read. Whether this page and the records Agora writes next are in the same language is unknown.",
    },
    state: {
      loading: "Loading",
      empty: "Nothing to show yet.",
    },
    // The project progress bar (lib/progress-snapshot.ts). Shared, because the same
    // bar is the answer on the workspace strip and in the portfolio cockpit, and two
    // copies of these sentences would let the two surfaces disagree about what a
    // missing snapshot means. Every sentence names the read it came from: none of
    // them may be readable as "no progress has been made".
    progress: {
      label: "Progress",
      // The counted items behind the percentage, so the number is checkable.
      counts: "{done} done · {inProgress} in progress · {todo} to do",
      // The same, for a snapshot written in checkboxes. "Not done" rather than "to
      // do": that convention cannot say whether an unchecked item has been started,
      // so calling it "to do" would state something the file never did.
      countsDoneOnly: "{done} done · {notDone} not done",
      // Present and tracking nothing yet — not a missing snapshot, and not 0%.
      noItems: "The progress snapshot tracks no items yet.",
      // No snapshot at all. Says where one would be read from, so it reads as a
      // thing that can be created rather than as a failure.
      noSnapshot: "No progress snapshot for this project ({source}), so no progress figure is shown.",
      // Read fine, and it does track items — in markers this reader does not count.
      // The honest sentence names the reader, not the project: saying "no items" about
      // a file full of checkboxes would be false.
      markersNotCounted: "The progress snapshot ({source}) uses checklist markers this reader does not count, so no progress figure is shown.",
      // Both conventions in one file. Naming the fix is the point: counting either
      // half alone would report a floor as a percentage.
      mixedConventions: "The progress snapshot ({source}) mixes ✅/🔵/⬜ markers with [x] checkboxes, so counting either one alone would understate it. No progress figure is shown.",
      unreadable: "The progress snapshot could not be read, so progress is not known.",
      notReported: "This server does not report a progress snapshot, so progress is not known.",
      // An older backend, whose counts came from a rule that could quietly leave part
      // of a file out. Names the fix, because that is the only action available.
      conventionNotReported: "This server does not report which checklist convention it counted, so its progress figure cannot be shown to cover the whole snapshot. Update the backend.",
    },
  },
  zh: {
    brand: {
      name: "Agora",
      tagline: "交付控制平面",
    },
    nav: {
      today: "今天",
      work: "工作",
    projects: "项目",
      workbench: "项目工作台",
      environments: "环境",
      skills: "技能库",
      settings: "设置",
      portfolio: "项目全景",
      controlPlane: "控制平面",
      requirements: "需求",
      runs: "运行记录",
      workflows: "工作流",
      attention: "关注事项",
    },
    textSize: { label: "字号（标准 / 大 / 特大）", normal: "字 标准", large: "字 大", xlarge: "字 特大" },
    shell: {
      tabs: "屏幕",
      instance: {
        stable: "v{version} · 稳定版",
        dev: "v{version} · 开发版",
        unknown: "v{version} · 未标明版本",
        failed: "读不到 Agora 版本",
        stableTitle: "稳定版：代码恰好是一个发布 tag。只有你同意时才会升级。",
        devTitle: "开发中的检出，不是发布版本，随时可能改动或出错。你日常用的是稳定版。",
        unknownTitle: "这个服务没有说明自己是哪一份安装，按「不是发布版本」看待。",
      },
      environmentLocal: "本机",
      waiting: "等你处理 {count}",
      waitingUnknown: "等你处理：读不到",
      cliChip: "CLI",
      cliProblem: "CLI 有问题",
      environmentTitle: "Run 现在都在本机执行。选环境这一项随远端执行（W3-a）一起来。",
      versions: {
        pending: "正在探测 CLI…",
        failed: "CLI 版本读不到",
        notFound: "未安装",
        uninspectable: "无法探测",
        unknownVersion: "版本未知",
      },
    },
    action: {
      toggleTheme: "切换主题",
      toggleLanguage: "切换语言",
      refresh: "刷新",
      retry: "重试",
      dismiss: "关闭",
    },
    language: {
      en: "English",
      zh: "中文",
      ja: "日本語",
    },
    languageSync: {
      next: "切换到{language}。切换之前写下的运行记录和待处理事项，保持写下时的语言。",
      saving: "正在保存语言设置…",
      saveFailed: "语言设置没能保存，所以显示语言没有改变。再点一次重试。",
      unread: "读不到 Agora 的语言设置。这个页面的语言和 Agora 接下来写的记录是否一致，不知道。",
    },
    state: {
      loading: "加载中",
      empty: "暂无内容。",
    },
    progress: {
      label: "进度",
      counts: "已完成 {done} · 进行中 {inProgress} · 待办 {todo}",
      countsDoneOnly: "已完成 {done} · 未完成 {notDone}",
      noItems: "进度快照中还没有登记任何条目。",
      noSnapshot: "这个项目没有进度快照（{source}），因此不显示任何进度数字。",
      markersNotCounted: "进度快照（{source}）使用的清单标记不在本读取器的统计范围内,因此不显示任何进度数字。",
      mixedConventions: "进度快照（{source}）同时混用了 ✅/🔵/⬜ 标记与 [x] 复选框，只按其中一种统计会少算，因此不显示任何进度数字。",
      unreadable: "进度快照无法读取,因此进度未知。",
      notReported: "这个服务端不提供进度快照,因此进度未知。",
      conventionNotReported: "这个服务端没有说明它按哪种清单约定统计，无法确认它的进度数字覆盖了整份快照。请升级后端。",
    },
  },
  ja: {
    brand: {
      name: "Agora",
      tagline: "デリバリー・コントロールプレーン",
    },
    nav: {
      today: "今日",
      work: "作業",
      projects: "プロジェクト",
      workbench: "ワークベンチ",
      environments: "環境",
      skills: "スキル",
      settings: "設定",
      portfolio: "プロジェクト一覧",
      controlPlane: "コントロールプレーン",
      requirements: "要件",
      runs: "実行履歴",
      workflows: "ワークフロー",
      attention: "要対応",
    },
    textSize: { label: "文字サイズ（標準 / 大 / 特大）", normal: "字 標準", large: "字 大", xlarge: "字 特大" },
    shell: {
      tabs: "画面",
      instance: {
        stable: "v{version} · 安定版",
        dev: "v{version} · 開発版",
        unknown: "v{version} · リリース不明",
        failed: "Agora のバージョンを読めません",
        stableTitle: "安定版：コードはリリースタグそのものです。アップグレードはあなたが承認したときだけ行われます。",
        devTitle: "開発中のチェックアウトで、リリースではありません。いつ変わったり壊れたりしてもおかしくありません。普段使う Agora は安定版です。",
        unknownTitle: "このサーバーはどのインストールなのかを示していません。「リリースではない」ものとして扱ってください。",
      },
      environmentLocal: "このマシン",
      waiting: "あなたの対応待ち {count}",
      waitingUnknown: "対応待ち：読めません",
      cliChip: "CLI",
      cliProblem: "CLI に問題",
      environmentTitle: "実行はすべてこのマシンで行われます。環境の選択はリモート実行（W3-a）とあわせて提供されます。",
      versions: {
        pending: "CLI を確認中…",
        failed: "CLI のバージョンを読めません",
        notFound: "未インストール",
        uninspectable: "確認できません",
        unknownVersion: "バージョン不明",
      },
    },
    action: {
      toggleTheme: "テーマを切り替え",
      toggleLanguage: "言語を切り替え",
      refresh: "更新",
      retry: "再試行",
      dismiss: "閉じる",
    },
    language: {
      en: "English",
      zh: "中文",
      ja: "日本語",
    },
    languageSync: {
      next: "{language}に切り替えます。切り替え前に書かれた実行ログと対応項目は、書かれた言語のままです。",
      saving: "言語設定を保存しています…",
      saveFailed: "言語設定を保存できなかったため、表示言語は変えていません。もう一度押すと再試行します。",
      unread: "Agora の言語設定を読めませんでした。この画面の言語と、Agora がこれから書く記録の言語が一致しているかは不明です。",
    },
    state: {
      loading: "読み込み中",
      empty: "まだ表示する内容はありません。",
    },
    progress: {
      label: "進捗",
      counts: "完了 {done} · 進行中 {inProgress} · 未着手 {todo}",
      countsDoneOnly: "完了 {done} · 未完了 {notDone}",
      noItems: "進捗スナップショットにはまだ項目が登録されていません。",
      noSnapshot: "このプロジェクトには進捗スナップショットがない（{source}）ため、進捗の数字は表示しません。",
      markersNotCounted: "進捗スナップショット（{source}）は、この読み取り処理が数えないチェックリスト記号を使っているため、進捗の数字は表示しません。",
      mixedConventions: "進捗スナップショット（{source}）は ✅/🔵/⬜ の記号と [x] のチェックボックスを混在させています。どちらか一方だけで数えると少なく出るため、進捗の数字は表示しません。",
      unreadable: "進捗スナップショットを読めないため、進捗は不明です。",
      notReported: "このサーバーは進捗スナップショットを提供していないため、進捗は不明です。",
      conventionNotReported: "このサーバーはどのチェックリスト形式で数えたかを示していないため、進捗の数字がスナップショット全体を対象にしているか確認できません。バックエンドを更新してください。",
    },
  },
} as const;
