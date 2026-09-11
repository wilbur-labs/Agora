"use client";

// The trailing slot on a row that reports a capability rather than a number.
//
// A number only when a read produced one. Otherwise the short form of whichever
// sentence applies, with the long form on `title` — never a bare grey dash, which would
// be "cannot read", "do not know" and "does not exist" merged into one again. Every
// judgement is in lib/nav-capability.ts; this file only picks the sentence.
//
// Shared by the project nav (workbench left column) and the Today cards, so one fact
// reads the same on both surfaces. It moved here from project-nav.tsx when Today needed
// it; the wording keys stayed under `workspace.nav.*` because that is where the two
// locales already agree on them.

import { useI18n } from "@/lib/i18n";
import { countSayable, navCapabilityKey, type NavCapability } from "@/lib/nav-capability";
import { cn } from "@/lib/utils";

export function CapabilitySlot(
  { capability, className }: { capability: NavCapability; className?: string },
) {
  const { t } = useI18n();
  const key = navCapabilityKey(capability);

  if (countSayable(capability)) {
    const caveat = capability.caveat;
    return (
      <span
        className={cn(
          "shrink-0 text-[11px] tabular-nums",
          caveat ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground",
          className,
        )}
        title={
          caveat
            ? t(`workspace.nav.why.caveat.${caveat.kind}`, { caveat: caveat.count })
            : undefined
        }
      >
        {t(key, { value: capability.value, caveat: caveat?.count ?? 0 })}
      </span>
    );
  }

  // Not a count. The row still renders and stays clickable — an item whose read is
  // missing is not a tombstone, it is an item that can explain itself.
  const why =
    capability.kind === "unbuilt"
      ? t("workspace.nav.why.unbuiltWithMilestone", { milestone: capability.milestone })
      : capability.kind === "noReader"
        ? t(`workspace.nav.why.noReader.${capability.reason}`)
        : capability.kind === "unreadable"
          ? t(`workspace.nav.why.unreadable.${capability.reason}`)
          : capability.kind === "unreported"
            ? t("workspace.nav.why.unreported")
            : t("workspace.nav.why.none");

  return (
    <span
      className={cn("shrink-0 truncate text-[10px] italic text-muted-foreground/80", className)}
      title={why}
    >
      {t(key, {
        milestone: capability.kind === "unbuilt" ? capability.milestone : "",
      })}
    </span>
  );
}
