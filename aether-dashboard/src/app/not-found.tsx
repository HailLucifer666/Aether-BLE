export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4 text-center text-slate-100">
      <div>
        <p className="text-5xl font-bold text-cyan-400">404</p>
        <p className="mt-3 text-sm text-slate-400">
          This page walked out of BLE range.
        </p>
      </div>
    </main>
  );
}
