import type { Metadata } from "next";
import { ThemeProvider } from "@/components/theme-provider";
import { TerminalCodeRedeemer } from "@/components/terminal-code-redeemer";
import { LocaleProvider } from "@/lib/i18n";
// CR-55: fonts ship with the build (no network at runtime). Latin from Inter, CJK
// from Noto Sans SC — kana included, so Japanese text on a page uses SC's glyph
// forms too; one consistent face per screen is deliberate. Noto Sans JP is only
// reached for characters SC lacks. Each is split by unicode-range, so a page
// downloads only the slices it actually uses.
import "@fontsource-variable/inter";
import "@fontsource-variable/noto-sans-sc";
import "@fontsource-variable/noto-sans-jp";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agora - Task Delivery Control Plane",
  description: "Durable Project, Task, Stage, Run, Evidence, and Gate coordination.",
};

// Runs before first paint. Resolves the stored/browser locale and stashes it on
// a global so the LocaleProvider's first client render is already correct. The
// guard style keeps content invisible (background still shows — no white flash)
// until the provider marks the tree ready, so users never see the English
// static HTML flip to Chinese, even on a full page reload. A timeout reveals
// the page as a safety net if hydration never runs.
const localeBootstrap = `(function(){try{var z={normal:1,large:1.125,xlarge:1.25}[localStorage.getItem("agora.uiScale")]||1.125;document.documentElement.style.setProperty("--ui-scale",String(z));}catch(e){}try{var l=localStorage.getItem("agora.locale");if(l!=="en"&&l!=="zh"&&l!=="ja"){l="zh";}window.__AGORA_LOCALE__=l;document.documentElement.lang=l==="zh"?"zh-Hans":l;}catch(e){}setTimeout(function(){try{document.documentElement.setAttribute("data-i18n-ready","");}catch(e){}},1500);})();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <style>{`html:not([data-i18n-ready]) body{visibility:hidden}`}</style>
        <script dangerouslySetInnerHTML={{ __html: localeBootstrap }} />
      </head>
      <body className="min-h-full flex flex-col">
        <ThemeProvider>
          <LocaleProvider>{children}</LocaleProvider>
          <TerminalCodeRedeemer />
        </ThemeProvider>
      </body>
    </html>
  );
}
