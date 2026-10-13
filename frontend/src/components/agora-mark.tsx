import { cn } from "@/lib/utils";

/**
 * The Agora mark (CR-55, V7c). Solid left stroke = this machine, dashed right
 * stroke = a remote one; both run into the orange point at the top (Agora /
 * the person), and the bar is the layer that joins them. Same drawing as
 * `app/icon.svg`, which is also the source of `favicon.ico`
 * (`scripts/build-favicon.mjs`) — change all three together.
 */
export function AgoraMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn("size-8 shrink-0", className)}>
      <rect x=".5" y=".5" width="31" height="31" rx="7" fill="#1f1e1b" />
      <path d="M13.8 10.6 7 26" stroke="#faf9f5" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M18.2 10.6 25 26" stroke="#faf9f5" strokeWidth="3.2" strokeDasharray="5 3.3 8.5 3" />
      <circle cx="16" cy="6.8" r="3.4" fill="#c96442" />
      <path d="M11.2 19.5h9.6" stroke="#faf9f5" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
