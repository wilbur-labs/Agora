// Landing page copy.

export const home = {
  en: {
    nav: {
      portfolio: "Portfolio",
    },
    hero: {
      eyebrow: "Supervise many coding agents",
      title: "A worker builds, a different agent reviews, you approve",
      subtitle:
        "Give a project a folder, a worker agent, and a different reviewer agent. Agora runs the worker, cross-reviews its work with the reviewer, and surfaces what needs you — a question, an approval, a blocker — on one attention-first board. Nothing advances on a model's say-so.",
      openControlPlane: "Open Control Plane",
      captureRequirements: "Capture requirements",
    },
    mainline: {
      title: "The durable pipeline underneath",
      steps: {
        project: "Project",
        task: "Task",
        stage: "Stage",
        run: "Run",
        artifact: "Artifact / Evidence",
        gate: "Gate",
        handoff: "Handoff / Done",
      },
    },
    guarantees: {
      state: {
        title: "Agora owns workflow state",
        text: "Native runtime output is evidence or a proposal. Only Agora may change cross-agent Task, Stage, and Gate state.",
      },
      evidence: {
        title: "A different agent reviews",
        text: "Every project pins a worker and a separate reviewer. A succeeded run is cross-reviewed automatically — self-review never counts.",
      },
      human: {
        title: "You approve what advances",
        text: "Approving a cross-review moves the task to verified. Adoption, rejection, approval, and escalation stay explicit human actions.",
      },
    },
    footer: "Local-first, inspectable, and resumable",
  },
  zh: {
    nav: {
      portfolio: "项目全景",
    },
    hero: {
      eyebrow: "监工多个 coding agent",
      title: "开发 agent 干活，另一个 agent 评审，你批准",
      subtitle:
        "给项目配一个文件夹、一个开发 agent 和一个不同的评审 agent。Agora 跑开发 agent、用评审 agent 交叉评审它的产出，并把需要你处理的事——待回答的提问、待批准的审批、受阻——集中到一块关注优先的看板上。没有什么会靠模型自说自话就推进。",
      openControlPlane: "打开控制平面",
      captureRequirements: "录入需求",
    },
    mainline: {
      title: "底层的持久流水线",
      steps: {
        project: "项目",
        task: "任务",
        stage: "阶段",
        run: "运行",
        artifact: "制品 / 证据",
        gate: "门禁",
        handoff: "交接 / 完成",
      },
    },
    guarantees: {
      state: {
        title: "工作流状态由 Agora 独占",
        text: "agent 的产出只是证据或提案。只有 Agora 能修改跨 agent 的任务、阶段与门禁状态。",
      },
      evidence: {
        title: "由另一个 agent 评审",
        text: "每个项目固定一个开发 agent 和一个独立的评审 agent。运行成功后自动交叉评审——自审从不算数。",
      },
      human: {
        title: "推进与否由你批准",
        text: "批准一次交叉评审，任务就推进到「已验证」。采纳、拒绝、审批与升级始终是明确的人为动作。",
      },
    },
    footer: "本地优先、可审查、可恢复",
  },
  ja: {
    nav: {
      portfolio: "プロジェクト一覧",
    },
    hero: {
      eyebrow: "複数のコーディングエージェントを監督",
      title: "開発エージェントが作り、別のエージェントがレビューし、あなたが承認する",
      subtitle:
        "プロジェクトにフォルダ、開発エージェント、それとは別のレビューエージェントを割り当てます。Agora は開発エージェントを実行し、その成果をレビューエージェントで相互レビューし、あなたの対応が必要なこと（回答待ちの質問、承認待ち、ブロック）を対応優先のボード1枚に集めます。モデルの自己申告だけで先へ進むものはありません。",
      openControlPlane: "コントロールプレーンを開く",
      captureRequirements: "要件を入力",
    },
    mainline: {
      title: "土台にある永続パイプライン",
      steps: {
        project: "プロジェクト",
        task: "タスク",
        stage: "ステージ",
        run: "実行",
        artifact: "成果物 / 証跡",
        gate: "ゲート",
        handoff: "引き継ぎ / 完了",
      },
    },
    guarantees: {
      state: {
        title: "ワークフローの状態は Agora だけが持つ",
        text: "エージェントの出力は証跡か提案にすぎません。エージェントをまたぐタスク・ステージ・ゲートの状態を変更できるのは Agora だけです。",
      },
      evidence: {
        title: "別のエージェントがレビュー",
        text: "各プロジェクトに開発エージェントと独立したレビューエージェントを1つずつ固定します。成功した実行は自動で相互レビューされ、セルフレビューは数に入りません。",
      },
      human: {
        title: "進めるかどうかはあなたが承認",
        text: "相互レビューを承認すると、タスクは「検証済み」に進みます。採用・却下・承認・エスカレーションは常に明示的な人の操作です。",
      },
    },
    footer: "ローカルファースト・検証可能・再開可能",
  },
} as const;
