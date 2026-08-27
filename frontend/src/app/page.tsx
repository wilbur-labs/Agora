"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// The Task Workspace is the default landing: opening Agora drops you straight
// into a task (project goal, where it stands, and the next step) rather than a
// marketing page. Client-side replace keeps it working under static export.
export default function HomePage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/workspace");
  }, [router]);
  return null;
}
