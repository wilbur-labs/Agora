/** The usage bar's request (workspace CR-87); the shapes and helpers are in usage-state. */
import { call } from "@/lib/terminal";
import type { UsageView } from "@/lib/usage-state";

export * from "@/lib/usage-state";

export const getUsage = () => call<UsageView>("/api/work/usage");
