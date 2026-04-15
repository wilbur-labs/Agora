"use client";

interface WelcomeProps {
  onExample: (text: string) => void;
}

const EXAMPLES = [
  "Design a caching strategy for a high-traffic Go API",
  "Review my Docker Compose setup for security issues",
  "Compare PostgreSQL vs MySQL for a multi-tenant SaaS",
];

export function Welcome({ onExample }: WelcomeProps) {
  return (
    <div className="flex-1 flex items-center justify-center p-6">
      <div className="max-w-md text-center space-y-6">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">🏛 Welcome to Agora</h2>
          <p className="text-muted-foreground text-sm mt-2 leading-relaxed">
            Ask a question and multiple AI perspectives will discuss it together.
          </p>
        </div>
        <div className="space-y-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => onExample(ex)}
              className="w-full text-left text-sm px-4 py-3 rounded-xl border border-border bg-card hover:border-primary/50 hover:bg-accent transition-colors"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
