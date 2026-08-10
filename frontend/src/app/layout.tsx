import type { Metadata } from "next";
import { ThemeProvider } from "@/components/theme-provider";
import { LocaleProvider } from "@/lib/i18n";
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
const localeBootstrap = `(function(){try{var l=localStorage.getItem("agora.locale");if(l!=="en"&&l!=="zh"){l=(navigator.language||"").toLowerCase().indexOf("zh")===0?"zh":"en";}window.__AGORA_LOCALE__=l;document.documentElement.lang=l==="zh"?"zh-Hans":"en";}catch(e){}setTimeout(function(){try{document.documentElement.setAttribute("data-i18n-ready","");}catch(e){}},1500);})();`;

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
        </ThemeProvider>
      </body>
    </html>
  );
}
