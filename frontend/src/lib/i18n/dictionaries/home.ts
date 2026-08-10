// Landing page copy.

export const home = {
  en: {
    nav: {
      portfolio: "Portfolio",
    },
    hero: {
      eyebrow: "Durable AI-assisted delivery",
      title: "One authoritative workflow from Task to done",
      subtitle:
        "Agora coordinates Codex, Claude Code, and Kiro through versioned Stages, bounded Context and Handoff Packs, truthful budgets, independent review, and evidence-backed Gates. It does not run an autonomous AI debate.",
      openControlPlane: "Open Control Plane",
      captureRequirements: "Capture requirements",
    },
    mainline: {
      title: "Authoritative product mainline",
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
        text: "Native runtime output is evidence or a proposal. Only Agora may change cross-runtime Task, Stage, and Gate state.",
      },
      evidence: {
        title: "Evidence-bound quality gates",
        text: "Process exit, transport, schema validity, and semantic results stay separate. Exit code zero is never enough.",
      },
      human: {
        title: "Explicit human decisions",
        text: "Consultation produces one advisory candidate. Adoption, rejection, approval, and escalation remain explicit actions.",
      },
    },
    footer: "Local-first, inspectable, and resumable",
  },
  zh: {
    nav: {
      portfolio: "项目全景",
    },
    hero: {
      eyebrow: "持久的 AI 辅助交付",
      title: "从任务到完成，只有一条权威工作流",
      subtitle:
        "Agora 通过版本化的阶段（Stage）、有界的上下文包与交接包（Context / Handoff Pack）、真实的预算、独立评审，以及以证据为准的门禁（Gate），协调 Codex、Claude Code 与 Kiro。它不会启动 AI 之间的自由辩论。",
      openControlPlane: "打开控制平面",
      captureRequirements: "录入需求",
    },
    mainline: {
      title: "权威产品主线",
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
        text: "运行时（runtime）的产出只是证据或提案。只有 Agora 能修改跨运行时的任务、阶段与门禁状态。",
      },
      evidence: {
        title: "以证据为准的质量门禁",
        text: "进程退出、传输、schema 合法性与语义结果分开记录。退出码为 0 从来都不够。",
      },
      human: {
        title: "由人做出的明确决策",
        text: "一次咨询只产生一个建议性候选。采纳、拒绝、审批与升级始终是明确的人为动作。",
      },
    },
    footer: "本地优先、可审查、可恢复",
  },
} as const;
