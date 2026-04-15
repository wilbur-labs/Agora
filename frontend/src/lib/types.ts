export interface Agent {
  name: string;
  role: string;
  model?: string;
  profile?: string;
}

export interface ChatMessage {
  id: string;
  type: "user" | "agent" | "system" | "route" | "tool_call" | "tool_result";
  agent?: string;
  role?: string;
  content: string;
  streaming?: boolean;
  feedback?: "up" | "down";
  confirmed?: boolean;
  toolStatus?: "running" | "done" | "skipped" | "error";
}

export const AGENT_COLORS: Record<string, string> = {
  moderator: "text-amber-400",
  scout: "text-blue-400",
  architect: "text-violet-400",
  critic: "text-red-400",
  sentinel: "text-orange-400",
  synthesizer: "text-emerald-400",
  executor: "text-cyan-400",
};

export const AGENT_DOT_COLORS: Record<string, string> = {
  moderator: "bg-amber-400",
  scout: "bg-blue-400",
  architect: "bg-violet-400",
  critic: "bg-red-400",
  sentinel: "bg-orange-400",
  synthesizer: "bg-emerald-400",
  executor: "bg-cyan-400",
};

export const AGENT_BORDER_COLORS: Record<string, string> = {
  moderator: "border-l-amber-400",
  scout: "border-l-blue-400",
  architect: "border-l-violet-400",
  critic: "border-l-red-400",
  sentinel: "border-l-orange-400",
  synthesizer: "border-l-emerald-400",
  executor: "border-l-cyan-400",
};

export const ROUTE_LABELS: Record<string, string> = {
  QUICK: "⚡ Quick Answer",
  DISCUSS: "💬 Council Discussion",
  EXECUTE: "🔧 Direct Execution",
  CLARIFY: "❓ Needs Clarification",
};
