"use client";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4 text-center text-slate-100">
      <div className="max-w-md">
        <p className="text-2xl font-bold text-rose-400">Something broke</p>
        <p className="mt-3 text-sm text-slate-400">
          {error.message || "An unexpected error occurred."}
        </p>
        <button
          onClick={reset}
          className="mt-5 rounded-lg border border-cyan-500/60 bg-cyan-500/10 px-4 py-2 text-sm font-semibold text-cyan-300 transition-colors hover:bg-cyan-500/20"
        >
          Try again
        </button>
      </div>
    </main>
  );
}
