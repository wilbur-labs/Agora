"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

const REPO = "wilbur-labs/Agora";

const FEATURES: Record<string, { icon: string; title: string; desc: string }[]> = {
  en: [
    { icon: "💬", title: "Multi-Perspective Discussion", desc: "Scout, Architect, Critic — each agent brings a unique viewpoint to your problem." },
    { icon: "🔧", title: "Full-Stack Execution", desc: "Agents don't just talk — they execute. File operations, shell commands, code generation." },
    { icon: "🧠", title: "Self-Learning", desc: "Every discussion and execution becomes a reusable skill. Agents improve over time." },
    { icon: "🐳", title: "Docker Sandbox", desc: "Dangerous commands run in isolated containers. Safe by default." },
    { icon: "⚙️", title: "Customizable Agents", desc: "Create, edit, and test agents with your own prompts. Full control over the council." },
    { icon: "🔌", title: "Model Agnostic", desc: "Azure OpenAI, OpenAI, Claude CLI, Gemini CLI, or any OpenAI-compatible API." },
  ],
  zh: [
    { icon: "💬", title: "多视角讨论", desc: "Scout、Architect、Critic——每个 Agent 从独特视角分析你的问题。" },
    { icon: "🔧", title: "全栈执行", desc: "Agent 不只是讨论——它们真正执行。文件操作、Shell 命令、代码生成。" },
    { icon: "🧠", title: "自我学习", desc: "每次讨论和执行都会变成可复用的技能。Agent 持续进化。" },
    { icon: "🐳", title: "Docker 沙箱", desc: "危险命令在隔离容器中运行。默认安全。" },
    { icon: "⚙️", title: "自定义 Agent", desc: "创建、编辑、测试你自己的 Agent。完全掌控议会。" },
    { icon: "🔌", title: "模型无关", desc: "Azure OpenAI、OpenAI、Claude CLI、Gemini CLI 或任何 OpenAI 兼容 API。" },
  ],
  ja: [
    { icon: "💬", title: "マルチパースペクティブ議論", desc: "Scout、Architect、Critic——各エージェントが独自の視点で問題を分析。" },
    { icon: "🔧", title: "フルスタック実行", desc: "エージェントは議論するだけでなく実行します。ファイル操作、シェルコマンド、コード生成。" },
    { icon: "🧠", title: "自己学習", desc: "すべての議論と実行が再利用可能なスキルに。エージェントは時間とともに進化。" },
    { icon: "🐳", title: "Docker サンドボックス", desc: "危険なコマンドは隔離コンテナで実行。デフォルトで安全。" },
    { icon: "⚙️", title: "カスタムエージェント", desc: "独自のプロンプトでエージェントを作成・編集・テスト。カウンシルを完全制御。" },
    { icon: "🔌", title: "モデル非依存", desc: "Azure OpenAI、OpenAI、Claude CLI、Gemini CLI、または任意のOpenAI互換API。" },
  ],
};

const I18N: Record<string, Record<string, string>> = {
  en: {
    hero: "Multiple AI perspectives. One shared context. Better decisions.",
    sub: "Agora is a multi-agent AI council where Scout, Architect, Critic, and more discuss your ideas — then execute the plan and learn from it.",
    try: "Try Agora →",
    star: "Star on GitHub",
    quickstart: "Quick Start",
    features: "Features",
  },
  zh: {
    hero: "多视角 AI 讨论。共享上下文。更好的决策。",
    sub: "Agora 是一个多 Agent AI 议会，Scout、Architect、Critic 等角色从不同视角讨论你的想法——然后执行计划并从中学习。",
    try: "开始使用 →",
    star: "Star on GitHub",
    quickstart: "快速开始",
    features: "功能特性",
  },
  ja: {
    hero: "複数のAI視点。共有コンテキスト。より良い意思決定。",
    sub: "Agoraは複数のAIエージェントが異なる視点からアイデアを議論し、計画を実行し、学習するマルチエージェントAIカウンシルです。",
    try: "試してみる →",
    star: "Star on GitHub",
    quickstart: "クイックスタート",
    features: "機能",
  },
};

export default function LandingPage() {
  const [lang, setLang] = useState("en");
  const [stars, setStars] = useState<number | null>(null);
  const [contributors, setContributors] = useState<{ login: string; avatar_url: string }[]>([]);
  const t = I18N[lang] ?? I18N.en;

  useEffect(() => {
    fetch(`https://api.github.com/repos/${REPO}`)
      .then((r) => r.json())
      .then((d) => { if (d.stargazers_count != null) setStars(d.stargazers_count); })
      .catch(() => {});
    fetch(`https://api.github.com/repos/${REPO}/contributors?per_page=20`)
      .then((r) => r.json())
      .then((d) => { if (Array.isArray(d)) setContributors(d); })
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <nav className="flex items-center justify-between px-6 py-4 max-w-5xl mx-auto">
        <div className="flex items-center gap-2">
          <span className="text-2xl">🏛</span>
          <span className="text-lg font-bold">Agora</span>
        </div>
        <div className="flex items-center gap-3">
          {(["en", "zh", "ja"] as const).map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              className={`text-xs px-2 py-1 rounded ${lang === l ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              {l === "en" ? "EN" : l === "zh" ? "中" : "日"}
            </button>
          ))}
          <a href={`https://github.com/${REPO}`} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm">
              {stars != null && stars >= 100 ? `⭐ ${stars >= 1000 ? (stars / 1000).toFixed(1) + "k" : stars}` : "⭐"} {t.star}
            </Button>
          </a>
        </div>
      </nav>

      {/* Hero */}
      <section className="text-center px-6 pt-16 pb-12 max-w-3xl mx-auto">
        <h1 className="text-4xl md:text-5xl font-bold tracking-tight leading-tight">{t.hero}</h1>
        <p className="mt-4 text-lg text-muted-foreground leading-relaxed">{t.sub}</p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <a href="/chat"><Button size="lg">{t.try}</Button></a>
        </div>
      </section>

      {/* Demo */}
      <section className="max-w-4xl mx-auto px-6 pb-16">
        <div className="rounded-xl border border-border bg-card p-6 text-sm font-mono leading-relaxed overflow-x-auto">
          <p className="text-muted-foreground">You: &quot;Design a caching strategy for my Go API, QPS ~5000&quot;</p>
          <p className="mt-3"><span className="text-blue-400 font-semibold">◆ scout</span> Redis vs Memcached vs local cache comparison...</p>
          <p className="mt-1"><span className="text-violet-400 font-semibold">◆ architect</span> Two-tier: ristretto (L1) + Redis (L2)...</p>
          <p className="mt-1"><span className="text-red-400 font-semibold">◆ critic</span> Cache consistency between L1 and L2 not addressed...</p>
          <p className="mt-1"><span className="text-emerald-400 font-semibold">◆ synthesizer</span> Action Items: ristretto + go-redis + CacheManager...</p>
          <p className="mt-3 text-muted-foreground">Execute? [y] →</p>
          <p className="mt-1"><span className="text-cyan-400 font-semibold">◆ executor</span> 🔧 shell(go get github.com/dgraph-io/ristretto) → ✅</p>
        </div>
      </section>

      {/* Architecture */}
      <section className="max-w-3xl mx-auto px-6 pb-16">
        <div className="rounded-xl border border-border bg-card p-6 text-sm font-mono text-center">
          <pre className="inline-block text-left text-xs leading-relaxed">{`User Input
  → Moderator (QUICK / DISCUSS / EXECUTE)
    → DISCUSS:
        Scout → Architect → Critic → Synthesizer
        → User confirms action items
        → Executor (tool-calling loop)
        → Learn skill
    → EXECUTE:
        → Executor (write_file, shell, ...)
        → Learn skill`}</pre>
        </div>
      </section>

      {/* Features */}
      <section className="max-w-5xl mx-auto px-6 pb-16">
        <h2 className="text-2xl font-bold text-center mb-8">{t.features}</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(FEATURES[lang] ?? FEATURES.en).map((f) => (
            <div key={f.title} className="rounded-xl border border-border bg-card p-5">
              <div className="text-2xl mb-2">{f.icon}</div>
              <h3 className="font-semibold mb-1">{f.title}</h3>
              <p className="text-sm text-muted-foreground">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Quick Start */}
      <section className="max-w-3xl mx-auto px-6 pb-16">
        <h2 className="text-2xl font-bold text-center mb-6">{t.quickstart}</h2>
        <div className="rounded-xl border border-border bg-card p-5 font-mono text-sm space-y-1">
          <p className="text-muted-foreground"># Docker (recommended)</p>
          <p>git clone https://github.com/{REPO}.git</p>
          <p>cd agora</p>
          <p>cp .env.example .env</p>
          <p>docker compose up -d</p>
          <p className="text-muted-foreground mt-2"># Open http://localhost:8000</p>
        </div>
      </section>

      {/* Contributors */}
      {contributors.length > 0 && (
        <section className="max-w-3xl mx-auto px-6 pb-16 text-center">
          <h2 className="text-lg font-semibold mb-4">Contributors</h2>
          <div className="flex flex-wrap justify-center gap-2">
            {contributors.map((c) => (
              <a key={c.login} href={`https://github.com/${c.login}`} target="_blank" rel="noopener noreferrer">
                <img src={c.avatar_url} alt={c.login} className="w-10 h-10 rounded-full border border-border hover:scale-110 transition-transform" />
              </a>
            ))}
          </div>
        </section>
      )}

      {/* Footer */}
      <footer className="border-t border-border py-8 text-center text-sm text-muted-foreground">
        <p>MIT License · <a href={`https://github.com/${REPO}`} className="underline">GitHub</a></p>
      </footer>
    </div>
  );
}
