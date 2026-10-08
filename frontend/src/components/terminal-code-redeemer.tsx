"use client";

/**
 * Redeems the launcher's one-time `?terminal_code=` (CR-30) on whatever page it lands,
 * then takes it out of the address bar. Renders nothing.
 */
import { useEffect } from "react";

import { launchCodeOf, redeemLaunchCode } from "@/lib/terminal";

export function TerminalCodeRedeemer() {
  useEffect(() => {
    const { code, clean } = launchCodeOf(window.location.href);
    if (!code) return;
    window.history.replaceState(null, "", clean);
    void redeemLaunchCode(code).catch(() => undefined);
  }, []);
  return null;
}
